from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.db import transaction as db_transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_http_methods, require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from ..forms import BudgetAllocationForm, BudgetForm, BudgetPlanForm
from ..models import Budget, BudgetAllocation, BudgetPlan
from ..services.budget import VUES, construire_matrice
from .mixins import (
    FormulaireUtilisateurMixin,
    HtmxTemplateMixin,
    ProprietaireMixin,
    RetourMixin,
    est_htmx,
    redirection,
    url_suivante,
)


def budget_accueil(request):
    """Entrée « Budget » du menu : matrice du plan actif, sinon liste des plans."""
    plan = BudgetPlan.objects.for_user(request.user).filter(actif=True).first()
    if plan is None:
        return redirect("finances:budget_plans")
    parametres = request.GET.urlencode()  # conserve ?vue=…&annee=…
    return redirect(reverse("finances:budget_matrice", args=[plan.pk]) + (f"?{parametres}" if parametres else ""))


# --- Plans -------------------------------------------------------------------
class BudgetPlanListView(ProprietaireMixin, ListView):
    model = BudgetPlan
    template_name = "finances/budget/plans.html"
    context_object_name = "plans"


class BudgetPlanCreateView(FormulaireUtilisateurMixin, SuccessMessageMixin, CreateView):
    model = BudgetPlan
    form_class = BudgetPlanForm
    template_name = "finances/budget/formulaire_plan.html"
    success_message = "Plan « %(nom)s » créé : ajoutez maintenant ses lignes budgétaires."

    def get_success_url(self):
        return reverse("finances:budget_matrice", args=[self.object.pk])


class BudgetPlanUpdateView(ProprietaireMixin, FormulaireUtilisateurMixin, SuccessMessageMixin, UpdateView):
    model = BudgetPlan
    form_class = BudgetPlanForm
    template_name = "finances/budget/formulaire_plan.html"
    success_message = "Plan « %(nom)s » mis à jour."

    def get_success_url(self):
        return reverse("finances:budget_matrice", args=[self.object.pk])


class BudgetPlanDeleteView(ProprietaireMixin, DeleteView):
    model = BudgetPlan
    template_name = "finances/confirmer_suppression.html"
    success_url = reverse_lazy("finances:budget_plans")

    def form_valid(self, form):
        messages.success(self.request, f"Plan « {self.object.nom} » supprimé.")
        return super().form_valid(form)


@require_POST
def activer_plan(request, pk):
    plan = get_object_or_404(BudgetPlan.objects.for_user(request.user), pk=pk)
    with db_transaction.atomic():
        BudgetPlan.objects.for_user(request.user).exclude(pk=pk).update(actif=False)
        BudgetPlan.objects.filter(pk=pk).update(actif=True)
    messages.success(request, f"« {plan.nom} » est désormais le plan actif.")
    return redirect("finances:budget_matrice", pk=pk)


@require_POST
def regenerer_plan(request, pk):
    """Recalcule tous les montants ; ?ecraser=1 remplace aussi les saisies manuelles."""
    plan = get_object_or_404(BudgetPlan.objects.for_user(request.user), pk=pk)
    ecraser = request.POST.get("ecraser") == "1"
    plan.regenerer(ecraser_manuels=ecraser)
    messages.success(
        request, "Montants recalculés" + (", saisies manuelles écrasées." if ecraser else ", saisies manuelles conservées.")
    )
    return redirect("finances:budget_matrice", pk=pk)


# --- Matrice Réel vs Prévisionnel ---------------------------------------------
class BudgetMatriceView(ProprietaireMixin, HtmxTemplateMixin, DetailView):
    """
    ?vue=mensuelle|annuelle|quinquennale & ?annee=AAAA.
    Requête HTMX : seule la matrice est renvoyée (changement de vue sans rechargement,
    ou rafraîchissement après l'édition d'une cellule).
    """

    model = BudgetPlan
    template_name = "finances/budget/matrice.html"
    partial_template_name = "finances/budget/partials/matrice.html"
    context_object_name = "plan"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        annee = self.request.GET.get("annee", "")
        matrice = construire_matrice(
            self.object,
            vue=self.request.GET.get("vue", "mensuelle"),
            annee=int(annee) if annee.isdigit() else None,
            seuil=self.request.user.profile.seuil_alerte_budget,
        )
        ctx.update(
            matrice=matrice,
            vues=VUES,
            plans=BudgetPlan.objects.for_user(self.request.user),
            alertes=matrice.alertes(),
        )
        return ctx


