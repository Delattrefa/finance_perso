from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.contrib.messages.views import SuccessMessageMixin
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.generic import CreateView, UpdateView

from finances.services.categories_defaut import creer_categories_par_defaut

from .forms import InscriptionForm, ProfileForm


@method_decorator(login_not_required, name="dispatch")
class InscriptionView(CreateView):
    form_class = InscriptionForm
    template_name = "registration/inscription.html"
    success_url = reverse_lazy("finances:dashboard")

    def form_valid(self, form):
        reponse = super().form_valid(form)
        creer_categories_par_defaut(self.object)
        login(self.request, self.object)
        messages.success(self.request, "Bienvenue ! Un jeu de catégories a été créé : commencez par ajouter vos comptes.")
        return reponse


class ProfilView(SuccessMessageMixin, UpdateView):
    form_class = ProfileForm
    template_name = "core/profil.html"
    success_url = reverse_lazy("profil")
    success_message = "Préférences enregistrées."

    def get_object(self, queryset=None):
        return self.request.user.profile
