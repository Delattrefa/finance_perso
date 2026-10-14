from django.contrib import messages
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count, Prefetch
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from ..forms import CategoryForm, SubCategoryForm
from ..models import Category, SubCategory, TypeFlux
from ..services.categories_defaut import creer_categories_par_defaut
from .mixins import (
    FormulaireUtilisateurMixin,
    ProprietaireMixin,
    RetourMixin,
    SuppressionProtegeeMixin,
    options_html,
)


class CategoryListView(ProprietaireMixin, ListView):
    model = Category
    template_name = "finances/categories/liste.html"
    context_object_name = "categories"

    def get_queryset(self):
        sous_categories = SubCategory.objects.annotate(nb_operations=Count("transactions")).order_by("ordre", "nom")
        return (
            super()
            .get_queryset()
            .annotate(nb_operations=Count("transactions"))
            .prefetch_related(Prefetch("sous_categories", queryset=sous_categories))
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        categories = list(ctx["categories"])
        ctx["depenses"] = [c for c in categories if c.type == TypeFlux.DEPENSE]
        ctx["revenus"] = [c for c in categories if c.type == TypeFlux.REVENU]
        return ctx


class CategoryCreateView(FormulaireUtilisateurMixin, RetourMixin, SuccessMessageMixin, CreateView):
    model = Category
    form_class = CategoryForm
    template_name = "finances/categories/formulaire.html"
    success_url = reverse_lazy("finances:categories")
    success_message = "Catégorie « %(nom)s » créée."

    def get_initial(self):
        return {"type": self.request.GET.get("type", TypeFlux.DEPENSE)}


class CategoryUpdateView(ProprietaireMixin, FormulaireUtilisateurMixin, RetourMixin, SuccessMessageMixin, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = "finances/categories/formulaire.html"
    success_url = reverse_lazy("finances:categories")
    success_message = "Catégorie « %(nom)s » mise à jour."


class CategoryDeleteView(ProprietaireMixin, SuppressionProtegeeMixin, DeleteView):
    model = Category
    template_name = "finances/confirmer_suppression.html"
    success_url = reverse_lazy("finances:categories")
    message_protection = "Des opérations utilisent cette catégorie : archivez-la plutôt."
    message_suppression = "Catégorie supprimée."


class SubCategoryCreateView(FormulaireUtilisateurMixin, RetourMixin, SuccessMessageMixin, CreateView):
    model = SubCategory
    form_class = SubCategoryForm
    template_name = "finances/categories/formulaire_sous_categorie.html"
    success_url = reverse_lazy("finances:categories")
    success_message = "Sous-catégorie « %(nom)s » créée."

    def get_initial(self):
        return {"categorie": self.request.GET.get("categorie")}


class SubCategoryUpdateView(ProprietaireMixin, FormulaireUtilisateurMixin, RetourMixin, SuccessMessageMixin, UpdateView):
    model = SubCategory
    form_class = SubCategoryForm
    template_name = "finances/categories/formulaire_sous_categorie.html"
    success_url = reverse_lazy("finances:categories")
    success_message = "Sous-catégorie « %(nom)s » mise à jour."


class SubCategoryDeleteView(ProprietaireMixin, SuppressionProtegeeMixin, DeleteView):
    model = SubCategory
    template_name = "finances/confirmer_suppression.html"
    success_url = reverse_lazy("finances:categories")
    message_protection = "Des opérations utilisent cette sous-catégorie : archivez-la plutôt."
    message_suppression = "Sous-catégorie supprimée."


@require_POST
def categories_par_defaut(request):
    n = creer_categories_par_defaut(request.user)
    messages.success(request, f"{n} catégorie(s) et sous-catégorie(s) ajoutée(s)." if n else "Rien à ajouter.")
    return redirect("finances:categories")


# --- Listes dépendantes (HTMX) ---------------------------------------------
@require_GET
def options_categories(request):
    """<option> des catégories actives d'un type (?type=DEPENSE|REVENU)."""
    categories = Category.objects.for_user(request.user).filter(archive=False)
    type_flux = request.GET.get("type")
    if type_flux in TypeFlux.values:
        categories = categories.filter(type=type_flux)
    else:
        categories = categories.none()
    return HttpResponse(options_html(categories, selection=request.GET.get("categorie")))


@require_GET
def options_sous_categories(request):
    """<option> des sous-catégories actives d'une catégorie (?categorie=<id>)."""
    categorie = request.GET.get("categorie", "")
    sous_categories = SubCategory.objects.for_user(request.user).filter(archive=False)
    sous_categories = sous_categories.filter(categorie_id=categorie) if categorie.isdigit() else sous_categories.none()
    return HttpResponse(
        options_html(sous_categories, libelle=lambda sc: sc.nom, selection=request.GET.get("sous_categorie"))
    )
