from django.urls import path

from . import views

urlpatterns = [
    path("inscription/", views.InscriptionView.as_view(), name="inscription"),
    path("profil/", views.ProfilView.as_view(), name="profil"),
]
