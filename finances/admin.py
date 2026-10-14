"""
Administration de PyFinance.

- Superutilisateur : voit toutes les données, choisit le propriétaire des objets « racines »
  (comptes, catégories, plans, cours) ; les objets enfants héritent du propriétaire de leur parent.
- Membre du staff non superutilisateur : ne voit et ne lie que ses propres données.
"""
from django import forms
from django.contrib import admin, messages
from django.db import transaction as db_transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from django.utils.html import format_html

from core.formatage import formater_montant

from .forms import SaisieManuelleMixin
from .models import (
    Account,
    Budget,
    BudgetAllocation,
    BudgetPlan,
    Category,
    CoursHistorique,
    Holding,
    InvestmentTransaction,
    StatutTransaction,
    SubCategory,
    Transaction,
    TypeFlux,
)
from .services.exports import ENTETES_TRANSACTIONS, lignes_transactions, reponse_csv

SeulementUtilises = admin.RelatedOnlyFieldListFilter


# ---------------------------------------------------------------------------
# Rendu
# ---------------------------------------------------------------------------
def pastille(couleur: str, texte: str = ""):
    return format_html(
        '<span style="display:inline-block;width:.7rem;height:.7rem;border-radius:9999px;'
        'background:{};vertical-align:middle;margin-right:.4rem"></span>{}',
        couleur or "#94a3b8",
        texte,
    )


def montant_colore(valeur, devise: str = "EUR", signe: bool = True):
    if valeur is None:
        return "—"
    couleur = "#15803d" if valeur > 0 else "#b91c1c" if valeur < 0 else "inherit"
    return format_html(
        '<span style="color:{};font-variant-numeric:tabular-nums;white-space:nowrap">{}</span>',
        couleur,
        formater_montant(valeur, devise, signe=signe),
    )


# ---------------------------------------------------------------------------
# Bases : isolation par utilisateur
# ---------------------------------------------------------------------------
def _a_un_proprietaire(modele) -> bool:
    return any(f.name == "user" for f in modele._meta.concrete_fields)


class FormulaireAdminProprietaire(forms.ModelForm):
    utilisateur_courant = None  # injecté par ProprietaireAdmin.get_form pour le staff

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.utilisateur_courant is not None and not self.instance.user_id:
            self.instance.user = self.utilisateur_courant

    def _get_validation_exclusions(self):
        # Valide les contraintes (user, nom…) même quand `user` n'est pas un champ du formulaire.
        exclusions = super()._get_validation_exclusions()
        if self.instance.user_id:
            exclusions.discard("user")
        return exclusions