# --- Lignes budgétaires --------------------------------------------------------
class BudgetLigneMixin(FormulaireUtilisateurMixin, RetourMixin):
    model = Budget
    form_class = BudgetForm
    template_name = "finances/budget/formulaire_ligne.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["plan"] = self.plan
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["plan"] = self.plan
        return ctx

    def get_success_url(self):
        return url_suivante(self.request, reverse("finances:budget_matrice", args=[self.plan.pk]))


class BudgetLigneCreateView(BudgetLigneMixin, CreateView):
    def dispatch(self, request, *args, **kwargs):
        self.plan = get_object_or_404(BudgetPlan.objects.for_user(request.user), pk=kwargs["plan_pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {"categorie": self.request.GET.get("categorie"), "sous_categorie": self.request.GET.get("sous_categorie")}

    def form_valid(self, form):
        messages.success(self.request, "Ligne budgétaire ajoutée.")
        return super().form_valid(form)


class BudgetLigneUpdateView(ProprietaireMixin, BudgetLigneMixin, UpdateView):
    def get_object(self, queryset=None):
        ligne = super().get_object(queryset)
        self.plan = ligne.plan
        return ligne

    def form_valid(self, form):
        messages.success(self.request, "Ligne budgétaire mise à jour (saisies manuelles conservées).")
        return super().form_valid(form)


class BudgetLigneDeleteView(ProprietaireMixin, RetourMixin, DeleteView):
    model = Budget
    template_name = "finances/confirmer_suppression.html"

    def get_success_url(self):
        return reverse("finances:budget_matrice", args=[self.object.plan_id])

    def form_valid(self, form):
        messages.success(self.request, "Ligne budgétaire supprimée.")
        return super().form_valid(form)


# --- Cellules (édition en place, HTMX) -------------------------------------------
def _rendu_cellule(request, allocation):
    reponse = render(request, "finances/budget/partials/cellule.html", {
        "allocation_id": allocation.pk,
        "montant": allocation.montant,
        "manuel": allocation.saisi_manuellement,
    })
    reponse["HX-Trigger"] = "budget-modifie"
    return reponse


def _allocation(request, pk):
    return get_object_or_404(BudgetAllocation.objects.for_user(request.user).select_related("budget__plan"), pk=pk)


@require_http_methods(["GET", "POST"])
def editer_cellule(request, pk):
    """
    GET : formulaire en place ; POST : enregistre (marque la cellule « saisie manuelle »).
    Réponse HTMX : la cellule + l'événement `budget-modifie` pour recharger les totaux.
    """
    allocation = _allocation(request, pk)
    form = BudgetAllocationForm(request.POST or None, instance=allocation)
    if request.method == "POST" and form.is_valid():
        allocation = form.save()
        if not est_htmx(request):
            return redirect("finances:budget_matrice", pk=allocation.budget.plan_id)
        return _rendu_cellule(request, allocation)
    return render(request, "finances/budget/partials/cellule_formulaire.html", {"allocation": allocation, "form": form})


@require_POST
def reinitialiser_cellule(request, pk):
    """Revient au montant calculé (périodicité + indexation) et retire le marquage manuel."""
    allocation = _allocation(request, pk)
    allocation.montant = allocation.budget.montant_theorique(allocation.annee, allocation.mois)
    allocation.saisi_manuellement = False
    allocation.save(update_fields=["montant", "saisi_manuellement"])
    if est_htmx(request):
        return _rendu_cellule(request, allocation)
    return redirection(request, reverse("finances:budget_matrice", args=[allocation.budget.plan_id]))
