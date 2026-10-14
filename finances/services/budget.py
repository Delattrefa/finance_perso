"""
Comparatif budgétaire Réel vs Prévisionnel.

Une matrice est construite pour un plan et une vue :
- mensuelle    : les 12 mois d'une année + total annuel ;
- annuelle     : synthèse d'une année par trimestre, cumul à date et année complète ;
- quinquennale : une colonne par année du plan + total.

Affectation du réel aux lignes budgétaires (sans double comptage) :
1. une opération dont la sous-catégorie a sa propre ligne lui est affectée ;
2. sinon, elle va à la ligne « catégorie entière » si elle existe ;
3. sinon, elle apparaît dans une ligne « Hors budget » de sa catégorie.
Les virements internes sont exclus.

Projection (« atterrissage ») d'une colonne : réel des mois écoulés + prévu des mois à venir ;
le mois en cours compte pour le maximum du réel et du prévu.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import ExtractMonth, ExtractYear
from django.utils import timezone

from ..models import ZERO, Budget, BudgetAllocation, BudgetPlan, Category, Transaction, TypeFlux
from .periodes import MOIS_COURTS

Periode = tuple[int, int]  # (année, mois)

VUES = {"mensuelle": "Mensuelle", "annuelle": "Annuelle", "quinquennale": "Sur la durée du plan"}
COULEUR_NEUTRE = "#94A3B8"


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Colonne:
    cle: str
    libelle: str
    periodes: tuple[Periode, ...]
    est_total: bool = False

    @property
    def est_mois(self) -> bool:
        return len(self.periodes) == 1


@dataclass
class Cellule:
    prevu: Decimal
    reel: Decimal
    projection: Decimal
    type_flux: str
    seuil: Decimal
    terminee: bool  # toutes les périodes de la colonne sont écoulées
    commencee: bool  # au moins une période est entamée
    allocation_id: int | None = None
    saisi_manuellement: bool = False

    @property
    def ecart(self) -> Decimal:
        """Réel − prévu (pour une dépense, positif = dépassement)."""
        return self.reel - self.prevu

    @property
    def ecart_favorable(self) -> bool:
        return self.reel <= self.prevu if self.type_flux == TypeFlux.DEPENSE else self.reel >= self.prevu

    @property
    def taux(self) -> Decimal | None:
        """Consommation du budget en %."""
        if not self.prevu:
            return None
        return (self.reel / self.prevu * 100).quantize(Decimal("0.1"))

    @property
    def statut(self) -> str:
        """vide | a_venir | ok | alerte | depassement | insuffisant"""
        if not self.prevu and not self.reel:
            return "vide"
        if not self.commencee:
            return "a_venir"
        if self.type_flux == TypeFlux.DEPENSE:
            if self.reel > self.prevu:
                return "depassement"
            if self.taux is not None and self.taux >= self.seuil:
                return "alerte"
            return "ok"
        if self.terminee and self.reel < self.prevu:
            return "insuffisant"
        return "ok"


@dataclass
class LigneMatrice:
    libelle: str
    couleur: str
    type_flux: str
    budget: Budget | None = None
    hors_budget: bool = False
    prevu: dict[Periode, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    reel: dict[Periode, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    allocations: dict[Periode, tuple[int, bool]] = field(default_factory=dict)
    cellules: list[Cellule] = field(default_factory=list)

    @classmethod
    def somme(cls, libelle: str, type_flux: str, lignes, couleur: str = COULEUR_NEUTRE) -> LigneMatrice:
        total = cls(libelle=libelle, couleur=couleur, type_flux=type_flux)
        for ligne in lignes:
            for p, v in ligne.prevu.items():
                total.prevu[p] += v
            for p, v in ligne.reel.items():
                total.reel[p] += v
        return total

    @property
    def total(self) -> Cellule | None:
        return self.cellules[-1] if self.cellules else None


@dataclass
class GroupeCategorie:
    categorie: Category | None
    lignes: list[LigneMatrice]
    total: LigneMatrice

    @property
    def detaille(self) -> bool:
        """Faux si le groupe se résume à une ligne « catégorie entière »."""
        premiere = self.lignes[0]
        return len(self.lignes) > 1 or bool(premiere.budget and premiere.budget.sous_categorie_id)


@dataclass
class Section:
    type_flux: str
    libelle: str
    groupes: list[GroupeCategorie]
    total: LigneMatrice


@dataclass
class MatriceBudget:
    plan: BudgetPlan
    vue: str
    annee: int
    colonnes: list[Colonne]
    sections: list[Section]
    solde: LigneMatrice
    aujourd_hui: date

    @property
    def depenses(self) -> Section:
        return self.sections[0]

    @property
    def revenus(self) -> Section:
        return self.sections[1]

    @property
    def toutes_les_lignes(self):
        for section in self.sections:
            for groupe in section.groupes:
                yield from groupe.lignes

    def alertes(self, colonne: int = -1) -> list[tuple[LigneMatrice, Cellule]]:
        """Lignes en dépassement ou en alerte pour une colonne, les plus gros écarts d'abord."""
        resultat = [
            (ligne, ligne.cellules[colonne])
            for ligne in self.toutes_les_lignes
            if not ligne.hors_budget and ligne.cellules[colonne].statut in ("depassement", "alerte")
        ]
        return sorted(resultat, key=lambda lc: lc[1].ecart, reverse=True)


