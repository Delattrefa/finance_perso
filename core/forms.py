"""
Socle des formulaires : style Tailwind et rattachement automatique à l'utilisateur.
"""
import re

from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import Profile, User

CLASSE_CHAMP = (
    "block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 "
    "shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/30 "
    "dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"
)
CLASSE_CASE = "h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
CLASSE_COULEUR = "h-10 w-16 cursor-pointer rounded-lg border border-slate-300 bg-white p-1"
CLASSE_ERREUR = "border-rose-500 focus:border-rose-500 focus:ring-rose-500/30"


class DateInput(forms.DateInput):
    """Sélecteur de date HTML5 (le format ISO est imposé par le navigateur)."""

    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs=attrs, format="%Y-%m-%d")


class ColorInput(forms.TextInput):
    input_type = "color"


class DecimalSaisieField(forms.DecimalField):
    """Accepte « 1 234,56 », « 1 234,56 » ou « 1'234.56 » : séparateurs de milliers retirés avant conversion."""

    def to_python(self, value):
        if isinstance(value, str):
            value = re.sub(r"[\s\u00a0\u202f']", "", value)
        return super().to_python(value)


class TailwindFormMixin:
    """Applique les classes Tailwind à chaque widget, et localise les champs décimaux (« 12,50 »)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for champ in self.fields.values():
            widget = champ.widget
            if isinstance(champ, forms.DecimalField):
                if type(champ) is forms.DecimalField:
                    champ.__class__ = DecimalSaisieField
                champ.localize = True
                widget.is_localized = True
                if isinstance(widget, forms.NumberInput):
                    # Un input « number » refuse la virgule : on passe en texte avec clavier décimal.
                    champ.widget = widget = forms.TextInput(attrs={**widget.attrs, "inputmode": "decimal"})
            if isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                # Django applique la classe au conteneur ET à chaque case : style posé par le gabarit.
                continue
            if isinstance(widget, forms.CheckboxInput):
                classe = CLASSE_CASE
            elif isinstance(widget, ColorInput):
                classe = CLASSE_COULEUR
            else:
                classe = CLASSE_CHAMP
            widget.attrs["class"] = f"{classe} {widget.attrs.get('class', '')}".strip()

    def full_clean(self):
        super().full_clean()
        for nom in self.errors:
            if nom in self.fields:
                attrs = self.fields[nom].widget.attrs
                attrs["class"] = f"{attrs.get('class', '')} {CLASSE_ERREUR}".strip()
                attrs["aria-invalid"] = "true"


class UserScopedModelForm(TailwindFormMixin, forms.ModelForm):
    """
    ModelForm lié à l'utilisateur connecté :
    - `user` (obligatoire) est affecté à l'instance avant toute validation ;
    - `user` et `champs_toujours_valides` sont réintégrés dans la validation du modèle,
      pour que les contraintes d'unicité (user, nom…) produisent une erreur de formulaire
      plutôt qu'une IntegrityError ;
    - les querysets de clés étrangères sont restreints via `restreindre()`.
    """

    champs_toujours_valides: tuple[str, ...] = ("user",)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if not self.instance.user_id:
            self.instance.user = user

    def restreindre(self, nom_champ: str, queryset) -> None:
        if nom_champ in self.fields:
            self.fields[nom_champ].queryset = queryset

    def valeur_courante(self, nom_champ: str):
        """Valeur soumise si le formulaire est lié, sinon valeur initiale / de l'instance."""
        if self.is_bound:
            return self.data.get(self.add_prefix(nom_champ)) or None
        if nom_champ in self.initial:
            valeur = self.initial[nom_champ]
            return getattr(valeur, "pk", valeur)
        return getattr(self.instance, f"{nom_champ}_id", None) or getattr(self.instance, nom_champ, None)

    def _get_validation_exclusions(self):
        exclusions = super()._get_validation_exclusions()
        for champ in self.champs_toujours_valides:
            exclusions.discard(champ)
        return exclusions


# ---------------------------------------------------------------------------
# Compte utilisateur
# ---------------------------------------------------------------------------
class InscriptionForm(TailwindFormMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "first_name")
        labels = {"first_name": "Prénom"}


class ProfileForm(TailwindFormMixin, forms.ModelForm):
    class Meta:
        model = Profile
        fields = ("devise_principale", "theme", "objectif_taux_epargne", "seuil_alerte_budget", "elements_par_page")
