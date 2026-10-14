def preferences(request):
    """Devise principale et thème disponibles dans tous les templates."""
    profil = getattr(getattr(request, "user", None), "profile", None)
    return {
        "devise": profil.devise_principale if profil else "EUR",
        "theme": profil.theme if profil else "SYSTEME",
    }