# ---------------------------------------------------------------------------
# Colonnes
# ---------------------------------------------------------------------------
def _mois(annee: int, mois: range | list[int]) -> tuple[Periode, ...]:
    return tuple((annee, m) for m in mois)


def construire_colonnes(plan: BudgetPlan, vue: str, annee: int, aujourd_hui: date) -> list[Colonne]:
    if vue == "quinquennale":
        colonnes = [Colonne(str(a), str(a), _mois(a, range(1, 13))) for a in plan.annees]
        toutes = tuple(p for c in colonnes for p in c.periodes)
        return [*colonnes, Colonne("total", f"Total {plan.duree_annees} ans", toutes, est_total=True)]

    annee_complete = _mois(annee, range(1, 13))
    if vue == "annuelle":
        colonnes = [
            Colonne(f"T{t}", f"T{t}", _mois(annee, range(3 * t - 2, 3 * t + 1))) for t in range(1, 5)
        ]
        if annee < aujourd_hui.year:
            colonnes.append(Colonne("cumul", "Cumul à date", annee_complete))
        elif annee == aujourd_hui.year:
            colonnes.append(Colonne("cumul", "Cumul à date", _mois(annee, range(1, aujourd_hui.month + 1))))
        return [*colonnes, Colonne("total", f"Année {annee}", annee_complete, est_total=True)]

    colonnes = [Colonne(f"{annee}-{m:02d}", MOIS_COURTS[m - 1], ((annee, m),)) for m in range(1, 13)]
    return [*colonnes, Colonne("total", f"Total {annee}", annee_complete, est_total=True)]


