from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DeleteView, DetailView, TemplateView, UpdateView

from ..forms import HoldingForm, InvestmentTransactionForm, MiseAJourCoursForm
from ..models import Account, CoursHistorique, Holding, InvestmentTransaction
from ..services.rapports import rapport_titres
from .mixins import (
    FormulaireUtilisateurMixin,
    ProprietaireMixin,
    RetourMixin,
    SuppressionProtegeeMixin,
    est_htmx,
    options_html,
    redirection,
    url_suivante,
)


def _compte_titre_demande(request):
    compte_id = request.GET.get("compte", "")
    if not compte_id.isdigit():
        return None
    return get_object_or_404(Account.objects.for_user(request.user).comptes_titres(), pk=compte_id)


class PortefeuilleView(TemplateView):
    """Positions ouvertes, liquidités et dernières opérations (tous comptes titres ou ?compte=<id>)."""

    template_name = "finances/titres/portefeuille.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        compte = _compte_titre_demande(self.request)
        operations = InvestmentTransaction.objects.for_user(self.request.user).select_related("holding", "compte_titre")
        if compte:
            operations = operations.filter(compte_titre=compte)
        ctx.update(
            compte=compte,
            comptes_titres=Account.objects.for_user(self.request.user).comptes_titres(),
            rapport=rapport_titres(self.request.user, compte),
            dernieres_operations=operations.order_by("-date", "-pk")[:15],
            form_cours=MiseAJourCoursForm(initial={"date": timezone.localdate()}),
        )
        return ctx


class HoldingDetailView(ProprietaireMixin, DetailView):
    model = Holding
    template_name = "finances/titres/position.html"
    context_object_name = "position"

    def get_queryset(self):
        return super().get_queryset().select_related("compte_titre")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        p = self.object
        ctx.update(
            operations=p.operations.select_related("compte_titre").order_by("-date", "-pk"),
            historique_cours=CoursHistorique.objects.for_user(self.request.user)
            .filter(ticker_symbole=p.ticker_symbole)
            .order_by("-date")[:120],
            form_cours=MiseAJourCoursForm(initial={"date": timezone.localdate(), "cours": p.dernier_cours}),
        )
        return ctx


class HoldingUpdateView(ProprietaireMixin, FormulaireUtilisateurMixin, RetourMixin, SuccessMessageMixin, UpdateView):
    model = Holding
    form_class = HoldingForm
    template_name = "finances/titres/formulaire_position.html"
    success_message = "Titre « %(ticker_symbole)s » mis à jour."

    def get_success_url(self):
        return reverse("finances:position", args=[self.object.pk])


class HoldingDeleteView(ProprietaireMixin, SuppressionProtegeeMixin, DeleteView):
    model = Holding
    template_name = "finances/confirmer_suppression.html"
    success_url = reverse_lazy("finances:portefeuille")
    message_protection = "Ce titre a des opérations : supprimez-les d'abord."
    message_suppression = "Titre supprimé."


class InvestmentTransactionFormMixin(FormulaireUtilisateurMixin, RetourMixin):
    model = InvestmentTransaction
    form_class = InvestmentTransactionForm
    template_name = "finances/titres/formulaire_operation.html"

    def get_success_url(self):
        return url_suivante(self.request, reverse("finances:position", args=[self.object.holding_id]))


class InvestmentTransactionCreateView(InvestmentTransactionFormMixin, CreateView):
    """Paramètres GET acceptés : compte (compte titres), position, type."""

    def get_initial(self):
        initial = super().get_initial()
        get = self.request.GET
        if get.get("compte", "").isdigit():
            initial["compte_titre"] = get["compte"]
        if get.get("position", "").isdigit():
            position = get_object_or_404(Holding.objects.for_user(self.request.user), pk=get["position"])
            initial.update(holding=position.pk, compte_titre=position.compte_titre_id)
        if get.get("type"):
            initial["type_operation"] = get["type"]
        return initial

    def form_valid(self, form):
        reponse = super().form_valid(form)
        op = self.object
        messages.success(
            self.request,
            f"{op.get_type_operation_display()} de {op.quantite.normalize():f} {op.ticker_symbole} enregistré(e).",
        )
        return reponse


class InvestmentTransactionUpdateView(ProprietaireMixin, InvestmentTransactionFormMixin, UpdateView):
    def form_valid(self, form):
        messages.success(self.request, "Opération mise à jour, position recalculée.")
        return super().form_valid(form)


class InvestmentTransactionDeleteView(ProprietaireMixin, RetourMixin, SuppressionProtegeeMixin, DeleteView):
    model = InvestmentTransaction
    template_name = "finances/confirmer_suppression.html"
    message_suppression = "Opération supprimée, position recalculée."

    def get_success_url(self):
        return url_suivante(self.request, reverse("finances:position", args=[self.object.holding_id]))


@require_POST
def mettre_a_jour_cours(request, pk):
    position = get_object_or_404(Holding.objects.for_user(request.user), pk=pk)
    form = MiseAJourCoursForm(request.POST)
    if form.is_valid():
        form.appliquer(position)
        if not est_htmx(request):
            messages.success(request, f"Cours de {position.ticker_symbole} mis à jour.")
    elif not est_htmx(request):
        messages.error(request, "Cours invalide.")
    if est_htmx(request):
        position.refresh_from_db()
        return render(request, "finances/titres/partials/cours.html", {"position": position, "form_cours": form})
    return redirection(request, url_suivante(request, reverse("finances:position", args=[pk])))


@require_GET
def options_positions(request):
    """<option> des positions d'un compte titres (?compte_titre=<id>)."""
    compte = request.GET.get("compte_titre", "")
    positions = Holding.objects.for_user(request.user)
    positions = positions.filter(compte_titre_id=compte) if compte.isdigit() else positions.none()
    return HttpResponse(options_html(
        positions,
        libelle=lambda h: f"{h.ticker_symbole} · {h.nom_action} ({h.quantite.normalize():f} titres)",
        vide="— Nouveau titre —",
        selection=request.GET.get("holding"),
    ))
