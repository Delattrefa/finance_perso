from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("compte/", include("django.contrib.auth.urls")),  # login, logout, mot de passe
    path("compte/", include("core.urls")),
    path("", include("finances.urls")),
]
