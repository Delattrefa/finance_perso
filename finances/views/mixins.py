"""
Briques communes des vues.

L'authentification est imposée globalement par LoginRequiredMiddleware (settings) :
les vues n'ont pas besoin de LoginRequiredMixin.
"""
from __future__ import annotations

from django.contrib import messages
from django.db.models import ProtectedError, RestrictedError
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect
from django.utils.html import format_html, format_html_join
from django.utils.http import url_has_allowed_host_and_scheme


def est_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def redirection(request, url: str) -> HttpResponse:
    """Redirection compatible HTMX (HX-Redirect) et classique."""
    if est_htmx(request):
        return HttpResponse(status=204, headers={"HX-Redirect": url})
    return HttpResponseRedirect(url)


def url_suivante(request, defaut: str) -> str:
    """Paramètre `next` (POST ou GET) s'il est sûr, sinon `defaut`."""
    suivante = request.POST.get("next") or request.GET.get("next")
    if suivante and url_has_allowed_host_and_scheme(suivante, {request.get_host()}, request.is_secure()):
        return suivante
    return defaut


def options_html(objets, libelle=str, vide: str | None = "---------", selection=None) -> str:
    """Balises <option> (réponses HTMX des listes dépendantes)."""
    vide_html = format_html('<option value="">{}</option>', vide) if vide is not None else ""
    selection = str(selection) if selection is not None else None
    return vide_html + format_html_join(
        "",
        '<option value="{}"{}>{}</option>',
        ((o.pk, " selected" if str(o.pk) == selection else "", libelle(o)) for o in objets),
    )


class ProprietaireMixin:
    """Restreint le queryset aux objets de l'utilisateur : les autres donnent une 404."""

    def get_queryset(self):
        return self.model.objects.for_user(self.request.user)


class FormulaireUtilisateurMixin:
    """Transmet l'utilisateur aux formulaires de finances.forms."""

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class HtmxTemplateMixin:
    """Rend `partial_template_name` (fragment) pour une requête HTMX."""

    partial_template_name: str | None = None

    def get_template_names(self):
        if self.partial_template_name and est_htmx(self.request):
            return [self.partial_template_name]
        return super().get_template_names()


class PaginationProfilMixin:
    """Taille de page issue des préférences de l'utilisateur."""

    def get_paginate_by(self, queryset):
        profil = getattr(self.request.user, "profile", None)
        return profil.elements_par_page if profil else 50


class RetourMixin:
    """Après enregistrement : `next` si fourni, sinon `success_url` / get_absolute_url."""

    def get_success_url(self):
        return url_suivante(self.request, super().get_success_url())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.setdefault("next", self.request.GET.get("next", ""))
        return ctx


class SuppressionProtegeeMixin:
    """Une suppression bloquée par l'historique devient un message plutôt qu'une erreur 500."""

    message_protection = "Suppression impossible : cet élément est utilisé par des opérations. Archivez-le plutôt."
    message_suppression = "Élément supprimé."

    def form_valid(self, form):
        try:
            reponse = super().form_valid(form)
        except (ProtectedError, RestrictedError):
            messages.error(self.request, self.message_protection)
            return redirect(self.get_success_url())
        messages.success(self.request, self.message_suppression)
        return reponse


def message_formulaire(objet, action: str) -> str:
    return format_html("« {} » {}.", objet, action)
