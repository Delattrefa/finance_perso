"""
Formulaires de PyFinance.

Tous les ModelForm métier héritent de `UserScopedModelForm` : ils reçoivent `user=`
et restreignent chaque liste de choix aux données de cet utilisateur.

Le filtrage dynamique catégorie → sous-catégorie fonctionne côté serveur dans les deux sens :
- à l'affichage, les sous-catégories sont limitées à la catégorie courante ;
- les templates (étape 4) rechargent la liste via HTMX à chaque changement de catégorie / type.
"""
from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Q

from core.forms import ColorInput, DateInput, TailwindFormMixin, UserScopedModelForm

from .models import (
    PRIX,
    Account,
    Budget,
    BudgetAllocation,
    BudgetPlan,
    Category,
    Holding,
    InvestmentTransaction,
    StatutTransaction,
    SubCategory,
    Transaction,
    TypeActif,
    TypeCompte,
    TypeOperationTitre,
    TypeTransaction,
)
from .services.periodes import PERIODES, bornes_periode

MOIS = [
    (1, "Janvier"), (2, "Février"), (3, "Mars"), (4, "Avril"), (5, "Mai"), (6, "Juin"),
    (7, "Juillet"), (8, "Août"), (9, "Septembre"), (10, "Octobre"), (11, "Novembre"), (12, "Décembre"),
]


def actifs_ou_selectionne(queryset, pk=None):
    """Objets non archivés, plus celui déjà sélectionné (édition d'une opération ancienne)."""
    filtre = Q(archive=False)
    if pk:
        filtre |= Q(pk=pk)
    return queryset.filter(filtre)


def _est_identifiant(valeur) -> bool:
    return valeur is not None and str(valeur).isdigit()


class CategorieDynamiqueMixin:
    """Restreint `categorie` (par type) et `sous_categorie` (par catégorie choisie)."""

    def configurer_categories(self, type_flux: str | None = None) -> None:
        categories = actifs_ou_selectionne(Category.objects.for_user(self.user), self.instance.categorie_id)
        if type_flux in (TypeTransaction.DEPENSE, TypeTransaction.REVENU):
            categories = categories.filter(type=type_flux)
        self.restreindre("categorie", categories)

        sous_categories = actifs_ou_selectionne(
            SubCategory.objects.for_user(self.user).select_related("categorie"), self.instance.sous_categorie_id
        )
        categorie_id = self.valeur_courante("categorie")
        if _est_identifiant(categorie_id):
            sous_categories = sous_categories.filter(categorie_id=categorie_id)
        elif not self.is_bound:
            sous_categories = sous_categories.none()
        # Formulaire soumis sans catégorie : toutes les sous-catégories restent valides,
        # la catégorie sera déduite par le modèle.
        self.restreindre("sous_categorie", sous_categories)
        if "sous_categorie" in self.fields:
            self.fields["sous_categorie"].label_from_instance = lambda sc: sc.nom