class RestrictionProprietaireMixin:
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        modele = db_field.related_model
        if not request.user.is_superuser and db_field.name != "user" and _a_un_proprietaire(modele):
            kwargs["queryset"] = modele._default_manager.filter(user=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "couleur" and not db_field.blank:
            kwargs["widget"] = forms.TextInput(attrs={"type": "color"})
        return super().formfield_for_dbfield(db_field, request, **kwargs)


class ProprietaireAdmin(RestrictionProprietaireMixin, admin.ModelAdmin):
    form = FormulaireAdminProprietaire
    list_per_page = 50

    def _proprietaire_editable(self, request) -> bool:
        return request.user.is_superuser and not self.model.proprietaire_via

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(user=request.user)

    def get_exclude(self, request, obj=None):
        exclude = list(super().get_exclude(request, obj) or [])
        if not self._proprietaire_editable(request):
            exclude.append("user")
        return exclude

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if self.fieldsets and self._proprietaire_editable(request):
            fieldsets = [("Propriétaire", {"fields": ("user",)}), *fieldsets]
        return fieldsets

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser:
            form = type(form.__name__, (form,), {"utilisateur_courant": request.user})
        return form

    def get_autocomplete_fields(self, request):
        champs = tuple(super().get_autocomplete_fields(request))
        return (*champs, "user") if self._proprietaire_editable(request) else champs

    def get_list_display(self, request):
        colonnes = tuple(super().get_list_display(request))
        return (*colonnes, "user") if request.user.is_superuser else colonnes

    def get_list_filter(self, request):
        filtres = tuple(super().get_list_filter(request))
        return (*filtres, "user") if request.user.is_superuser else filtres


class ProprietaireInline(RestrictionProprietaireMixin, admin.TabularInline):
    """Inline d'objets enfants : `user` est hérité du parent (voir UserOwnedModel.proprietaire_via)."""

    exclude = ("user",)
    extra = 0


# ===========================================================================
# Comptes
# ===========================================================================
@admin.register(Account)
class AccountAdmin(ProprietaireAdmin):
    list_display = (
        "apercu", "type", "institution", "devise", "liquidites", "valorisation", "total",
        "inclure_patrimoine", "archive",
    )
    list_filter = ("type", "devise", "archive", "inclure_patrimoine")
    search_fields = ("nom", "institution", "numero")
    fieldsets = (
        (None, {"fields": ("nom", "type", "devise", "solde_initial", "date_creation")}),
        ("Établissement", {"fields": ("institution", "numero")}),
        ("Affichage", {"fields": ("couleur", "icone", "ordre", "inclure_patrimoine", "archive")}),
        ("Notes", {"fields": ("notes",), "classes": ("collapse",)}),
    )
    actions = ["archiver", "desarchiver"]

    def get_queryset(self, request):
        return super().get_queryset(request).with_soldes()

    @admin.display(description="Compte", ordering="nom")
    def apercu(self, obj):
        return pastille(obj.couleur, obj.nom)

    @admin.display(description="Liquidités", ordering="solde_especes")
    def liquidites(self, obj):
        return montant_colore(obj.solde_especes, obj.devise, signe=False)

    @admin.display(description="Titres", ordering="valorisation_titres")
    def valorisation(self, obj):
        return formater_montant(obj.valorisation_titres, obj.devise) if obj.est_compte_titre else "—"

    @admin.display(description="Solde total", ordering="solde_total")
    def total(self, obj):
        return montant_colore(obj.solde_total, obj.devise, signe=False)

    @admin.action(description="Archiver les comptes sélectionnés")
    def archiver(self, request, queryset):
        self.message_user(request, f"{queryset.update(archive=True)} compte(s) archivé(s).")

    @admin.action(description="Réactiver les comptes sélectionnés")
    def desarchiver(self, request, queryset):
        self.message_user(request, f"{queryset.update(archive=False)} compte(s) réactivé(s).")


# ===========================================================================
# Catégories
# ===========================================================================
class SubCategoryInline(ProprietaireInline):
    model = SubCategory
    fields = ("nom", "icone", "couleur", "ordre", "archive")
    extra = 1


@admin.register(Category)
class CategoryAdmin(ProprietaireAdmin):
    list_display = ("apercu", "type", "nb_sous_categories", "nb_operations", "ordre", "archive")
    list_editable = ("ordre", "archive")
    list_filter = ("type", "archive")
    search_fields = ("nom", "sous_categories__nom")
    inlines = [SubCategoryInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(nb_sc=Count("sous_categories", distinct=True), nb_ops=Count("transactions", distinct=True))
        )

    @admin.display(description="Catégorie", ordering="nom")
    def apercu(self, obj):
        return pastille(obj.couleur, f"{obj.icone} {obj.nom}".strip())

    @admin.display(description="Sous-catégories", ordering="nb_sc")
    def nb_sous_categories(self, obj):
        return obj.nb_sc

    @admin.display(description="Opérations", ordering="nb_ops")
    def nb_operations(self, obj):
        return obj.nb_ops


@admin.register(SubCategory)
class SubCategoryAdmin(ProprietaireAdmin):
    list_display = ("apercu", "categorie", "nb_operations", "ordre", "archive")
    list_filter = ("categorie__type", ("categorie", SeulementUtilises), "archive")
    search_fields = ("nom", "categorie__nom")
    autocomplete_fields = ("categorie",)
    list_select_related = ("categorie",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(nb_ops=Count("transactions"))

    @admin.display(description="Sous-catégorie", ordering="nom")
    def apercu(self, obj):
        return pastille(obj.couleur_effective, obj.nom)

    @admin.display(description="Opérations", ordering="nb_ops")
    def nb_operations(self, obj):
        return obj.nb_ops


# ===========================================================================
# Opérations
# ===========================================================================
@admin.register(Transaction)
class TransactionAdmin(ProprietaireAdmin):
    date_hierarchy = "date"
    list_display = ("date", "type", "compte", "montant_affiche", "classement", "tiers_payeur", "description", "statut")
    list_filter = ("type", "statut", ("compte", SeulementUtilises), "categorie__type", ("categorie", SeulementUtilises))
    search_fields = ("tiers_payeur", "description", "notes")
    list_select_related = ("compte", "compte_destination", "categorie", "sous_categorie")
    autocomplete_fields = ("compte", "compte_destination", "categorie", "sous_categorie")
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 100
    fieldsets = (
        ("Opération", {"fields": ("type", "date", "compte", "montant", "statut")}),
        ("Classement", {"fields": ("categorie", "sous_categorie", "tiers_payeur", "description", "notes")}),
        ("Virement interne", {"fields": ("compte_destination", "montant_destination"), "classes": ("collapse",)}),
        ("Suivi", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
    actions = ["marquer_rapprochees", "marquer_en_attente", "exporter_csv"]

    @admin.display(description="Montant", ordering="montant")
    def montant_affiche(self, obj):
        if obj.est_virement:
            return format_html("<span style='white-space:nowrap'>⇄ {}</span>", formater_montant(obj.montant, obj.compte.devise))
        return montant_colore(obj.montant_signe(), obj.compte.devise)

    @admin.display(description="Catégorie / destination")
    def classement(self, obj):
        if obj.est_virement:
            return f"→ {obj.compte_destination}"
        if obj.sous_categorie:
            return pastille(obj.sous_categorie.couleur_effective, f"{obj.categorie.nom} › {obj.sous_categorie.nom}")
        if obj.categorie:
            return pastille(obj.categorie.couleur, obj.categorie.nom)
        return "—"

    @admin.action(description="Marquer comme rapprochées")
    def marquer_rapprochees(self, request, queryset):
        n = queryset.update(statut=StatutTransaction.RAPPROCHE, updated_at=timezone.now())
        self.message_user(request, f"{n} opération(s) rapprochée(s).")

    @admin.action(description="Marquer comme en attente")
    def marquer_en_attente(self, request, queryset):
        n = queryset.update(statut=StatutTransaction.EN_ATTENTE, updated_at=timezone.now())
        self.message_user(request, f"{n} opération(s) remise(s) en attente.")

    @admin.action(description="Exporter en CSV")
    def exporter_csv(self, request, queryset):
        return reponse_csv("operations", ENTETES_TRANSACTIONS, lignes_transactions(queryset.order_by("date", "pk")))


# ===========================================================================
# Compte titres
# ===========================================================================
class InvestmentTransactionInline(ProprietaireInline):
    model = InvestmentTransaction
    fk_name = "holding"
    fields = ("date", "type_operation", "quantite", "prix_unitaire", "frais", "montant_net", "plus_value_realisee", "notes")
    readonly_fields = ("montant_net", "plus_value_realisee")
    ordering = ("date", "pk")


@admin.register(Holding)
class HoldingAdmin(ProprietaireAdmin):
    list_display = (
        "ticker_symbole", "nom_action", "compte_titre", "type_actif", "quantite_affichee", "pmpa_affiche",
        "cours_affiche", "valeur_affichee", "pv_latente", "perf", "pv_realisee",
    )
    list_filter = (("compte_titre", SeulementUtilises), "type_actif")
    search_fields = ("ticker_symbole", "nom_action", "isin")
    autocomplete_fields = ("compte_titre",)
    list_select_related = ("compte_titre",)
    readonly_fields = (
        "quantite", "pmpa", "cout_total", "plus_value_realisee", "dividendes_percus", "frais_totaux",
        "valeur_affichee", "pv_latente", "perf",
    )
    fieldsets = (
        ("Titre", {"fields": ("compte_titre", "ticker_symbole", "nom_action", "isin", "type_actif", "secteur")}),
        ("Cotation", {"fields": ("dernier_cours", "date_dernier_cours")}),
        (
            "Position (calculée)",
            {"fields": (
                "quantite", "pmpa", "cout_total", "valeur_affichee", "pv_latente", "perf",
                "plus_value_realisee", "dividendes_percus", "frais_totaux",
            )},
        ),
    )
    inlines = [InvestmentTransactionInline]
    actions = ["recalculer_positions"]

    def _devise(self, obj):
        return obj.compte_titre.devise

    @admin.display(description="Quantité", ordering="quantite")
    def quantite_affichee(self, obj):
        return f"{obj.quantite.normalize():f}"

    @admin.display(description="PMPA", ordering="pmpa")
    def pmpa_affiche(self, obj):
        return formater_montant(obj.pmpa, self._devise(obj), decimales=4)

    @admin.display(description="Cours", ordering="dernier_cours")
    def cours_affiche(self, obj):
        if obj.dernier_cours is None:
            return "—"
        return format_html(
            "{}<br><small style='color:#64748b'>{}</small>",
            formater_montant(obj.dernier_cours, self._devise(obj), decimales=4),
            obj.date_dernier_cours.strftime("%d/%m/%Y") if obj.date_dernier_cours else "",
        )

    @admin.display(description="Valeur de marché")
    def valeur_affichee(self, obj):
        return formater_montant(obj.valeur_marche, self._devise(obj))

    @admin.display(description="+/- value latente")
    def pv_latente(self, obj):
        return montant_colore(obj.plus_value_latente, self._devise(obj))

    @admin.display(description="Perf. latente")
    def perf(self, obj):
        pct = obj.performance_latente_pct
        if pct is None:
            return "—"
        couleur = "#15803d" if pct >= 0 else "#b91c1c"
        return format_html("<span style='color:{}'>{} %</span>", couleur, f"{pct:+.2f}".replace(".", ","))

    @admin.display(description="+/- value réalisée", ordering="plus_value_realisee")
    def pv_realisee(self, obj):
        return montant_colore(obj.plus_value_realisee, self._devise(obj))

    def save_model(self, request, obj, form, change):
        cours_modifie = "dernier_cours" in form.changed_data and obj.dernier_cours is not None
        if cours_modifie and not obj.date_dernier_cours:
            obj.date_dernier_cours = timezone.localdate()
        super().save_model(request, obj, form, change)
        if cours_modifie:
            CoursHistorique.objects.update_or_create(
                user_id=obj.user_id,
                ticker_symbole=obj.ticker_symbole,
                date=obj.date_dernier_cours,
                defaults={"cours": obj.dernier_cours},
            )

    @admin.action(description="Recalculer PMPA et plus-values")
    def recalculer_positions(self, request, queryset):
        for position in queryset:
            position.recalculer()
        self.message_user(request, f"{queryset.count()} position(s) recalculée(s).")


@admin.register(InvestmentTransaction)
class InvestmentTransactionAdmin(ProprietaireAdmin):
    date_hierarchy = "date"
    list_display = (
        "date", "type_operation", "titre", "compte_titre", "quantite", "prix_unitaire", "frais",
        "montant_net_affiche", "pv_affichee",
    )
    list_filter = ("type_operation", ("compte_titre", SeulementUtilises))
    search_fields = ("holding__ticker_symbole", "holding__nom_action", "notes")
    list_select_related = ("holding", "compte_titre")
    autocomplete_fields = ("holding",)
    fields = (
        "holding", "type_operation", "date", "quantite", "prix_unitaire", "frais", "notes",
        "compte_titre", "montant_net", "plus_value_realisee",
    )
    readonly_fields = ("compte_titre", "montant_net", "plus_value_realisee")

    @admin.display(description="Titre", ordering="holding__ticker_symbole")
    def titre(self, obj):
        return obj.holding

    @admin.display(description="Impact liquidités", ordering="montant_net")
    def montant_net_affiche(self, obj):
        return montant_colore(obj.montant_net, obj.compte_titre.devise)

    @admin.display(description="+/- value réalisée", ordering="plus_value_realisee")
    def pv_affichee(self, obj):
        return montant_colore(obj.plus_value_realisee, obj.compte_titre.devise)

    def delete_queryset(self, request, queryset):
        # Suppression unitaire pour déclencher le recalcul des positions.
        with db_transaction.atomic():
            for operation in queryset.select_related("holding"):
                operation.delete()


@admin.register(CoursHistorique)
class CoursHistoriqueAdmin(ProprietaireAdmin):
    list_display = ("ticker_symbole", "date", "cours")
    list_filter = ("ticker_symbole",)
    search_fields = ("ticker_symbole",)
    date_hierarchy = "date"


# ===========================================================================
# Budget
# ===========================================================================
class BudgetInline(ProprietaireInline):
    model = Budget
    fields = (
        "categorie", "sous_categorie", "montant_reference", "periodicite", "mois_reference",
        "lisser", "taux_indexation", "total_premiere_annee",
    )
    readonly_fields = ("total_premiere_annee",)
    autocomplete_fields = ("categorie", "sous_categorie")
    show_change_link = True

    @admin.display(description="Total année 1")
    def total_premiere_annee(self, obj):
        if not obj.pk:
            return "—"
        return formater_montant(obj.montant_annuel_theorique(obj.plan.annee_debut))


@admin.register(BudgetPlan)
class BudgetPlanAdmin(ProprietaireAdmin):
    list_display = ("nom", "periode", "taux_indexation", "actif", "nb_lignes", "depenses_an1", "revenus_an1")
    list_filter = ("actif",)
    search_fields = ("nom",)
    inlines = [BudgetInline]
    actions = ["activer", "regenerer", "regenerer_en_ecrasant"]

    def get_queryset(self, request):
        an1 = Q(lignes__montants__annee=F("annee_debut"))
        return (
            super()
            .get_queryset(request)
            .annotate(
                nb=Count("lignes", distinct=True),
                total_dep=Sum("lignes__montants__montant", filter=an1 & Q(lignes__categorie__type=TypeFlux.DEPENSE)),
                total_rev=Sum("lignes__montants__montant", filter=an1 & Q(lignes__categorie__type=TypeFlux.REVENU)),
            )
        )

    @admin.display(description="Période", ordering="annee_debut")
    def periode(self, obj):
        return f"{obj.annee_debut} – {obj.annee_fin}"

    @admin.display(description="Lignes", ordering="nb")
    def nb_lignes(self, obj):
        return obj.nb

    @admin.display(description="Dépenses an 1", ordering="total_dep")
    def depenses_an1(self, obj):
        return formater_montant(obj.total_dep or 0)

    @admin.display(description="Revenus an 1", ordering="total_rev")
    def revenus_an1(self, obj):
        return formater_montant(obj.total_rev or 0)

    @admin.action(description="Activer ce plan (désactive les autres)")
    def activer(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Sélectionnez exactement un plan.", messages.WARNING)
            return
        plan = queryset.get()
        with db_transaction.atomic():
            BudgetPlan.objects.filter(user=plan.user, actif=True).exclude(pk=plan.pk).update(actif=False)
            BudgetPlan.objects.filter(pk=plan.pk).update(actif=True)
        self.message_user(request, f"« {plan.nom} » est désormais le plan actif.")

    @admin.action(description="Recalculer les montants (garder les saisies manuelles)")
    def regenerer(self, request, queryset):
        for plan in queryset:
            plan.regenerer()
        self.message_user(request, f"{queryset.count()} plan(s) recalculé(s).")

    @admin.action(description="Recalculer les montants (écraser les saisies manuelles)")
    def regenerer_en_ecrasant(self, request, queryset):
        for plan in queryset:
            plan.regenerer(ecraser_manuels=True)
        self.message_user(request, f"{queryset.count()} plan(s) recalculé(s), saisies manuelles écrasées.")


class BudgetAllocationAdminForm(SaisieManuelleMixin, forms.ModelForm):
    class Meta:
        model = BudgetAllocation
        fields = ("montant", "saisi_manuellement")


class BudgetAllocationInline(admin.TabularInline):
    model = BudgetAllocation
    form = BudgetAllocationAdminForm
    fields = ("annee", "mois", "montant", "saisi_manuellement")
    readonly_fields = ("annee", "mois")
    can_delete = False
    extra = 0
    classes = ("collapse",)
    verbose_name_plural = "échéancier mensuel"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Budget)
class BudgetAdmin(ProprietaireAdmin):
    list_display = (
        "cible", "plan", "montant_reference", "periodicite", "lisser", "taux_affiche", "total_premiere_annee",
    )
    list_filter = (("plan", SeulementUtilises), "categorie__type", "periodicite")
    search_fields = ("categorie__nom", "sous_categorie__nom", "plan__nom")
    autocomplete_fields = ("plan", "categorie", "sous_categorie")
    list_select_related = ("plan", "categorie", "sous_categorie")
    inlines = [BudgetAllocationInline]

    @admin.display(description="Catégorie", ordering="categorie__nom")
    def cible(self, obj):
        sc = obj.sous_categorie
        return pastille(sc.couleur_effective if sc else obj.categorie.couleur, str(sc or obj.categorie))

    @admin.display(description="Indexation")
    def taux_affiche(self, obj):
        suffixe = "" if obj.taux_indexation is not None else " (plan)"
        return f"{obj.taux_effectif} %{suffixe}"

    @admin.display(description="Total année 1")
    def total_premiere_annee(self, obj):
        return formater_montant(obj.montant_annuel_theorique(obj.plan.annee_debut))
