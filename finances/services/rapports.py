"""
Agrégations pour le tableau de bord et les rapports.

Tous les montants sont convertis dans la devise principale de l'utilisateur (TauxChange).
Un taux manquant n'interrompt pas le calcul : le montant est conservé tel quel et un
avertissement est remonté dans le résultat.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Case, DecimalField, F, Sum, When
from django.db.models.functions import ExtractMonth, ExtractYear
from django.utils import timezone

from core.models import TauxChange

from ..models import (
    ZERO,
    Account,
    CoursHistorique,
    Holding,
    InvestmentTransaction,
    Transaction,
    TypeActif,
    TypeCompte,
    TypeFlux,
    TypeOperationTitre,
    TypeTransaction,
    arrondir,
    rejouer_operations,
)
from .periodes import (
    MOIS_COURTS,
    decaler_mois,
    fin_de_mois,
    fins_de_mois,
    libelle_mois,
    meme_jour_annee_precedente,
)

COULEUR_NEUTRE = "#94A3B8"
# Emplacements 1 à 4 de la palette catégorielle validée (ordre fixe : la couleur suit le type).
COULEURS_TYPE_COMPTE = {
    TypeCompte.COURANT: "#2A78D6",
    TypeCompte.EPARGNE: "#EB6834",
    TypeCompte.TITRE: "#1BAF7A",
    TypeCompte.AUTRE: "#EDA100",
}
DECIMAL = DecimalField(max_digits=20, decimal_places=2)


def devise_principale(user) -> str:
    profil = getattr(user, "profile", None)
    return profil.devise_principale if profil else "EUR"


def pourcentage(partie: Decimal, total: Decimal) -> Decimal | None:
    if not total:
        return None
    return (partie / total * 100).quantize(Decimal("0.1"))


class Convertisseur:
    """Conversion vers une devise cible avec cache et collecte des avertissements."""

    def __init__(self, devise_cible: str):
        self.devise_cible = devise_cible
        self.avertissements: set[str] = set()
        self._cache: dict[tuple[str, date], Decimal] = {}

    def taux(self, devise: str, au: date) -> Decimal:
        if devise == self.devise_cible:
            return Decimal("1")
        cle = (devise, au)
        if cle not in self._cache:
            try:
                self._cache[cle] = TauxChange.taux_au(devise, self.devise_cible, au)
            except ValidationError:
                self.avertissements.add(
                    f"Aucun taux {devise} → {self.devise_cible} : les montants en {devise} ne sont pas convertis."
                )
                self._cache[cle] = Decimal("1")
        return self._cache[cle]

    def convertir(self, montant: Decimal, devise: str, au: date | None = None) -> Decimal:
        return arrondir(montant * self.taux(devise, au or timezone.localdate()))


# ===========================================================================
# Patrimoine
# ===========================================================================
@dataclass
class Patrimoine:
    devise: str
    total: Decimal
    comptes: list[dict]
    par_type: list[dict]
    avertissements: list[str] = field(default_factory=list)


def patrimoine(user, au: date | None = None) -> Patrimoine:
    """Valeur nette (comptes inclus dans le patrimoine), actuelle ou à une date passée."""
    devise = devise_principale(user)
    conv = Convertisseur(devise)
    jour = au or timezone.localdate()
    comptes = Account.objects.for_user(user).filter(inclure_patrimoine=True)

    lignes = []
    if au is None:
        for c in comptes.with_soldes():
            # arrondir : SQLite agrège les décimaux en flottants
            lignes.append((c, arrondir(c.solde_especes), arrondir(c.valorisation_titres), arrondir(c.solde_total)))
    else:
        for c in comptes.filter(date_creation__lte=au):
            especes, titres = c.calculer_solde_especes(au), c.calculer_valorisation_titres(au)
            lignes.append((c, especes, titres, especes + titres))

    resultat, par_type = [], defaultdict(Decimal)
    for compte, especes, titres, total in lignes:
        converti = conv.convertir(total, compte.devise, jour)
        par_type[compte.type] += converti
        resultat.append({
            "compte": compte,
            "especes": especes,
            "titres": titres,
            "solde": total,
            "solde_converti": converti,
        })
    total = sum((l["solde_converti"] for l in resultat), ZERO)
    repartition = [
        {
            "type": t,
            "libelle": TypeCompte(t).label,
            "couleur": COULEURS_TYPE_COMPTE[t],
            "montant": par_type[t],
            "pct": pourcentage(par_type[t], total),
        }
        for t in TypeCompte.values
        if t in par_type
    ]
    return Patrimoine(devise, total, resultat, repartition, sorted(conv.avertissements))


def historique_patrimoine(user, nb_mois: int = 12, aujourd_hui: date | None = None) -> dict:
    """
    Valeur nette en fin de chaque mois (le dernier point est aujourd'hui).
    Calcul en un nombre fixe de requêtes : flux mensuels cumulés + rejeu des positions titres.
    """
    aujourd_hui = aujourd_hui or timezone.localdate()
    devise = devise_principale(user)
    conv = Convertisseur(devise)
    fins = fins_de_mois(nb_mois, aujourd_hui)
    periodes = [(f.year, f.month) for f in fins]
    comptes = list(Account.objects.for_user(user).filter(inclure_patrimoine=True))
    ids = {c.pk for c in comptes}

    # --- Flux mensuels par compte (toutes dates jusqu'à aujourd'hui) ---
    flux: dict[int, dict[tuple[int, int], Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    tx = Transaction.objects.for_user(user).filter(date__lte=aujourd_hui).annotate(
        an=ExtractYear("date"), mois=ExtractMonth("date")
    )
    signe = Case(When(type=TypeTransaction.REVENU, then=F("montant")), default=-F("montant"), output_field=DECIMAL)
    for r in tx.values("compte_id", "an", "mois").annotate(t=Sum(signe)).order_by():
        flux[r["compte_id"]][(r["an"], r["mois"])] += r["t"]
    entrants = tx.filter(type=TypeTransaction.VIREMENT_INTERNE).values("compte_destination_id", "an", "mois")
    for r in entrants.annotate(t=Sum("montant_destination")).order_by():
        flux[r["compte_destination_id"]][(r["an"], r["mois"])] += r["t"]
    ops_titres = InvestmentTransaction.objects.for_user(user).filter(date__lte=aujourd_hui)
    for r in (
        ops_titres.annotate(an=ExtractYear("date"), mois=ExtractMonth("date"))
        .values("compte_titre_id", "an", "mois")
        .annotate(t=Sum("montant_net"))
        .order_by()
    ):
        flux[r["compte_titre_id"]][(r["an"], r["mois"])] += r["t"]

    especes: dict[int, list[Decimal | None]] = {}
    for c in comptes:
        mois_flux = sorted(flux[c.pk].items())
        solde, i, valeurs = c.solde_initial, 0, []
        for fin, p in zip(fins, periodes):
            while i < len(mois_flux) and mois_flux[i][0] <= p:
                solde += mois_flux[i][1]
                i += 1
            valeurs.append(solde if c.date_creation <= fin else None)
        especes[c.pk] = valeurs

    # --- Valorisation des titres en fin de mois ---
    titres: dict[int, list[Decimal]] = defaultdict(lambda: [ZERO] * len(fins))
    ops_par_position = defaultdict(list)
    for op in ops_titres.order_by("date", "pk"):
        ops_par_position[op.holding_id].append(op)
    cours_par_ticker = defaultdict(list)
    for ticker, jour, cours in CoursHistorique.objects.for_user(user).order_by("date").values_list(
        "ticker_symbole", "date", "cours"
    ):
        cours_par_ticker[ticker].append((jour, cours))

    for h in Holding.objects.for_user(user).filter(compte_titre_id__in=ids):
        ops = ops_par_position[h.pk]
        dates_ops = [o.date for o in ops]
        historique = cours_par_ticker[h.ticker_symbole]
        dates_cours = [d for d, _ in historique]
        for i, fin in enumerate(fins):
            k = bisect_right(dates_ops, fin)
            if not k:
                continue
            etat = rejouer_operations(ops[:k])
            if not etat.quantite:
                continue
            j = bisect_right(dates_cours, fin)
            if j:
                prix = historique[j - 1][1]
            elif h.dernier_cours is not None and h.date_dernier_cours and h.date_dernier_cours <= fin:
                prix = h.dernier_cours
            else:
                prix = etat.pmpa
            titres[h.compte_titre_id][i] += etat.quantite * prix

    # --- Agrégation et conversion ---
    total = [ZERO] * len(fins)
    par_type = defaultdict(lambda: [ZERO] * len(fins))
    series_comptes = []
    for c in comptes:
        valeurs = []
        for i, fin in enumerate(fins):
            if especes[c.pk][i] is None:
                valeurs.append(None)
                continue
            v = conv.convertir(especes[c.pk][i] + titres[c.pk][i], c.devise, fin)
            valeurs.append(v)
            total[i] += v
            par_type[c.type][i] += v
        series_comptes.append({"compte": c, "nom": c.nom, "couleur": c.couleur, "valeurs": valeurs})

    return {
        "devise": devise,
        "dates": fins,
        "libelles": [f"{MOIS_COURTS[f.month - 1]} {f.year}" for f in fins],
        "total": total,
        "par_type": [
            {"type": t, "libelle": TypeCompte(t).label, "couleur": COULEURS_TYPE_COMPTE[t], "valeurs": par_type[t]}
            for t in TypeCompte.values
            if t in par_type
        ],
        "comptes": series_comptes,
        "variation": total[-1] - total[0] if total else ZERO,
        "variation_pct": pourcentage(total[-1] - total[0], total[0]) if total else None,
        "avertissements": sorted(conv.avertissements),
    }


# ===========================================================================
# Flux : revenus, dépenses, épargne
# ===========================================================================
def totaux_convertis(queryset, devise: str, au: date | None = None) -> dict:
    """Revenus, dépenses, solde et taux d'épargne d'un queryset de Transaction (virements exclus)."""
    conv = Convertisseur(devise)
    revenus = depenses = ZERO
    lignes = (
        queryset.hors_virements().values("type", "compte__devise").annotate(t=Sum("montant")).order_by()
    )
    for r in lignes:
        montant = conv.convertir(r["t"], r["compte__devise"], au)
        if r["type"] == TypeTransaction.REVENU:
            revenus += montant
        else:
            depenses += montant
    solde = revenus - depenses
    return {
        "revenus": revenus,
        "depenses": depenses,
        "solde": solde,
        "taux_epargne": pourcentage(solde, revenus),
        "devise": devise,
        "avertissements": sorted(conv.avertissements),
    }


def totaux_periode(user, debut: date | None, fin: date | None) -> dict:
    qs = Transaction.objects.for_user(user).periode(debut, fin)
    return totaux_convertis(qs, devise_principale(user), fin)


def evolution_mensuelle(user, nb_mois: int = 12, aujourd_hui: date | None = None) -> dict:
    """Revenus, dépenses et épargne mois par mois (graphique en barres)."""
    aujourd_hui = aujourd_hui or timezone.localdate()
    devise = devise_principale(user)
    conv = Convertisseur(devise)
    debut = decaler_mois(aujourd_hui, -(nb_mois - 1))
    periodes = [((d := decaler_mois(debut, i)).year, d.month) for i in range(nb_mois)]
    index = {p: i for i, p in enumerate(periodes)}
    revenus, depenses = [ZERO] * nb_mois, [ZERO] * nb_mois
    lignes = (
        Transaction.objects.for_user(user)
        .hors_virements()
        .periode(debut, fin_de_mois(aujourd_hui.year, aujourd_hui.month))
        .annotate(an=ExtractYear("date"), mois=ExtractMonth("date"))
        .values("type", "compte__devise", "an", "mois")
        .annotate(t=Sum("montant"))
        .order_by()
    )
    for r in lignes:
        i = index[(r["an"], r["mois"])]
        montant = conv.convertir(r["t"], r["compte__devise"], fin_de_mois(r["an"], r["mois"]))
        if r["type"] == TypeTransaction.REVENU:
            revenus[i] += montant
        else:
            depenses[i] += montant
    epargne = [r - d for r, d in zip(revenus, depenses)]
    return {
        "devise": devise,
        "libelles": [f"{MOIS_COURTS[m - 1]} {a}" for a, m in periodes],
        "revenus": revenus,
        "depenses": depenses,
        "epargne": epargne,
        "taux_epargne": [pourcentage(e, r) for e, r in zip(epargne, revenus)],
        "maximum": max([*revenus, *depenses, ZERO]),
        "avertissements": sorted(conv.avertissements),
    }


# ===========================================================================
# Répartition par catégorie
# ===========================================================================
def repartition_categories(user, debut: date | None, fin: date | None, type_flux: str = TypeFlux.DEPENSE) -> dict:
    """Montants par catégorie et sous-catégorie (camemberts / donuts), triés par montant décroissant."""
    devise = devise_principale(user)
    conv = Convertisseur(devise)
    lignes = (
        Transaction.objects.for_user(user)
        .filter(type=type_flux)
        .periode(debut, fin)
        .values(
            "categorie_id", "categorie__nom", "categorie__couleur",
            "sous_categorie_id", "sous_categorie__nom", "sous_categorie__couleur", "compte__devise",
        )
        .annotate(t=Sum("montant"))
        .order_by()
    )
    categories: dict = {}
    for r in lignes:
        montant = conv.convertir(r["t"], r["compte__devise"], fin)
        cat = categories.setdefault(
            r["categorie_id"],
            {
                "id": r["categorie_id"],
                "nom": r["categorie__nom"] or "Non catégorisé",
                "couleur": r["categorie__couleur"] or COULEUR_NEUTRE,
                "total": ZERO,
                "sous_categories": {},
            },
        )
        cat["total"] += montant
        sc = cat["sous_categories"].setdefault(
            r["sous_categorie_id"],
            {
                "id": r["sous_categorie_id"],
                "nom": r["sous_categorie__nom"] or "Sans sous-catégorie",
                "couleur": r["sous_categorie__couleur"] or cat["couleur"],
                "total": ZERO,
            },
        )
        sc["total"] += montant

    total = sum((c["total"] for c in categories.values()), ZERO)
    resultat = sorted(categories.values(), key=lambda c: c["total"], reverse=True)
    for cat in resultat:
        cat["pct"] = pourcentage(cat["total"], total)
        sous = sorted(cat["sous_categories"].values(), key=lambda s: s["total"], reverse=True)
        for sc in sous:
            sc["pct"] = pourcentage(sc["total"], cat["total"])
        cat["sous_categories"] = sous
    return {
        "type": type_flux,
        "devise": devise,
        "debut": debut,
        "fin": fin,
        "total": total,
        "categories": resultat,
        "avertissements": sorted(conv.avertissements),
    }


# ===========================================================================
# Comparatifs temporels
# ===========================================================================
def comparatif(user, periode_a: tuple[date, date], periode_b: tuple[date, date], libelle_a: str, libelle_b: str) -> dict:
    """Compare la période A (courante) à la période B (référence), globalement et par catégorie."""
    totaux_a, totaux_b = totaux_periode(user, *periode_a), totaux_periode(user, *periode_b)
    sections = []
    for type_flux, libelle in ((TypeFlux.DEPENSE, "Dépenses"), (TypeFlux.REVENU, "Revenus")):
        rep_a = repartition_categories(user, *periode_a, type_flux)
        rep_b = repartition_categories(user, *periode_b, type_flux)
        cats_b = {c["id"]: c for c in rep_b["categories"]}
        lignes = []
        for cat in rep_a["categories"]:
            ref = cats_b.pop(cat["id"], None)
            lignes.append(_ligne_comparatif(cat["nom"], cat["couleur"], cat["total"], ref["total"] if ref else ZERO))
        for ref in cats_b.values():
            lignes.append(_ligne_comparatif(ref["nom"], ref["couleur"], ZERO, ref["total"]))
        lignes.sort(key=lambda l: max(l["a"], l["b"]), reverse=True)
        sections.append({
            "type": type_flux,
            "libelle": libelle,
            "lignes": lignes,
            "total": _ligne_comparatif(f"Total {libelle.lower()}", COULEUR_NEUTRE, rep_a["total"], rep_b["total"]),
        })
    return {
        "libelle_a": libelle_a,
        "libelle_b": libelle_b,
        "periode_a": periode_a,
        "periode_b": periode_b,
        "totaux_a": totaux_a,
        "totaux_b": totaux_b,
        "solde": _ligne_comparatif("Solde", COULEUR_NEUTRE, totaux_a["solde"], totaux_b["solde"]),
        "sections": sections,
    }


def _ligne_comparatif(nom: str, couleur: str, a: Decimal, b: Decimal) -> dict:
    return {"nom": nom, "couleur": couleur, "a": a, "b": b, "variation": a - b, "variation_pct": pourcentage(a - b, abs(b))}


def comparatif_mois(user, annee: int, mois: int) -> dict:
    """Mois M vs M-1."""
    debut_a = date(annee, mois, 1)
    debut_b = decaler_mois(debut_a, -1)
    return comparatif(
        user,
        (debut_a, fin_de_mois(annee, mois)),
        (debut_b, fin_de_mois(debut_b.year, debut_b.month)),
        libelle_mois(annee, mois).capitalize(),
        libelle_mois(debut_b.year, debut_b.month).capitalize(),
    )


def comparatif_annees(user, annee: int, a_date: bool = True, aujourd_hui: date | None = None) -> dict:
    """Année N vs N-1. Pour l'année en cours, `a_date` compare à période équivalente (1er janvier → aujourd'hui)."""
    aujourd_hui = aujourd_hui or timezone.localdate()
    if a_date and annee == aujourd_hui.year:
        fin_a = aujourd_hui
        fin_b = meme_jour_annee_precedente(aujourd_hui)
        suffixe = f" (au {aujourd_hui:%d/%m})"
    else:
        fin_a, fin_b, suffixe = date(annee, 12, 31), date(annee - 1, 12, 31), ""
    return comparatif(
        user,
        (date(annee, 1, 1), fin_a),
        (date(annee - 1, 1, 1), fin_b),
        f"{annee}{suffixe}",
        f"{annee - 1}{suffixe}",
    )


# ===========================================================================
# Compte titres
# ===========================================================================
def rapport_titres(user, compte: Account | None = None) -> dict:
    """Performance, plus-values latentes et réalisées, dividendes et allocation d'actifs."""
    devise = devise_principale(user)
    conv = Convertisseur(devise)
    comptes = Account.objects.for_user(user).comptes_titres().with_soldes()
    positions = Holding.objects.for_user(user).select_related("compte_titre")
    operations = InvestmentTransaction.objects.for_user(user)
    if compte is not None:
        comptes = comptes.filter(pk=compte.pk)
        positions = positions.filter(compte_titre=compte)
        operations = operations.filter(compte_titre=compte)

    def c(montant, compte_titre):
        return conv.convertir(montant, compte_titre.devise)

    lignes, pv_realisee, dividendes, frais = [], ZERO, ZERO, ZERO
    for p in positions:
        pv_realisee += c(p.plus_value_realisee, p.compte_titre)
        dividendes += c(p.dividendes_percus, p.compte_titre)
        frais += c(p.frais_totaux, p.compte_titre)
        if p.quantite:
            lignes.append({
                "position": p,
                "valeur": c(p.valeur_marche, p.compte_titre),
                "cout": c(p.cout_total, p.compte_titre),
                "pv_latente": c(p.plus_value_latente, p.compte_titre),
                "perf_pct": p.performance_latente_pct,
                "cours_absent": p.dernier_cours is None,
            })
    valeur = sum((l["valeur"] for l in lignes), ZERO)
    cout = sum((l["cout"] for l in lignes), ZERO)
    for l in lignes:
        l["poids"] = pourcentage(l["valeur"], valeur)
    lignes.sort(key=lambda l: l["valeur"], reverse=True)

    liquidites = sum((conv.convertir(cp.solde_especes, cp.devise) for cp in comptes), ZERO)
    investi = ZERO
    for montant, devise_compte in (
        operations.filter(type_operation=TypeOperationTitre.ACHAT)
        .values("compte_titre__devise")
        .annotate(t=Sum("montant_net"))
        .order_by()
        .values_list("t", "compte_titre__devise")
    ):
        investi += conv.convertir(-montant, devise_compte)
    pv_latente = valeur - cout
    performance = pv_latente + pv_realisee + dividendes

    def allocation(cle, libelle):
        groupes = defaultdict(Decimal)
        for l in lignes:
            groupes[cle(l["position"])] += l["valeur"]
        return sorted(
            ({"cle": k, "libelle": libelle(k), "valeur": v, "pct": pourcentage(v, valeur)} for k, v in groupes.items()),
            key=lambda g: g["valeur"],
            reverse=True,
        )

    par_annee = defaultdict(lambda: {"achats": ZERO, "ventes": ZERO, "pv_realisee": ZERO, "dividendes": ZERO})
    for r in (
        operations.annotate(an=ExtractYear("date"))
        .values("an", "type_operation", "compte_titre__devise")
        .annotate(net=Sum("montant_net"), pv=Sum("plus_value_realisee"))
        .order_by()
    ):
        ligne = par_annee[r["an"]]
        net = conv.convertir(r["net"], r["compte_titre__devise"])
        if r["type_operation"] == TypeOperationTitre.ACHAT:
            ligne["achats"] += -net
        elif r["type_operation"] == TypeOperationTitre.VENTE:
            ligne["ventes"] += net
            ligne["pv_realisee"] += conv.convertir(r["pv"] or ZERO, r["compte_titre__devise"])
        else:
            ligne["dividendes"] += net

    return {
        "devise": devise,
        "comptes": list(comptes),
        "lignes": lignes,
        "valeur": valeur,
        "cout": cout,
        "liquidites": liquidites,
        "valeur_totale": valeur + liquidites,
        "investi": investi,
        "pv_latente": pv_latente,
        "pv_latente_pct": pourcentage(pv_latente, cout),
        "pv_realisee": pv_realisee,
        "dividendes": dividendes,
        "frais": frais,
        "performance": performance,
        "performance_pct": pourcentage(performance, investi),
        "allocation_types": allocation(lambda p: p.type_actif, lambda k: TypeActif(k).label),
        "allocation_secteurs": allocation(lambda p: p.secteur or "", lambda k: k or "Non renseigné"),
        "par_annee": [{"annee": a, **v} for a, v in sorted(par_annee.items(), reverse=True)],
        "cours_manquants": [l["position"] for l in lignes if l["cours_absent"]],
        "avertissements": sorted(conv.avertissements),
    }
