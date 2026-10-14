from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Profile, TauxChange, User

admin.site.site_header = "PyFinance · Administration"
admin.site.site_title = "PyFinance"
admin.site.index_title = "Gestion des données"


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = "préférences"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "first_name", "devise", "is_staff", "is_active", "date_joined")
    list_select_related = ("profile",)
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "email", "password1", "password2")}),
    )

    def get_inlines(self, request, obj):
        # À la création, le profil est créé par signal : l'inline ne doit pas en ajouter un second.
        return [ProfileInline] if obj else []

    @admin.display(description="Devise", ordering="profile__devise_principale")
    def devise(self, obj):
        return obj.profile.devise_principale if hasattr(obj, "profile") else "—"


@admin.register(TauxChange)
class TauxChangeAdmin(admin.ModelAdmin):
    list_display = ("date", "devise_source", "devise_cible", "taux")
    list_filter = ("devise_source", "devise_cible")
    date_hierarchy = "date"
    ordering = ("-date", "devise_source", "devise_cible")
