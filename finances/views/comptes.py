from django.contrib.messages.views import SuccessMessageMixin
from django.core.paginator import Paginator
from django.db.models import Case, DecimalField, F, Sum, When, Window
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from ..forms import AccountForm
from ..models import Account, Transaction, TypeTransaction
from ..services.periodes import bornes_periode
from ..services.rapports import totaux_convertis
from .mixins import (
    FormulaireUtilisateurMixin,
    PaginationProfilMixin,
    ProprietaireMixin,
    RetourMixin,
    SuppressionProtegeeMixin,
)


class AccountListView(ProprietaireMixin, ListView):
    model = Account
    template_name = "finances/comptes/liste.html"
    context_object_name = "comptes"

    def get_queryset(self):
        archives = self.request.GET.get("archives") == "1"
        return super().get_queryset().filter(archive=archives).with_soldes()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["archives"] = self.request.GET.get("archives") == "1"
        ctx["nb_archives"] = Account.objects.for_user(self.request.user).filter(archive=True).count()
        return ctx


class AccountDetailView(ProprietaireMixin, PaginationProfilMixin, DetailView):
    """Fiche d'un compte : opérations paginées avec solde progressif."""

    model = Account
    template_name = "finances/comptes/detail.html"
    context_object_name = "compte"

    def get_queryset(self):
        return super().get_queryset().with_soldes()

    def operations(self):
        compte = self.object
        montant_compte = Case(
            When(compte=compte, type=TypeTransaction.REVENU, then=F("montant")),
            When(compte_destination=compte, then=F("montant_destination")),
            default=-F("montant"),
            output_field=DecimalField(max_digits=15, decimal_places=2),
        )
        return (
            Transaction.objects.for_user(self.request.user)
            .pour_compte(compte)
            .select_related("compte", "compte_destination", "categorie", "sous_categorie")
            .annotate(
                montant_compte=montant_compte,
                cumul=Window(Sum(montant_compte), order_by=[F("date").asc(), F("pk").asc()]),
            )
            .order_by("-date", "-pk")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        compte = self.object
        page = Paginator(self.operations(), self.get_paginate_by(None)).get_page(self.request.GET.get("page"))
        for op in page:
            # Les opérations sur titres ne sont pas incluses dans ce solde (cf. onglet portefeuille).
            op.solde_apres = compte.solde_initial + op.cumul
        ctx.update(
            page_obj=page,
            operations=page.object_list,
            flux_mois=totaux_convertis(
                Transaction.objects.for_user(self.request.user).filter(compte=compte).periode(*bornes_periode("mois")),
                compte.devise,
            ),
        )
        return ctx


class AccountCreateView(FormulaireUtilisateurMixin, RetourMixin, SuccessMessageMixin, CreateView):
    model = Account
    form_class = AccountForm
    template_name = "finances/comptes/formulaire.html"
    success_url = reverse_lazy("finances:comptes")
    success_message = "Compte « %(nom)s » créé."


class AccountUpdateView(ProprietaireMixin, FormulaireUtilisateurMixin, RetourMixin, SuccessMessageMixin, UpdateView):
    model = Account
    form_class = AccountForm
    template_name = "finances/comptes/formulaire.html"
    success_url = reverse_lazy("finances:comptes")
    success_message = "Compte « %(nom)s » mis à jour."


class AccountDeleteView(ProprietaireMixin, SuppressionProtegeeMixin, DeleteView):
    model = Account
    template_name = "finances/confirmer_suppression.html"
    success_url = reverse_lazy("finances:comptes")
    message_protection = "Ce compte a un historique d'opérations : archivez-le plutôt que de le supprimer."
    message_suppression = "Compte supprimé."