# ===========================================================================
# Comptes & catégories
# ===========================================================================
class AccountForm(UserScopedModelForm):
    class Meta:
        model = Account
        fields = (
            "nom", "type", "institution", "numero", "devise", "solde_initial", "date_creation",
            "couleur", "icone", "inclure_patrimoine", "ordre", "archive", "notes",
        )
        widgets = {
            "date_creation": DateInput(),
            "couleur": ColorInput(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_type(self):
        nouveau = self.cleaned_data["type"]
        compte = self.instance
        if (
            compte.pk
            and compte.type == TypeCompte.TITRE
            and nouveau != TypeCompte.TITRE
            and (compte.positions.exists() or compte.operations_titres.exists())
        ):
            raise ValidationError("Ce compte détient des titres : il doit rester un compte titres.")
        return nouveau

    def clean_devise(self):
        nouvelle = self.cleaned_data["devise"]
        compte = self.instance
        if (
            compte.pk
            and nouvelle != compte.devise
            and (compte.transactions.exists() or compte.virements_entrants.exists() or compte.operations_titres.exists())
        ):
            raise ValidationError("Impossible de changer la devise d'un compte qui a déjà des opérations.")
        return nouvelle


class CategoryForm(UserScopedModelForm):
    class Meta:
        model = Category
        fields = ("nom", "type", "icone", "couleur", "ordre", "archive")
        widgets = {"couleur": ColorInput()}

    def clean_type(self):
        nouveau = self.cleaned_data["type"]
        categorie = self.instance
        if categorie.pk and nouveau != categorie.type and categorie.transactions.exists():
            raise ValidationError("Des opérations utilisent cette catégorie : son type ne peut plus changer.")
        return nouveau


class SubCategoryForm(UserScopedModelForm):
    class Meta:
        model = SubCategory
        fields = ("categorie", "nom", "icone", "couleur", "ordre", "archive")
        widgets = {"couleur": forms.TextInput(attrs={"placeholder": "#RRGGBB — vide : couleur de la catégorie"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.restreindre(
            "categorie", actifs_ou_selectionne(Category.objects.for_user(self.user), self.instance.categorie_id)
        )

    def clean_categorie(self):
        nouvelle = self.cleaned_data["categorie"]
        sc = self.instance
        if sc.pk and nouvelle.pk != sc.categorie_id and sc.transactions.exists():
            raise ValidationError("Des opérations utilisent cette sous-catégorie : elle ne peut pas changer de catégorie.")
        return nouvelle


# ===========================================================================
# Opérations
# ===========================================================================
class TransactionForm(CategorieDynamiqueMixin, UserScopedModelForm):
    """Saisie rapide d'une dépense, d'un revenu ou d'un virement interne."""

    class Meta:
        model = Transaction
        fields = (
            "type", "date", "compte", "compte_destination", "montant", "montant_destination",
            "categorie", "sous_categorie", "tiers_payeur", "description", "statut", "notes",
        )
        widgets = {
            "type": forms.RadioSelect,
            "statut": forms.RadioSelect,
            "date": DateInput(),
            "montant": forms.TextInput(
                attrs={"inputmode": "decimal", "placeholder": "0,00", "autofocus": True, "class": "pr-12 text-lg font-semibold"}
            ),
            "tiers_payeur": forms.TextInput(attrs={"list": "tiers-suggestions", "autocomplete": "off"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        comptes = Account.objects.for_user(self.user)
        self.restreindre("compte", actifs_ou_selectionne(comptes, self.instance.compte_id))
        self.restreindre("compte_destination", actifs_ou_selectionne(comptes, self.instance.compte_destination_id))
        self.configurer_categories(self.valeur_courante("type"))

    def clean(self):
        data = super().clean()
        if data.get("type") == TypeTransaction.VIREMENT_INTERNE:
            data["categorie"] = data["sous_categorie"] = None
            if not data.get("compte_destination") and "compte_destination" not in self.errors:
                self.add_error("compte_destination", "Choisissez le compte crédité.")
        else:
            data["compte_destination"] = data["montant_destination"] = None
        return data

    @staticmethod
    def suggestions_tiers(user, limite: int = 200) -> list[str]:
        """Tiers déjà utilisés, du plus récent au plus ancien (pour la <datalist>)."""
        tiers = (
            Transaction.objects.for_user(user)
            .exclude(tiers_payeur="")
            .order_by("-date")
            .values_list("tiers_payeur", flat=True)[: limite * 5]
        )
        return list(dict.fromkeys(tiers))[:limite]


# ===========================================================================
# Compte titres
# ===========================================================================
class InvestmentTransactionForm(UserScopedModelForm):
    """
    Achat / vente / dividende. Le titre est soit choisi parmi les positions existantes,
    soit créé à la volée à partir d'un ticker (premier achat).
    Note : une nouvelle position est créée par `save()`, y compris avec commit=False.
    """

    ticker_symbole = forms.CharField(
        label="Ticker", max_length=20, required=False, help_text="Uniquement pour un nouveau titre (ex. CW8, AI.PA)."
    )
    nom_action = forms.CharField(label="Nom du titre", max_length=150, required=False)
    type_actif = forms.ChoiceField(label="Type d'actif", choices=TypeActif.choices, initial=TypeActif.ACTION, required=False)

    field_order = [
        "compte_titre", "holding", "ticker_symbole", "nom_action", "type_actif",
        "type_operation", "date", "quantite", "prix_unitaire", "frais", "notes",
    ]

    class Meta:
        model = InvestmentTransaction
        fields = ("compte_titre", "holding", "type_operation", "date", "quantite", "prix_unitaire", "frais", "notes")
        labels = {"holding": "Titre existant"}
        widgets = {"type_operation": forms.RadioSelect, "date": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._nouvelle_position: Holding | None = None

        comptes = actifs_ou_selectionne(
            Account.objects.for_user(self.user).comptes_titres(), self.instance.compte_titre_id
        )
        self.restreindre("compte_titre", comptes)
        self.fields["compte_titre"].required = False
        if not self.is_bound and not self.instance.pk and len(comptes) == 1:
            self.initial.setdefault("compte_titre", comptes[0].pk)

        positions = Holding.objects.for_user(self.user).select_related("compte_titre")
        compte_id = self.valeur_courante("compte_titre")
        if _est_identifiant(compte_id):
            positions = positions.filter(compte_titre_id=compte_id)
        self.restreindre("holding", positions)
        self.fields["holding"].required = False
        self.fields["holding"].label_from_instance = (
            lambda h: f"{h.ticker_symbole} · {h.nom_action} ({h.quantite.normalize():f} titres)"
        )

    def clean(self):
        data = super().clean()
        holding, compte = data.get("holding"), data.get("compte_titre")
        ticker = (data.get("ticker_symbole") or "").strip().upper()

        if holding:
            if compte and holding.compte_titre_id != compte.pk:
                self.add_error("holding", "Ce titre n'appartient pas au compte titres sélectionné.")
            data["compte_titre"] = holding.compte_titre
            return data
        if not ticker:
            self.add_error("holding", "Choisissez un titre existant ou saisissez le ticker d'un nouveau titre.")
            return data
        if not compte:
            self.add_error("compte_titre", "Indiquez le compte titres du nouveau titre.")
            return data

        existant = Holding.objects.filter(compte_titre=compte, ticker_symbole=ticker).first()
        if existant:
            data["holding"] = existant
            return data

        if data.get("type_operation") != TypeOperationTitre.ACHAT:
            self.add_error("type_operation", "La première opération sur un nouveau titre doit être un achat.")
        if not data.get("nom_action"):
            self.add_error("nom_action", "Indiquez le nom du nouveau titre.")
        self._nouvelle_position = Holding(
            user=self.user,
            compte_titre=compte,
            ticker_symbole=ticker,
            nom_action=data.get("nom_action", ""),
            type_actif=data.get("type_actif") or TypeActif.ACTION,
        )
        return data

    @db_transaction.atomic
    def save(self, commit=True):
        if self._nouvelle_position is not None:
            self._nouvelle_position.save()
            self.instance.holding = self._nouvelle_position
        return super().save(commit=commit)


class HoldingForm(UserScopedModelForm):
    champs_toujours_valides = ("user", "compte_titre")

    class Meta:
        model = Holding
        fields = ("ticker_symbole", "nom_action", "isin", "type_actif", "secteur")


class MiseAJourCoursForm(TailwindFormMixin, forms.Form):
    cours = forms.DecimalField(label="Cours", min_value=0, max_digits=PRIX["max_digits"], decimal_places=PRIX["decimal_places"])
    date = forms.DateField(label="Date", widget=DateInput())

    def appliquer(self, holding: Holding) -> None:
        holding.mettre_a_jour_cours(self.cleaned_data["cours"], self.cleaned_data["date"])


# ===========================================================================
# Budget
# ===========================================================================
class BudgetPlanForm(UserScopedModelForm):
    """Activer un plan désactive automatiquement les autres plans de l'utilisateur."""

    class Meta:
        model = BudgetPlan
        fields = ("nom", "annee_debut", "duree_annees", "taux_indexation", "actif", "description")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def _get_validation_exclusions(self):
        # L'unicité du plan actif est assurée par save() : on ne la valide pas ici.
        exclusions = super()._get_validation_exclusions()
        exclusions.add("actif")
        return exclusions

    @db_transaction.atomic
    def save(self, commit=True):
        plan = super().save(commit=False)
        if commit:
            if plan.actif:
                BudgetPlan.objects.for_user(self.user).filter(actif=True).exclude(pk=plan.pk).update(actif=False)
            plan.save()
        return plan


class BudgetForm(CategorieDynamiqueMixin, UserScopedModelForm):
    """Ligne budgétaire d'un plan (le plan est fourni par la vue)."""

    champs_toujours_valides = ("user", "plan")

    class Meta:
        model = Budget
        fields = (
            "categorie", "sous_categorie", "montant_reference", "periodicite",
            "mois_reference", "lisser", "taux_indexation", "notes",
        )
        widgets = {"mois_reference": forms.Select(choices=MOIS)}

    def __init__(self, *args, plan: BudgetPlan, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.plan_id:
            self.instance.plan = plan
        self.configurer_categories()
        # Masqué (et donc non soumis) pour une périodicité mensuelle
        self.fields["mois_reference"].required = False

    def clean_mois_reference(self):
        return self.cleaned_data.get("mois_reference") or 1


class SaisieManuelleMixin:
    """Une cellule budgétaire modifiée à la main est protégée des recalculs automatiques."""

    def save(self, commit=True):
        if "montant" in self.changed_data and "saisi_manuellement" not in self.changed_data:
            self.instance.saisi_manuellement = True
        return super().save(commit=commit)


class BudgetAllocationForm(SaisieManuelleMixin, TailwindFormMixin, forms.ModelForm):
    """Édition d'une cellule de la matrice budgétaire (HTMX, étape 4)."""

    class Meta:
        model = BudgetAllocation
        fields = ("montant",)
        widgets = {"montant": forms.TextInput(attrs={"inputmode": "decimal", "class": "text-right"})}


# ===========================================================================
# Recherche multicritère
# ===========================================================================
class RechercheForm(TailwindFormMixin, forms.Form):
    TRIS = [
        ("-date", "Date (récentes d'abord)"),
        ("date", "Date (anciennes d'abord)"),
        ("-montant", "Montant décroissant"),
        ("montant", "Montant croissant"),
        ("tiers_payeur", "Tiers (A → Z)"),
    ]

    q = forms.CharField(
        label="Texte", required=False,
        widget=forms.TextInput(attrs={"type": "search", "placeholder": "Tiers, description, notes…", "class": "pl-9"}),
    )
    periode = forms.ChoiceField(label="Période", choices=PERIODES, required=False)
    date_debut = forms.DateField(label="Du", required=False, widget=DateInput())
    date_fin = forms.DateField(label="Au", required=False, widget=DateInput())
    montant_min = forms.DecimalField(label="Montant min.", required=False, min_value=0, decimal_places=2)
    montant_max = forms.DecimalField(label="Montant max.", required=False, min_value=0, decimal_places=2)
    comptes = forms.ModelMultipleChoiceField(
        label="Comptes", queryset=Account.objects.none(), required=False, widget=forms.CheckboxSelectMultiple
    )
    types = forms.MultipleChoiceField(
        label="Types", choices=TypeTransaction.choices, required=False, widget=forms.CheckboxSelectMultiple
    )
    categories = forms.ModelMultipleChoiceField(label="Catégories", queryset=Category.objects.none(), required=False)
    sous_categories = forms.ModelMultipleChoiceField(
        label="Sous-catégories", queryset=SubCategory.objects.none(), required=False
    )
    sans_categorie = forms.BooleanField(label="Opérations non catégorisées", required=False)
    statut = forms.ChoiceField(
        label="Statut", choices=[("", "Tous les statuts"), *StatutTransaction.choices], required=False
    )
    tri = forms.ChoiceField(label="Trier par", choices=TRIS, required=False, initial="-date")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        # L'historique peut référencer des éléments archivés : on les propose aussi.
        self.fields["comptes"].queryset = Account.objects.for_user(user)
        self.fields["categories"].queryset = Category.objects.for_user(user)
        self.fields["sous_categories"].queryset = SubCategory.objects.for_user(user).select_related("categorie")

    def clean(self):
        data = super().clean()
        periode = data.get("periode")
        if periode and periode != "perso":
            data["date_debut"], data["date_fin"] = bornes_periode(periode)
        if data.get("date_debut") and data.get("date_fin") and data["date_debut"] > data["date_fin"]:
            self.add_error("date_fin", "La date de fin précède la date de début.")
        mini, maxi = data.get("montant_min"), data.get("montant_max")
        if mini is not None and maxi is not None and mini > maxi:
            self.add_error("montant_max", "Le montant maximum est inférieur au minimum.")
        return data

    def filtrer(self, queryset=None):
        """
        Applique les critères à un queryset de Transaction de l'utilisateur.
        Formulaire non soumis : tout est renvoyé ; formulaire invalide : rien.
        """
        if queryset is None:
            queryset = Transaction.objects.for_user(self.user)
        queryset = queryset.select_related("compte", "compte_destination", "categorie", "sous_categorie")
        if not self.is_bound:
            return queryset.order_by("-date", "-pk")
        if not self.is_valid():
            return queryset.none()

        d = self.cleaned_data
        qs = queryset
        for mot in d["q"].split():
            qs = qs.filter(Q(tiers_payeur__icontains=mot) | Q(description__icontains=mot) | Q(notes__icontains=mot))
        qs = qs.periode(d["date_debut"], d["date_fin"])
        if d["montant_min"] is not None:
            qs = qs.filter(montant__gte=d["montant_min"])
        if d["montant_max"] is not None:
            qs = qs.filter(montant__lte=d["montant_max"])
        if d["comptes"]:
            qs = qs.filter(Q(compte__in=d["comptes"]) | Q(compte_destination__in=d["comptes"]))
        if d["types"]:
            qs = qs.filter(type__in=d["types"])

        filtre_categories = Q()
        if d["categories"]:
            filtre_categories |= Q(categorie__in=d["categories"])
        if d["sous_categories"]:
            filtre_categories |= Q(sous_categorie__in=d["sous_categories"])
        if d["sans_categorie"]:
            filtre_categories |= Q(categorie__isnull=True) & ~Q(type=TypeTransaction.VIREMENT_INTERNE)
        if filtre_categories:
            qs = qs.filter(filtre_categories)

        if d["statut"]:
            qs = qs.filter(statut=d["statut"])
        return qs.order_by(d["tri"] or "-date", "-pk")

    def criteres_actifs(self) -> list[str]:
        """Libellés courts des filtres appliqués (pastilles au-dessus des résultats)."""
        if not self.is_bound or not self.is_valid():
            return []
        d, criteres = self.cleaned_data, []
        if d["q"]:
            criteres.append(f"« {d['q']} »")
        if d["date_debut"] or d["date_fin"]:
            debut = d["date_debut"].strftime("%d/%m/%Y") if d["date_debut"] else "…"
            fin = d["date_fin"].strftime("%d/%m/%Y") if d["date_fin"] else "…"
            criteres.append(f"{debut} → {fin}")
        if d["montant_min"] is not None or d["montant_max"] is not None:
            criteres.append(f"{d['montant_min'] if d['montant_min'] is not None else 0} – {d['montant_max'] or '∞'}")
        criteres += [c.nom for c in d["comptes"]]
        criteres += [dict(TypeTransaction.choices)[t] for t in d["types"]]
        criteres += [c.nom for c in d["categories"]] + [str(sc) for sc in d["sous_categories"]]
        if d["sans_categorie"]:
            criteres.append("Non catégorisées")
        if d["statut"]:
            criteres.append(dict(StatutTransaction.choices)[d["statut"]])
        return criteres


class PeriodeForm(TailwindFormMixin, forms.Form):
    """Sélecteur de période des rapports (GET)."""

    periode = forms.ChoiceField(label="Période", choices=[c for c in PERIODES if c[0]], required=False)
    date_debut = forms.DateField(label="Du", required=False, widget=DateInput())
    date_fin = forms.DateField(label="Au", required=False, widget=DateInput())

    def bornes(self, defaut: str = "mois"):
        """(début, fin) choisis ; la période `defaut` si rien de valide n'est fourni."""
        if self.is_bound and self.is_valid():
            d = self.cleaned_data
            if d["periode"] == "perso" and d["date_debut"] and d["date_fin"] and d["date_debut"] <= d["date_fin"]:
                return d["date_debut"], d["date_fin"]
            if d["periode"] and d["periode"] != "perso":
                return bornes_periode(d["periode"])
        return bornes_periode(defaut)
