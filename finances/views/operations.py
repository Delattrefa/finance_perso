from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, UpdateView

from ..forms import TransactionForm
from ..models import Account, StatutTransaction, Transaction
from .mixins import (
    FormulaireUtilisateurMixin,
    ProprietaireMixin,
    RetourMixin,
    SuppressionProtegeeMixin,
    est_htmx,
    redirection,
    url_suivante,
)


class TransactionFormMixin(FormulaireUtilisateurMixin, RetourMixin):
    model = Transaction
    form_class = TransactionForm
    template_name = "finances/operations/formulaire.html"
    success_url = reverse_lazy("finances:recherche")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["suggestions_tiers"] = TransactionForm.suggestions_tiers(self.request.user)
        return ctx


class TransactionCreateView(TransactionFormMixin, CreateView):
    """
    Saisie rapide. Paramètres GET acceptés : compte, type, date, categorie,
    ou depuis=<id> pour dupliquer une opération existante (à la date du jour).
    « Enregistrer et saisir une autre » (_autre) conserve compte, type et date.
    """

    def get_initial(self):
        initial = super().get_initial()
        source_id = self.request.GET.get("depuis", "")
        if source_id.isdigit():
            source = get_object_or_404(Transaction.objects.for_user(self.request.user), pk=source_id)
            for champ in (
                "type", "compte", "compte_destination", "montant", "montant_destination",
                "categorie", "sous_categorie", "tiers_payeur", "description",
            ):
                valeur = getattr(source, champ)
                initial[champ] = getattr(valeur, "pk", valeur)
        for champ in ("compte", "type", "date", "categorie"):
            if self.request.GET.get(champ):
                initial[champ] = self.request.GET[champ]
        if "compte" not in initial:
            # Par défaut : le compte de la dernière opération saisie, sinon le premier compte actif.
            derniere = Transaction.objects.for_user(self.request.user).order_by("-created_at").first()
            compte = derniere.compte if derniere and not derniere.compte.archive else (
                Account.objects.for_user(self.request.user).actifs().first()
            )
            if compte:
                initial["compte"] = compte.pk
        return initial

    def form_valid(self, form):
        reponse = super().form_valid(form)
        t = self.object
        messages.success(self.request, f"Opération enregistrée : {t.get_type_display().lower()} de {t.montant} {t.compte.devise}.")
        if "_autre" in self.request.POST:
            params = urlencode({"compte": t.compte_id, "type": t.type, "date": t.date.isoformat()})
            return redirection(self.request, f"{reverse('finances:operation_ajouter')}?{params}")
        return reponse


class TransactionUpdateView(ProprietaireMixin, TransactionFormMixin, UpdateView):
    def form_valid(self, form):
        messages.success(self.request, "Opération mise à jour.")
        return super().form_valid(form)


class TransactionDeleteView(ProprietaireMixin, RetourMixin, SuppressionProtegeeMixin, DeleteView):
    model = Transaction
    template_name = "finances/confirmer_suppression.html"
    success_url = reverse_lazy("finances:recherche")
    message_suppression = "Opération supprimée."


@require_POST
def basculer_statut(request, pk):
    """Rapproché ⇄ en attente (bouton HTMX sur chaque ligne)."""
    operation = get_object_or_404(Transaction.objects.for_user(request.user), pk=pk)
    operation.statut = (
        StatutTransaction.EN_ATTENTE if operation.statut == StatutTransaction.RAPPROCHE else StatutTransaction.RAPPROCHE
    )
    operation.save(update_fields=["statut", "updated_at"])
    if est_htmx(request):
        return render(request, "finances/partials/statut_operation.html", {"operation": operation})
    return redirection(request, url_suivante(request, reverse("finances:recherche")))