def _cellule(ligne: LigneMatrice, colonne: Colonne, courant: Periode, seuil: Decimal) -> Cellule:
    prevu = reel = projection = ZERO
    for p in colonne.periodes:
        pv, re = ligne.prevu.get(p, ZERO), ligne.reel.get(p, ZERO)
        prevu += pv
        reel += re
        projection += re if p < courant else max(re, pv) if p == courant else pv
    allocation_id, manuel = (None, False)
    if colonne.est_mois and not colonne.est_total:
        allocation_id, manuel = ligne.allocations.get(colonne.periodes[0], (None, False))
    return Cellule(
        prevu=prevu,
        reel=reel,
        projection=projection,
        type_flux=ligne.type_flux,
        seuil=seuil,
        terminee=all(p < courant for p in colonne.periodes),
        commencee=any(p <= courant for p in colonne.periodes),
        allocation_id=allocation_id,
        saisi_manuellement=manuel,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
def annee_par_defaut(plan: BudgetPlan, aujourd_hui: date | None = None) -> int:
    aujourd_hui = aujourd_hui or timezone.localdate()
    return aujourd_hui.year if plan.couvre(aujourd_hui.year) else plan.annee_debut


def construire_matrice(
    plan: BudgetPlan,
    vue: str = "mensuelle",
    annee: int | None = None,
    seuil: Decimal = Decimal("90"),
    aujourd_hui: date | None = None,
) -> MatriceBudget:
    aujourd_hui = aujourd_hui or timezone.localdate()
    vue = vue if vue in VUES else "mensuelle"
    if annee is None or not plan.couvre(annee):
        annee = annee_par_defaut(plan, aujourd_hui)
    courant: Periode = (aujourd_hui.year, aujourd_hui.month)

    colonnes = construire_colonnes(plan, vue, annee, aujourd_hui)
    annees = sorted({a for c in colonnes for a, _ in c.periodes})

    # --- Lignes budgétaires et prévu ---
    budgets = list(plan.lignes.select_related("categorie", "sous_categorie"))
    lignes: dict[int, LigneMatrice] = {}
    for b in budgets:
        detail = b.sous_categorie
        lignes[b.pk] = LigneMatrice(
            libelle=detail.nom if detail else b.categorie.nom,
            couleur=detail.couleur_effective if detail else b.categorie.couleur,
            type_flux=b.categorie.type,
            budget=b,
        )
    for a in BudgetAllocation.objects.filter(budget__plan=plan, annee__in=annees).values_list(
        "id", "budget_id", "annee", "mois", "montant", "saisi_manuellement"
    ):
        pk, budget_id, an, mois, montant, manuel = a
        ligne = lignes[budget_id]
        ligne.prevu[(an, mois)] += montant
        ligne.allocations[(an, mois)] = (pk, manuel)

    # --- Réel ---
    par_sous_categorie = {b.sous_categorie_id: lignes[b.pk] for b in budgets if b.sous_categorie_id}
    par_categorie = {b.categorie_id: lignes[b.pk] for b in budgets if not b.sous_categorie_id}
    hors_budget: dict[tuple[str, int | None], LigneMatrice] = {}

    reel = (
        Transaction.objects.for_user(plan.user)
        .hors_virements()
        .filter(date__gte=date(annees[0], 1, 1), date__lte=date(annees[-1], 12, 31))
        .annotate(an=ExtractYear("date"), mois=ExtractMonth("date"))
        .values("type", "categorie_id", "sous_categorie_id", "an", "mois")
        .annotate(total=Sum("montant"))
        .order_by()
    )
    for r in reel:
        cible = par_sous_categorie.get(r["sous_categorie_id"]) or par_categorie.get(r["categorie_id"])
        if cible is None:
            cle = (r["type"], r["categorie_id"])
            if cle not in hors_budget:
                hors_budget[cle] = LigneMatrice(
                    libelle="Hors budget", couleur=COULEUR_NEUTRE, type_flux=r["type"], hors_budget=True
                )
            cible = hors_budget[cle]
        cible.reel[(r["an"], r["mois"])] += r["total"]

    # --- Regroupement par catégorie ---
    ids_categories = {b.categorie_id for b in budgets} | {cat for _, cat in hors_budget if cat}
    categories = Category.objects.in_bulk(ids_categories)
    lignes_par_categorie: dict[tuple[str, int | None], list[LigneMatrice]] = defaultdict(list)
    for b in budgets:
        lignes_par_categorie[(b.categorie.type, b.categorie_id)].append(lignes[b.pk])
    for cle, ligne in hors_budget.items():
        lignes_par_categorie[cle].append(ligne)

    def cle_tri(item):
        (_, cat_id), _ = item
        cat = categories.get(cat_id)
        return (cat is None, cat.ordre if cat else 0, cat.nom.lower() if cat else "")

    sections = []
    for type_flux, libelle in ((TypeFlux.DEPENSE, "Dépenses"), (TypeFlux.REVENU, "Revenus")):
        groupes = []
        for (t, cat_id), lignes_cat in sorted(
            ((k, v) for k, v in lignes_par_categorie.items() if k[0] == type_flux), key=cle_tri
        ):
            categorie = categories.get(cat_id)
            nom = categorie.nom if categorie else "Non catégorisé"
            # Ligne « catégorie entière » d'abord, sous-catégories ensuite, hors budget en dernier.
            lignes_cat.sort(key=lambda l: (l.hors_budget, bool(l.budget and l.budget.sous_categorie_id), l.libelle))
            if len(lignes_cat) > 1:
                for l in lignes_cat:
                    if l.budget and not l.budget.sous_categorie_id:
                        l.libelle = "Autres / non détaillé"
            elif lignes_cat[0].hors_budget:
                lignes_cat[0].libelle = nom
            couleur = categorie.couleur if categorie else COULEUR_NEUTRE
            groupes.append(
                GroupeCategorie(categorie, lignes_cat, LigneMatrice.somme(nom, type_flux, lignes_cat, couleur))
            )
        total = LigneMatrice.somme(f"Total {libelle.lower()}", type_flux, [g.total for g in groupes])
        sections.append(Section(type_flux, libelle, groupes, total))

    solde = LigneMatrice(libelle="Solde (revenus − dépenses)", couleur=COULEUR_NEUTRE, type_flux=TypeFlux.REVENU)
    depenses, revenus = sections[0].total, sections[1].total
    for source, signe in ((revenus, 1), (depenses, -1)):
        for p, v in source.prevu.items():
            solde.prevu[p] += signe * v
        for p, v in source.reel.items():
            solde.reel[p] += signe * v

    # --- Cellules ---
    toutes = [solde]
    for section in sections:
        toutes.append(section.total)
        for groupe in section.groupes:
            toutes.append(groupe.total)
            toutes.extend(groupe.lignes)
    for ligne in toutes:
        ligne.cellules = [_cellule(ligne, c, courant, seuil) for c in colonnes]

    return MatriceBudget(plan, vue, annee, colonnes, sections, solde, aujourd_hui)


def resume_mois(user, annee: int, mois: int, seuil: Decimal = Decimal("90"), aujourd_hui=None) -> dict | None:
    """Synthèse du plan actif pour un mois (tableau de bord) ; None sans plan actif couvrant l'année."""
    plan = BudgetPlan.objects.for_user(user).filter(actif=True).first()
    if plan is None or not plan.couvre(annee):
        return None
    matrice = construire_matrice(plan, "mensuelle", annee, seuil, aujourd_hui)
    index = mois - 1
    return {
        "plan": plan,
        "depenses": matrice.depenses.total.cellules[index],
        "revenus": matrice.revenus.total.cellules[index],
        "solde": matrice.solde.cellules[index],
        "alertes": matrice.alertes(index)[:5],
    }
