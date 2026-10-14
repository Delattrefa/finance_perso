"""
Modèle de données métier de PyFinance.

Conventions :
- Les montants sont stockés en valeur absolue (> 0) ; le sens est porté par le type
  (DEPENSE / REVENU / VIREMENT_INTERNE, ACHAT / VENTE / DIVIDENDE).
- Chaque modèle principal porte un FK `user` ; la cohérence entre objets liés
  (même propriétaire) est vérifiée dans `clean()`, les vues filtrent via `.for_user()`.
- Les suppressions d'objets référencés par l'historique sont bloquées (RESTRICT) :
  on archive plutôt que de supprimer. La suppression d'un utilisateur reste possible
  car tout son contenu est supprimé en cascade dans la même opération.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db import transaction as db_transaction
from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.models import Devise, UserOwnedModel, UserOwnedQuerySet

ZERO = Decimal("0")
CENT = Decimal("0.01")

# Précisions numériques
MONTANT = {"max_digits": 15, "decimal_places": 2}
QUANTITE = {"max_digits": 20, "decimal_places": 8}  # fractions de titres / crypto
PRIX = {"max_digits": 18, "decimal_places": 6}
TAUX = {"max_digits": 6, "decimal_places": 2}  # pourcentage, ex. 2.35 (%)
DECIMAL_SORTIE = DecimalField(max_digits=24, decimal_places=8)

valider_couleur = RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Couleur hexadécimale attendue (ex. #22C55E).")


def arrondir(valeur: Decimal) -> Decimal:
    return valeur.quantize(CENT, rounding=ROUND_HALF_UP)


def _somme_sous_requete(queryset, champ_groupe: str, expression) -> Coalesce:
    """SUM corrélée (Subquery) utilisable dans .annotate(), 0 si aucune ligne."""
    sous_requete = (
        queryset.order_by()
        .values(champ_groupe)
        .annotate(total=Sum(expression, output_field=DECIMAL_SORTIE))
        .values("total")[:1]
    )
    return Coalesce(Subquery(sous_requete, output_field=DECIMAL_SORTIE), Value(ZERO), output_field=DECIMAL_SORTIE)


# ===========================================================================
# Comptes
# ===========================================================================
class TypeCompte(models.TextChoices):
    COURANT = "COURANT", "Compte courant"
    EPARGNE = "EPARGNE", "Compte épargne"
    TITRE = "TITRE", "Compte titres"
    AUTRE = "AUTRE", "Autre"


class AccountQuerySet(UserOwnedQuerySet):
    def actifs(self):
        return self.filter(archive=False)

    def comptes_titres(self):
        return self.filter(type=TypeCompte.TITRE)

    def with_soldes(self):
        """
        Annote chaque compte en une seule requête :
        - solde_especes : liquidités (solde initial + flux d'opérations + flux titres)
        - valorisation_titres : valeur de marché des positions (PMPA si pas de cours)
        - solde_total : solde_especes + valorisation_titres
        """
        Op = TypeTransaction
        tx = Transaction.objects.all()
        revenus = _somme_sous_requete(tx.filter(compte=OuterRef("pk"), type=Op.REVENU), "compte", "montant")
        depenses = _somme_sous_requete(tx.filter(compte=OuterRef("pk"), type=Op.DEPENSE), "compte", "montant")
        sortants = _somme_sous_requete(tx.filter(compte=OuterRef("pk"), type=Op.VIREMENT_INTERNE), "compte", "montant")
        entrants = _somme_sous_requete(
            tx.filter(compte_destination=OuterRef("pk"), type=Op.VIREMENT_INTERNE),
            "compte_destination",
            "montant_destination",
        )
        flux_titres = _somme_sous_requete(
            InvestmentTransaction.objects.filter(compte_titre=OuterRef("pk")), "compte_titre", "montant_net"
        )
        valeur_positions = _somme_sous_requete(
            Holding.objects.filter(compte_titre=OuterRef("pk")),
            "compte_titre",
            ExpressionWrapper(F("quantite") * Coalesce(F("dernier_cours"), F("pmpa")), output_field=DECIMAL_SORTIE),
        )
        return self.annotate(
            solde_especes=F("solde_initial") + revenus - depenses - sortants + entrants + flux_titres,
            valorisation_titres=valeur_positions,
        ).annotate(solde_total=F("solde_especes") + F("valorisation_titres"))


class Account(UserOwnedModel):
    nom = models.CharField("nom", max_length=100)
    type = models.CharField("type", max_length=10, choices=TypeCompte.choices, default=TypeCompte.COURANT)
    solde_initial = models.DecimalField("solde initial", default=ZERO, **MONTANT)
    devise = models.CharField("devise", max_length=3, choices=Devise.choices, default=Devise.EUR)
    date_creation = models.DateField("date d'ouverture", default=timezone.localdate)
    institution = models.CharField("établissement", max_length=100, blank=True)
    numero = models.CharField("numéro / IBAN", max_length=34, blank=True)
    couleur = models.CharField("couleur", max_length=7, default="#3B82F6", validators=[valider_couleur])
    icone = models.CharField("icône", max_length=50, blank=True, help_text="Nom d'icône (ex. Lucide) ou emoji.")
    inclure_patrimoine = models.BooleanField("inclure dans le patrimoine", default=True)
    archive = models.BooleanField("archivé", default=False)
    ordre = models.PositiveSmallIntegerField("ordre d'affichage", default=0)
    notes = models.TextField("notes", blank=True)

    objects = AccountQuerySet.as_manager()

    class Meta:
        verbose_name = "compte"
        verbose_name_plural = "comptes"
        ordering = ["ordre", "nom"]
        constraints = [models.UniqueConstraint(
                fields=["user", "nom"],
                name="compte_nom_unique_par_user",
                violation_error_message="Vous avez déjà un compte portant ce nom.",
            )]
        indexes = [models.Index(fields=["user", "type", "archive"])]

    def __str__(self) -> str:
        return self.nom

    @property
    def est_compte_titre(self) -> bool:
        return self.type == TypeCompte.TITRE

    def calculer_solde_especes(self, au: date | None = None) -> Decimal:
        """Liquidités du compte à une date donnée (incluse)."""
        Op = TypeTransaction
        tx = Transaction.objects.filter(Q(compte=self) | Q(compte_destination=self))
        if au:
            tx = tx.filter(date__lte=au)
        agg = tx.aggregate(
            revenus=Sum("montant", filter=Q(compte=self, type=Op.REVENU)),
            depenses=Sum("montant", filter=Q(compte=self, type=Op.DEPENSE)),
            sortants=Sum("montant", filter=Q(compte=self, type=Op.VIREMENT_INTERNE)),
            entrants=Sum("montant_destination", filter=Q(compte_destination=self, type=Op.VIREMENT_INTERNE)),
        )
        solde = (
            self.solde_initial
            + (agg["revenus"] or ZERO)
            - (agg["depenses"] or ZERO)
            - (agg["sortants"] or ZERO)
            + (agg["entrants"] or ZERO)
        )
        if self.est_compte_titre:
            ops = self.operations_titres.all()
            if au:
                ops = ops.filter(date__lte=au)
            solde += ops.aggregate(t=Sum("montant_net"))["t"] or ZERO
        return solde

    def calculer_valorisation_titres(self, au: date | None = None) -> Decimal:
        """Valeur de marché des positions (cours historique si `au`, sinon dernier cours)."""
        if not self.est_compte_titre:
            return ZERO
        total = ZERO
        for position in self.positions.all():
            if au is None:
                total += position.valeur_marche
            else:
                etat = position.etat_au(au)
                if etat.quantite:
                    cours = position.cours_au(au) or etat.pmpa
                    total += etat.quantite * cours
        return arrondir(total)

    def calculer_solde(self, au: date | None = None) -> Decimal:
        return self.calculer_solde_especes(au) + self.calculer_valorisation_titres(au)


# ===========================================================================
# Catégories
# ===========================================================================
class TypeFlux(models.TextChoices):
    DEPENSE = "DEPENSE", "Dépense"
    REVENU = "REVENU", "Revenu"


class Category(UserOwnedModel):
    nom = models.CharField("nom", max_length=80)
    type = models.CharField("type", max_length=7, choices=TypeFlux.choices, default=TypeFlux.DEPENSE)
    icone = models.CharField("icône", max_length=50, blank=True)
    couleur = models.CharField("couleur", max_length=7, default="#64748B", validators=[valider_couleur])
    ordre = models.PositiveSmallIntegerField("ordre", default=0)
    archive = models.BooleanField("archivée", default=False)

    class Meta:
        verbose_name = "catégorie"
        verbose_name_plural = "catégories"
        ordering = ["type", "ordre", "nom"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "type", "nom"],
                name="categorie_unique_par_user",
                violation_error_message="Cette catégorie existe déjà.",
            ),
        ]

    def __str__(self) -> str:
        return self.nom


class SubCategory(UserOwnedModel):
    categorie = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="sous_categories", verbose_name="catégorie"
    )
    nom = models.CharField("nom", max_length=80)
    icone = models.CharField("icône", max_length=50, blank=True)
    couleur = models.CharField(
        "couleur", max_length=7, blank=True, validators=[valider_couleur], help_text="Vide : couleur de la catégorie."
    )
    ordre = models.PositiveSmallIntegerField("ordre", default=0)
    archive = models.BooleanField("archivée", default=False)

    proprietaire_via = "categorie"

    class Meta:
        verbose_name = "sous-catégorie"
        verbose_name_plural = "sous-catégories"
        ordering = ["categorie__ordre", "categorie__nom", "ordre", "nom"]
        constraints = [models.UniqueConstraint(
                fields=["categorie", "nom"],
                name="sous_categorie_unique",
                violation_error_message="Cette sous-catégorie existe déjà dans la catégorie.",
            )]

    def __str__(self) -> str:
        return f"{self.categorie.nom} › {self.nom}"

    @property
    def type(self) -> str:
        return self.categorie.type

    @property
    def couleur_effective(self) -> str:
        return self.couleur or self.categorie.couleur

    def clean(self):
        if self.categorie_id:
            erreurs = self.verifier_proprietaire(categorie=self.categorie)
            if erreurs:
                raise ValidationError(erreurs)


# ===========================================================================
# Opérations (transactions)
# ===========================================================================
class TypeTransaction(models.TextChoices):
    DEPENSE = "DEPENSE", "Dépense"
    REVENU = "REVENU", "Revenu"
    VIREMENT_INTERNE = "VIREMENT_INTERNE", "Virement interne"


class StatutTransaction(models.TextChoices):
    RAPPROCHE = "RAPPROCHE", "Rapproché"
    EN_ATTENTE = "EN_ATTENTE", "En attente"


class TransactionQuerySet(UserOwnedQuerySet):
    def depenses(self):
        return self.filter(type=TypeTransaction.DEPENSE)

    def revenus(self):
        return self.filter(type=TypeTransaction.REVENU)

    def hors_virements(self):
        return self.exclude(type=TypeTransaction.VIREMENT_INTERNE)

    def periode(self, debut: date | None = None, fin: date | None = None):
        qs = self
        if debut:
            qs = qs.filter(date__gte=debut)
        if fin:
            qs = qs.filter(date__lte=fin)
        return qs

    def du_mois(self, annee: int, mois: int):
        return self.filter(date__year=annee, date__month=mois)

    def pour_compte(self, compte):
        return self.filter(Q(compte=compte) | Q(compte_destination=compte))

    def totaux(self) -> dict[str, Decimal]:
        """Revenus, dépenses et solde net (virements internes exclus)."""
        agg = self.aggregate(
            revenus=Sum("montant", filter=Q(type=TypeTransaction.REVENU)),
            depenses=Sum("montant", filter=Q(type=TypeTransaction.DEPENSE)),
        )
        revenus, depenses = agg["revenus"] or ZERO, agg["depenses"] or ZERO
        return {"revenus": revenus, "depenses": depenses, "solde": revenus - depenses}


class Transaction(UserOwnedModel):
    compte = models.ForeignKey(
        Account, on_delete=models.RESTRICT, related_name="transactions", verbose_name="compte"
    )
    type = models.CharField("type", max_length=16, choices=TypeTransaction.choices, default=TypeTransaction.DEPENSE)
    montant = models.DecimalField(
        "montant", validators=[MinValueValidator(CENT)], help_text="Toujours positif ; le sens dépend du type.", **MONTANT
    )
    date = models.DateField("date", default=timezone.localdate)
    categorie = models.ForeignKey(
        Category, on_delete=models.RESTRICT, null=True, blank=True, related_name="transactions", verbose_name="catégorie"
    )
    sous_categorie = models.ForeignKey(
        SubCategory,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name="sous-catégorie",
    )
    tiers_payeur = models.CharField("tiers", max_length=150, blank=True)
    description = models.CharField("description", max_length=255, blank=True)
    notes = models.TextField("notes", blank=True)
    statut = models.CharField(
        "statut", max_length=10, choices=StatutTransaction.choices, default=StatutTransaction.EN_ATTENTE
    )

    # --- Virements internes uniquement ---
    compte_destination = models.ForeignKey(
        Account,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="virements_entrants",
        verbose_name="compte de destination",
    )
    montant_destination = models.DecimalField(
        "montant crédité",
        null=True,
        blank=True,
        validators=[MinValueValidator(CENT)],
        help_text="Montant reçu sur le compte de destination (utile si les devises diffèrent).",
        **MONTANT,
    )

    objects = TransactionQuerySet.as_manager()
    proprietaire_via = "compte"

    class Meta:
        verbose_name = "opération"
        verbose_name_plural = "opérations"
        ordering = ["-date", "-pk"]
        indexes = [
            models.Index(fields=["user", "date"]),
            models.Index(fields=["user", "type", "date"]),
            models.Index(fields=["user", "categorie", "date"]),
            models.Index(fields=["compte", "date"]),
            models.Index(fields=["compte_destination", "date"]),
            models.Index(fields=["user", "tiers_payeur"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(montant__gt=0), name="transaction_montant_positif"),
            models.CheckConstraint(
                condition=Q(montant_destination__isnull=True) | Q(montant_destination__gt=0),
                name="transaction_montant_destination_positif",
            ),
            models.CheckConstraint(
                condition=(
                    Q(type=TypeTransaction.VIREMENT_INTERNE, compte_destination__isnull=False, categorie__isnull=True)
                    | (~Q(type=TypeTransaction.VIREMENT_INTERNE) & Q(compte_destination__isnull=True))
                ),
                name="transaction_coherence_virement",
            ),
            models.CheckConstraint(
                condition=~Q(compte=F("compte_destination")), name="transaction_virement_comptes_distincts"
            ),
        ]

    def __str__(self) -> str:
        libelle = self.tiers_payeur or self.description or self.get_type_display()
        return f"{self.date:%d/%m/%Y} · {libelle} · {self.montant}"

    @property
    def est_virement(self) -> bool:
        return self.type == TypeTransaction.VIREMENT_INTERNE

    @property
    def couleur(self) -> str:
        """Couleur de la sous-catégorie, sinon de la catégorie (gris si non catégorisée)."""
        if self.sous_categorie_id:
            return self.sous_categorie.couleur_effective
        return self.categorie.couleur if self.categorie_id else "#94A3B8"

    def montant_signe(self, compte: Account | None = None) -> Decimal:
        """Impact sur le solde de `compte` (par défaut le compte principal de l'opération)."""
        compte_id = compte.pk if compte else self.compte_id
        if self.est_virement and compte_id == self.compte_destination_id:
            return self.montant_destination or self.montant
        if self.type == TypeTransaction.REVENU:
            return self.montant
        return -self.montant

    def clean(self):
        erreurs = self.verifier_proprietaire(
            compte=self.compte if self.compte_id else None,
            compte_destination=self.compte_destination if self.compte_destination_id else None,
            categorie=self.categorie if self.categorie_id else None,
            sous_categorie=self.sous_categorie if self.sous_categorie_id else None,
        )

        if self.sous_categorie_id:
            if not self.categorie_id:
                self.categorie = self.sous_categorie.categorie
            elif self.sous_categorie.categorie_id != self.categorie_id:
                erreurs["sous_categorie"] = "Cette sous-catégorie n'appartient pas à la catégorie choisie."

        if self.est_virement:
            if not self.compte_destination_id:
                erreurs["compte_destination"] = "Un virement interne nécessite un compte de destination."
            elif self.compte_destination_id == self.compte_id:
                erreurs["compte_destination"] = "Les comptes source et destination doivent être différents."
            elif (
                self.compte_id
                and self.compte.devise != self.compte_destination.devise
                and not self.montant_destination
            ):
                erreurs["montant_destination"] = "Devises différentes : indiquez le montant crédité."
            if self.categorie_id or self.sous_categorie_id:
                erreurs["categorie"] = "Un virement interne n'est pas catégorisé."
        else:
            if self.compte_destination_id:
                erreurs["compte_destination"] = "Seuls les virements internes ont un compte de destination."
            if self.categorie_id and self.categorie.type != self.type:
                erreurs["categorie"] = f"Catégorie de type « {self.categorie.get_type_display()} » incompatible."

        if erreurs:
            raise ValidationError(erreurs)

    def save(self, *args, **kwargs):
        if self.sous_categorie_id and not self.categorie_id:
            self.categorie_id = self.sous_categorie.categorie_id
        if self.est_virement:
            self.categorie = self.sous_categorie = None
            if self.montant_destination is None:
                self.montant_destination = self.montant
        else:
            self.compte_destination = None
            self.montant_destination = None
        super().save(*args, **kwargs)


# ===========================================================================
# Compte titres : positions, opérations, cours
# ===========================================================================
class TypeActif(models.TextChoices):
    ACTION = "ACTION", "Action"
    ETF = "ETF", "ETF / Tracker"
    OBLIGATION = "OBLIGATION", "Obligation"
    FONDS = "FONDS", "Fonds / SICAV"
    CRYPTO = "CRYPTO", "Crypto-actif"
    AUTRE = "AUTRE", "Autre"


class TypeOperationTitre(models.TextChoices):
    ACHAT = "ACHAT", "Achat"
    VENTE = "VENTE", "Vente"
    DIVIDENDE = "DIVIDENDE", "Dividende"


@dataclass
class EtatPosition:
    """Résultat du rejeu chronologique des opérations d'une position."""

    quantite: Decimal = ZERO
    cout_total: Decimal = ZERO  # coût d'acquisition des titres encore détenus (frais inclus)
    plus_value_realisee: Decimal = ZERO
    dividendes: Decimal = ZERO
    frais: Decimal = ZERO
    pv_par_operation: list[Decimal | None] = field(default_factory=list)

    @property
    def pmpa(self) -> Decimal:
        return self.cout_total / self.quantite if self.quantite else ZERO


def rejouer_operations(operations) -> EtatPosition:
    """
    Rejoue des opérations triées chronologiquement et calcule le PMPA.

    - ACHAT : le coût (quantité × prix + frais) s'ajoute au coût total ; PMPA = coût total / quantité.
    - VENTE : le coût sortant vaut quantité vendue × PMPA ; PV réalisée = produit net − coût sortant.
              Le PMPA des titres restants est inchangé.
    - DIVIDENDE : montant net (quantité × dividende unitaire − retenues) ; sans effet sur le PMPA.
    """
    etat = EtatPosition()
    for op in operations:
        brut = op.quantite * op.prix_unitaire
        frais = op.frais or ZERO
        etat.frais += frais
        pv = None
        if op.type_operation == TypeOperationTitre.ACHAT:
            etat.cout_total += brut + frais
            etat.quantite += op.quantite
        elif op.type_operation == TypeOperationTitre.VENTE:
            if op.quantite > etat.quantite:
                raise ValidationError(
                    {
                        "quantite": f"Vente de {op.quantite.normalize()} titre(s) le {op.date:%d/%m/%Y} : "
                        f"seulement {etat.quantite.normalize()} détenu(s) à cette date."
                    }
                )
            # Prorata du coût (équivaut à quantité × PMPA, sans dérive d'arrondi en cas de vente totale)
            cout_sortant = etat.cout_total * op.quantite / etat.quantite
            pv = arrondir(brut - frais - cout_sortant)
            etat.plus_value_realisee += pv
            etat.cout_total -= cout_sortant
            etat.quantite -= op.quantite
        else:
            etat.dividendes += brut - frais
        etat.pv_par_operation.append(pv)
    return etat


def _cle_chronologique(op):
    # Une opération pas encore enregistrée est placée après celles du même jour.
    return (op.date, op.pk if op.pk is not None else float("inf"))


class Holding(UserOwnedModel):
    """
    Position sur un titre dans un compte titres.
    Les champs « calculés » sont dénormalisés et recalculés à chaque modification
    d'une opération (voir InvestmentTransaction.save / delete).
    """

    compte_titre = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="positions",
        limit_choices_to={"type": TypeCompte.TITRE},
        verbose_name="compte titres",
    )
    ticker_symbole = models.CharField("ticker", max_length=20)
    nom_action = models.CharField("nom du titre", max_length=150)
    isin = models.CharField("ISIN", max_length=12, blank=True)
    type_actif = models.CharField("type d'actif", max_length=10, choices=TypeActif.choices, default=TypeActif.ACTION)
    secteur = models.CharField("secteur", max_length=80, blank=True)

    # --- Champs calculés ---
    quantite = models.DecimalField("quantité détenue", default=ZERO, editable=False, **QUANTITE)
    pmpa = models.DecimalField("PMPA", default=ZERO, editable=False, **PRIX)
    cout_total = models.DecimalField("prix de revient", default=ZERO, editable=False, **MONTANT)
    plus_value_realisee = models.DecimalField("plus-value réalisée", default=ZERO, editable=False, **MONTANT)
    dividendes_percus = models.DecimalField("dividendes perçus", default=ZERO, editable=False, **MONTANT)
    frais_totaux = models.DecimalField("frais cumulés", default=ZERO, editable=False, **MONTANT)

    # --- Cotation ---
    dernier_cours = models.DecimalField("dernier cours", null=True, blank=True, **PRIX)
    date_dernier_cours = models.DateField("date du dernier cours", null=True, blank=True)

    proprietaire_via = "compte_titre"

    class Meta:
        verbose_name = "position"
        verbose_name_plural = "positions"
        ordering = ["compte_titre", "ticker_symbole"]
        constraints = [
            models.UniqueConstraint(
                fields=["compte_titre", "ticker_symbole"],
                name="position_unique_par_compte",
                violation_error_message="Ce titre est déjà présent dans ce compte titres.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.ticker_symbole} · {self.nom_action}"

    # --- Indicateurs ---
    @property
    def est_soldee(self) -> bool:
        return self.quantite == 0

    @property
    def cours_de_reference(self) -> Decimal:
        return self.dernier_cours if self.dernier_cours is not None else self.pmpa

    @property
    def valeur_marche(self) -> Decimal:
        return arrondir(self.quantite * self.cours_de_reference)

    @property
    def plus_value_latente(self) -> Decimal:
        return self.valeur_marche - self.cout_total

    @property
    def performance_latente_pct(self) -> Decimal | None:
        if not self.cout_total:
            return None
        return (self.plus_value_latente / self.cout_total * 100).quantize(CENT)

    @property
    def performance_globale(self) -> Decimal:
        """PV latente + PV réalisée + dividendes nets."""
        return self.plus_value_latente + self.plus_value_realisee + self.dividendes_percus

    # --- Calculs ---
    def operations_chronologiques(self, au: date | None = None):
        ops = self.operations.order_by("date", "pk")
        return ops.filter(date__lte=au) if au else ops

    def etat_au(self, au: date | None = None) -> EtatPosition:
        return rejouer_operations(self.operations_chronologiques(au))

    def cours_au(self, au: date) -> Decimal | None:
        cours = (
            CoursHistorique.objects.filter(user_id=self.user_id, ticker_symbole=self.ticker_symbole, date__lte=au)
            .order_by("-date")
            .values_list("cours", flat=True)
            .first()
        )
        if cours is None and self.date_dernier_cours and self.date_dernier_cours <= au:
            cours = self.dernier_cours
        return cours

    @db_transaction.atomic
    def recalculer(self) -> EtatPosition:
        """Recalcule les champs dénormalisés et la PV réalisée de chaque vente."""
        operations = list(self.operations_chronologiques())
        etat = rejouer_operations(operations)

        a_mettre_a_jour = []
        for op, pv in zip(operations, etat.pv_par_operation):
            if op.plus_value_realisee != pv:
                op.plus_value_realisee = pv
                a_mettre_a_jour.append(op)
        if a_mettre_a_jour:
            InvestmentTransaction.objects.bulk_update(a_mettre_a_jour, ["plus_value_realisee"])

        self.quantite = etat.quantite
        self.pmpa = etat.pmpa.quantize(Decimal("0.000001"))
        self.cout_total = arrondir(etat.cout_total)
        self.plus_value_realisee = arrondir(etat.plus_value_realisee)
        self.dividendes_percus = arrondir(etat.dividendes)
        self.frais_totaux = arrondir(etat.frais)
        self.save(
            update_fields=[
                "quantite", "pmpa", "cout_total", "plus_value_realisee",
                "dividendes_percus", "frais_totaux", "updated_at",
            ]
        )
        return etat

    def mettre_a_jour_cours(self, cours: Decimal, au: date | None = None) -> None:
        au = au or timezone.localdate()
        CoursHistorique.objects.update_or_create(
            user_id=self.user_id, ticker_symbole=self.ticker_symbole, date=au, defaults={"cours": cours}
        )
        if self.date_dernier_cours is None or au >= self.date_dernier_cours:
            self.dernier_cours, self.date_dernier_cours = cours, au
            self.save(update_fields=["dernier_cours", "date_dernier_cours", "updated_at"])

    def clean(self):
        erreurs = self.verifier_proprietaire(compte_titre=self.compte_titre if self.compte_titre_id else None)
        if self.compte_titre_id and not self.compte_titre.est_compte_titre:
            erreurs["compte_titre"] = "Une position doit être rattachée à un compte titres."
        if erreurs:
            raise ValidationError(erreurs)

    def save(self, *args, **kwargs):
        self.ticker_symbole = self.ticker_symbole.strip().upper()
        self.isin = self.isin.strip().upper()
        super().save(*args, **kwargs)


class InvestmentTransaction(UserOwnedModel):
    """
    Opération sur titre. Son impact sur les liquidités du compte titres est
    stocké dans `montant_net` (signé) :
        ACHAT     : −(quantité × prix + frais)
        VENTE     : +(quantité × prix − frais)
        DIVIDENDE : +(quantité × dividende unitaire − frais/retenues)
    Les apports / retraits d'espèces se font par virement interne (Transaction).
    """

    compte_titre = models.ForeignKey(
        Account,
        on_delete=models.RESTRICT,
        related_name="operations_titres",
        limit_choices_to={"type": TypeCompte.TITRE},
        verbose_name="compte titres",
    )
    holding = models.ForeignKey(Holding, on_delete=models.RESTRICT, related_name="operations", verbose_name="titre")
    type_operation = models.CharField("opération", max_length=10, choices=TypeOperationTitre.choices)
    date = models.DateField("date", default=timezone.localdate)
    quantite = models.DecimalField("quantité", validators=[MinValueValidator(Decimal("0.00000001"))], **QUANTITE)
    prix_unitaire = models.DecimalField(
        "prix unitaire",
        validators=[MinValueValidator(ZERO)],
        help_text="Cours d'exécution, ou dividende par titre pour un dividende.",
        **PRIX,
    )
    frais = models.DecimalField(
        "frais", default=ZERO, validators=[MinValueValidator(ZERO)], help_text="Courtage, taxes, retenue à la source.", **MONTANT
    )
    notes = models.CharField("notes", max_length=255, blank=True)

    # --- Champs calculés ---
    montant_net = models.DecimalField("impact sur les liquidités", default=ZERO, editable=False, **MONTANT)
    plus_value_realisee = models.DecimalField("plus-value réalisée", null=True, blank=True, editable=False, **MONTANT)

    objects = UserOwnedQuerySet.as_manager()
    proprietaire_via = "holding"

    class Meta:
        verbose_name = "opération sur titre"
        verbose_name_plural = "opérations sur titres"
        ordering = ["-date", "-pk"]
        indexes = [
            models.Index(fields=["user", "date"]),
            models.Index(fields=["compte_titre", "date"]),
            models.Index(fields=["holding", "date"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(quantite__gt=0), name="op_titre_quantite_positive"),
            models.CheckConstraint(condition=Q(prix_unitaire__gte=0), name="op_titre_prix_positif"),
            models.CheckConstraint(condition=Q(frais__gte=0), name="op_titre_frais_positifs"),
        ]

    def __str__(self) -> str:
        return f"{self.date:%d/%m/%Y} · {self.get_type_operation_display()} {self.quantite.normalize()} {self.ticker_symbole}"

    @property
    def ticker_symbole(self) -> str:
        return self.holding.ticker_symbole if self.holding_id else ""

    @property
    def nom_action(self) -> str:
        return self.holding.nom_action if self.holding_id else ""

    @property
    def montant_brut(self) -> Decimal:
        return arrondir(self.quantite * self.prix_unitaire)

    def calculer_montant_net(self) -> Decimal:
        if self.type_operation == TypeOperationTitre.ACHAT:
            return -(self.montant_brut + self.frais)
        return self.montant_brut - self.frais

    def heriter_proprietaire(self) -> None:
        super().heriter_proprietaire()
        if self.holding_id and not self.compte_titre_id:
            self.compte_titre_id = self.holding.compte_titre_id

    def clean(self):
        erreurs = self.verifier_proprietaire(
            compte_titre=self.compte_titre if self.compte_titre_id else None,
            holding=self.holding if self.holding_id else None,
        )
        if self.compte_titre_id and not self.compte_titre.est_compte_titre:
            erreurs["compte_titre"] = "Sélectionnez un compte de type « Compte titres »."
        if self.holding_id and self.compte_titre_id and self.holding.compte_titre_id != self.compte_titre_id:
            erreurs["holding"] = "Ce titre n'est pas détenu dans ce compte titres."
        if erreurs:
            raise ValidationError(erreurs)

        # Simulation : la position ne doit jamais devenir négative, même rétroactivement.
        if self.holding_id and self.quantite and self.prix_unitaire is not None and self.date:
            autres = list(self.holding.operations.exclude(pk=self.pk)) if self.holding.pk else []
            rejouer_operations(sorted([*autres, self], key=_cle_chronologique))

    def save(self, *args, **kwargs):
        self.montant_net = self.calculer_montant_net()
        ancien_holding_id = None
        if self.pk:
            ancien_holding_id = (
                InvestmentTransaction.objects.filter(pk=self.pk).values_list("holding_id", flat=True).first()
            )
        with db_transaction.atomic():
            super().save(*args, **kwargs)
            self.holding.recalculer()
            if ancien_holding_id and ancien_holding_id != self.holding_id:
                Holding.objects.get(pk=ancien_holding_id).recalculer()
        # Le recalcul écrit la PV réalisée en base : on la reporte sur l'instance en mémoire.
        self.refresh_from_db(fields=["plus_value_realisee"])

    def delete(self, *args, **kwargs):
        holding = self.holding
        with db_transaction.atomic():
            resultat = super().delete(*args, **kwargs)
            holding.recalculer()
        return resultat


class CoursHistorique(UserOwnedModel):
    """Historique des cours (saisie manuelle ou import), pour la valorisation passée."""

    ticker_symbole = models.CharField("ticker", max_length=20)
    date = models.DateField("date")
    cours = models.DecimalField("cours", validators=[MinValueValidator(ZERO)], **PRIX)

    class Meta:
        verbose_name = "cours historique"
        verbose_name_plural = "cours historiques"
        ordering = ["ticker_symbole", "-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "ticker_symbole", "date"],
                name="cours_unique_par_jour",
                violation_error_message="Un cours est déjà enregistré pour ce titre à cette date.",
            ),
        ]
        indexes = [models.Index(fields=["user", "ticker_symbole", "-date"])]

    def __str__(self) -> str:
        return f"{self.ticker_symbole} {self.date:%d/%m/%Y} : {self.cours}"

    def save(self, *args, **kwargs):
        self.ticker_symbole = self.ticker_symbole.strip().upper()
        super().save(*args, **kwargs)


# ===========================================================================
# Budget prévisionnel pluriannuel
# ===========================================================================
class Periodicite(models.TextChoices):
    MENSUELLE = "MENSUELLE", "Mensuelle"
    TRIMESTRIELLE = "TRIMESTRIELLE", "Trimestrielle"
    SEMESTRIELLE = "SEMESTRIELLE", "Semestrielle"
    ANNUELLE = "ANNUELLE", "Annuelle"


PAS_MOIS = {
    Periodicite.MENSUELLE: 1,
    Periodicite.TRIMESTRIELLE: 3,
    Periodicite.SEMESTRIELLE: 6,
    Periodicite.ANNUELLE: 12,
}

def annee_courante() -> int:
    return timezone.localdate().year


valider_taux = [MinValueValidator(Decimal("-100")), MaxValueValidator(Decimal("100"))]


class BudgetPlan(UserOwnedModel):
    """
    Plan budgétaire sur N années (5 par défaut) avec indexation annuelle globale.
    Un seul plan actif par utilisateur : c'est lui qui alimente le comparatif Réel vs Prévu.
    """

    nom = models.CharField("nom", max_length=100)
    annee_debut = models.PositiveSmallIntegerField(
        "année de début", default=annee_courante, validators=[MinValueValidator(2000)]
    )
    duree_annees = models.PositiveSmallIntegerField(
        "durée (années)", default=5, validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    taux_indexation = models.DecimalField(
        "indexation annuelle (%)",
        default=Decimal("2.00"),
        validators=valider_taux,
        help_text="Variation appliquée chaque année (ex. inflation). Peut être surchargée par ligne.",
        **TAUX,
    )
    actif = models.BooleanField("plan actif", default=True)
    description = models.TextField("description", blank=True)

    _CHAMPS_GENERATION = ("annee_debut", "duree_annees", "taux_indexation")

    class Meta:
        verbose_name = "plan budgétaire"
        verbose_name_plural = "plans budgétaires"
        ordering = ["-actif", "-annee_debut", "nom"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "nom"],
                name="plan_budget_nom_unique",
                violation_error_message="Un plan porte déjà ce nom.",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(actif=True),
                name="un_seul_plan_actif_par_user",
                violation_error_message="Un seul plan budgétaire peut être actif à la fois.",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.nom} ({self.annee_debut}–{self.annee_fin})"

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        # Ne lire que les champs effectivement chargés : accéder à un champ différé relancerait
        # une requête, donc from_db, à l'infini (cas des suppressions en cascade, qui chargent le seul pk).
        charges = dict(zip(field_names, values))
        if all(champ in charges for champ in cls._CHAMPS_GENERATION):
            instance._parametres_initiaux = tuple(charges[champ] for champ in cls._CHAMPS_GENERATION)
        return instance

    def _parametres_generation(self):
        return tuple(getattr(self, champ) for champ in self._CHAMPS_GENERATION)

    @property
    def annee_fin(self) -> int:
        return self.annee_debut + self.duree_annees - 1

    @property
    def annees(self) -> list[int]:
        return list(range(self.annee_debut, self.annee_fin + 1))

    def couvre(self, annee: int) -> bool:
        return self.annee_debut <= annee <= self.annee_fin

    @db_transaction.atomic
    def regenerer(self, ecraser_manuels: bool = False) -> None:
        for ligne in self.lignes.select_related("plan"):
            ligne.generer_montants(ecraser_manuels=ecraser_manuels)

    def save(self, *args, **kwargs):
        # Paramètres initiaux inconnus (création, chargement partiel) : on régénère par prudence,
        # les saisies manuelles étant de toute façon conservées.
        initiaux = getattr(self, "_parametres_initiaux", None)
        parametres_modifies = initiaux is None or initiaux != self._parametres_generation()
        with db_transaction.atomic():
            super().save(*args, **kwargs)
            if parametres_modifies:
                self.regenerer()
        self._parametres_initiaux = self._parametres_generation()


class Budget(UserOwnedModel):
    """
    Ligne budgétaire d'un plan pour une catégorie (ou une sous-catégorie précise).
    Le montant de référence (année 1) est décliné en montants mensuels sur toute
    la durée du plan, indexés d'année en année (voir BudgetAllocation).
    """

    plan = models.ForeignKey(BudgetPlan, on_delete=models.CASCADE, related_name="lignes", verbose_name="plan")
    categorie = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="budgets", verbose_name="catégorie"
    )
    sous_categorie = models.ForeignKey(
        SubCategory,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="budgets",
        verbose_name="sous-catégorie",
        help_text="Vide : budget global de la catégorie.",
    )
    montant_reference = models.DecimalField(
        "montant par échéance",
        validators=[MinValueValidator(ZERO)],
        help_text="Montant de la première année, par occurrence de la périodicité.",
        **MONTANT,
    )
    periodicite = models.CharField(
        "périodicité", max_length=13, choices=Periodicite.choices, default=Periodicite.MENSUELLE
    )
    mois_reference = models.PositiveSmallIntegerField(
        "mois de la première échéance",
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    lisser = models.BooleanField(
        "lisser sur l'année",
        default=False,
        help_text="Répartit chaque échéance uniformément sur les mois (ex. taxe annuelle ÷ 12).",
    )
    taux_indexation = models.DecimalField(
        "indexation spécifique (%)",
        null=True,
        blank=True,
        validators=valider_taux,
        help_text="Vide : indexation globale du plan.",
        **TAUX,
    )
    notes = models.CharField("notes", max_length=255, blank=True)

    proprietaire_via = "plan"

    class Meta:
        verbose_name = "ligne budgétaire"
        verbose_name_plural = "lignes budgétaires"
        ordering = ["categorie__type", "categorie__ordre", "categorie__nom", "sous_categorie__nom"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "categorie", "sous_categorie"],
                condition=Q(sous_categorie__isnull=False),
                name="budget_unique_sous_categorie",
                violation_error_message="Cette sous-catégorie est déjà budgétée dans ce plan.",
            ),
            models.UniqueConstraint(
                fields=["plan", "categorie"],
                condition=Q(sous_categorie__isnull=True),
                name="budget_unique_categorie",
                violation_error_message="Cette catégorie est déjà budgétée dans ce plan.",
            ),
        ]

    def __str__(self) -> str:
        cible = self.sous_categorie or self.categorie
        return f"{self.plan.nom} · {cible}"

    @property
    def type(self) -> str:
        return self.categorie.type

    @property
    def taux_effectif(self) -> Decimal:
        return self.taux_indexation if self.taux_indexation is not None else self.plan.taux_indexation

    def coefficient_indexation(self, annee: int) -> Decimal:
        return (1 + self.taux_effectif / 100) ** (annee - self.plan.annee_debut)

    def montant_theorique(self, annee: int, mois: int) -> Decimal:
        """Montant prévu pour un mois donné, avant toute saisie manuelle."""
        pas = PAS_MOIS[self.periodicite]
        if self.lisser:
            base = self.montant_reference / pas
        elif (mois - self.mois_reference) % pas == 0:
            base = self.montant_reference
        else:
            return ZERO
        return arrondir(base * self.coefficient_indexation(annee))

    def montant_annuel_theorique(self, annee: int) -> Decimal:
        return sum((self.montant_theorique(annee, m) for m in range(1, 13)), ZERO)

    def generer_montants(self, ecraser_manuels: bool = False) -> None:
        """
        Crée / met à jour les montants mensuels sur toute la durée du plan.
        Les montants saisis manuellement sont préservés sauf si `ecraser_manuels`.
        """
        annees = self.plan.annees
        self.montants.exclude(annee__in=annees).delete()
        existants = {(a.annee, a.mois): a for a in self.montants.all()}
        a_creer, a_modifier = [], []
        for annee in annees:
            for mois in range(1, 13):
                montant = self.montant_theorique(annee, mois)
                allocation = existants.get((annee, mois))
                if allocation is None:
                    a_creer.append(BudgetAllocation(budget=self, annee=annee, mois=mois, montant=montant))
                elif (ecraser_manuels or not allocation.saisi_manuellement) and (
                    allocation.montant != montant or allocation.saisi_manuellement
                ):
                    allocation.montant, allocation.saisi_manuellement = montant, False
                    a_modifier.append(allocation)
        BudgetAllocation.objects.bulk_create(a_creer)
        BudgetAllocation.objects.bulk_update(a_modifier, ["montant", "saisi_manuellement"])

    def clean(self):
        erreurs = self.verifier_proprietaire(
            plan=self.plan if self.plan_id else None,
            categorie=self.categorie if self.categorie_id else None,
            sous_categorie=self.sous_categorie if self.sous_categorie_id else None,
        )
        if self.sous_categorie_id and self.categorie_id and self.sous_categorie.categorie_id != self.categorie_id:
            erreurs["sous_categorie"] = "Cette sous-catégorie n'appartient pas à la catégorie choisie."
        if erreurs:
            raise ValidationError(erreurs)

    def save(self, *args, **kwargs):
        if self.sous_categorie_id and not self.categorie_id:
            self.categorie_id = self.sous_categorie.categorie_id
        with db_transaction.atomic():
            super().save(*args, **kwargs)
            self.generer_montants()


class BudgetAllocationQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(budget__user=user)

    def du_plan(self, plan):
        return self.filter(budget__plan=plan)

    def periode(self, annee: int, mois: int | None = None):
        qs = self.filter(annee=annee)
        return qs.filter(mois=mois) if mois else qs


class BudgetAllocation(models.Model):
    """Montant prévisionnel d'une ligne budgétaire pour un mois donné (cellule de la matrice)."""

    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="montants", verbose_name="ligne")
    annee = models.PositiveSmallIntegerField("année")
    mois = models.PositiveSmallIntegerField("mois", validators=[MinValueValidator(1), MaxValueValidator(12)])
    montant = models.DecimalField("montant prévu", default=ZERO, validators=[MinValueValidator(ZERO)], **MONTANT)
    saisi_manuellement = models.BooleanField(
        "saisi manuellement", default=False, help_text="Préservé lors des recalculs automatiques."
    )

    objects = BudgetAllocationQuerySet.as_manager()

    class Meta:
        verbose_name = "montant budgété"
        verbose_name_plural = "montants budgétés"
        ordering = ["budget", "annee", "mois"]
        constraints = [
            models.UniqueConstraint(fields=["budget", "annee", "mois"], name="allocation_unique_par_mois"),
            models.CheckConstraint(condition=Q(mois__gte=1, mois__lte=12), name="allocation_mois_valide"),
            models.CheckConstraint(condition=Q(montant__gte=0), name="allocation_montant_positif"),
        ]
        indexes = [models.Index(fields=["annee", "mois"])]

    def __str__(self) -> str:
        return f"{self.budget} · {self.mois:02d}/{self.annee} : {self.montant}"

    @property
    def periode(self) -> date:
        return date(self.annee, self.mois, 1)
