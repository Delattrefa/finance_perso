import re
from decimal import Decimal

from django import template
from django.utils.formats import number_format
from django.utils.html import format_html

from core.formatage import formater_montant

register = template.Library()

STATUTS = {
    # statut de cellule budgétaire → (classes Tailwind, libellé)
    "depassement": ("bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-300", "Dépassement"),
    "alerte": ("bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300", "Proche du budget"),
    "insuffisant": ("bg-orange-50 text-orange-700 dark:bg-orange-950/50 dark:text-orange-300", "Sous l'objectif"),
    "ok": ("text-emerald-700 dark:text-emerald-400", "Conforme"),
    "a_venir": ("text-slate-400 dark:text-slate-500", "À venir"),
    "vide": ("text-slate-300 dark:text-slate-600", ""),
}
ICONE_LUCIDE = re.compile(r"^[a-z0-9-]+$")


@register.filter
def monnaie(valeur, devise="EUR"):
    """{{ montant|monnaie:"EUR" }} → 1 234,56 €"""
    return formater_montant(valeur, devise or "EUR")


@register.filter
def monnaie_signe(valeur, devise="EUR"):
    """{{ montant|monnaie_signe }} → +1 234,56 € / −12,00 €"""
    return formater_montant(valeur, devise or "EUR", signe=True)


@register.filter
def monnaie_courte(valeur, devise="EUR"):
    """Sans décimales (cellules de matrice)."""
    return formater_montant(valeur, devise or "EUR", decimales=0)


@register.filter
def pct(valeur, decimales=1):
    if valeur is None or valeur == "":
        return "—"
    return f"{number_format(Decimal(valeur), decimal_pos=int(decimales), use_l10n=True)} %"


@register.filter
def pct_signe(valeur):
    if valeur is None:
        return "—"
    texte = pct(valeur)
    return f"+{texte}" if Decimal(valeur) > 0 else texte


@register.filter
def couleur_montant(valeur, inverse=False):
    """Classe de couleur d'un montant signé ; `inverse` pour une dépense (hausse = défavorable)."""
    if valeur is None or valeur == 0:
        return "text-slate-500 dark:text-slate-400"
    positif = (valeur > 0) != bool(inverse)
    return "text-emerald-600 dark:text-emerald-400" if positif else "text-rose-600 dark:text-rose-400"


@register.filter
def classes_statut(statut):
    return STATUTS.get(statut, ("", ""))[0]


@register.filter
def libelle_statut(statut):
    return STATUTS.get(statut, ("", ""))[1]


@register.filter
def get(dictionnaire, cle):
    return dictionnaire.get(cle) if hasattr(dictionnaire, "get") else None


@register.filter
def absolu(valeur):
    return abs(valeur) if valeur is not None else None


@register.filter
def quantite(valeur):
    """Quantité sans zéros superflus : 10.00000000 → 10"""
    if valeur is None:
        return "—"
    return number_format(Decimal(valeur).normalize(), use_l10n=True, force_grouping=True)


@register.filter
def largeur_pct(valeur, maximum=100):
    """Largeur CSS bornée à [0, 100] % pour les jauges."""
    if not valeur:
        return "0%"
    return f"{max(0, min(100, float(valeur) / float(maximum) * 100)):.1f}%"


@register.simple_tag
def icone(nom, classes="h-4 w-4"):
    """Icône Lucide (nom kebab-case) ou emoji / texte libre."""
    if not nom:
        return ""
    if ICONE_LUCIDE.match(nom):
        return format_html('<i data-lucide="{}" class="{}" aria-hidden="true"></i>', nom, classes)
    return format_html('<span class="{}" aria-hidden="true">{}</span>', classes, nom)


@register.inclusion_tag("components/champ.html")
def champ(field, classe=""):
    """Rend un champ : libellé, widget, aide et erreurs."""
    widget = field.field.widget.__class__.__name__
    return {
        "field": field,
        "classe": classe,
        "est_case": widget == "CheckboxInput",
        "est_choix_multiples": widget in ("CheckboxSelectMultiple", "RadioSelect"),
    }


@register.simple_tag(takes_context=True)
def parametres_page(context, page):
    """Querystring courant avec `page` remplacé (pagination qui conserve les filtres)."""
    requete = context["request"].GET.copy()
    requete["page"] = page
    requete.pop("export", None)
    return requete.urlencode()


@register.filter
def commence_par(texte, prefixes):
    """Vrai si `texte` commence par l'un des préfixes (séparés par des espaces)."""
    return bool(texte) and any(texte.startswith(p) for p in str(prefixes).split())


@register.filter
def montant_pour(operation, compte=None):
    """Montant signé d'une opération du point de vue d'un compte (ou de son compte principal)."""
    return operation.montant_signe(compte or None)


@register.filter
def index(sequence, i):
    try:
        return sequence[int(i)]
    except (IndexError, TypeError, ValueError):
        return None


@register.filter
def classe_grille(field):
    """Les zones de texte et choix multiples occupent toute la largeur de la grille."""
    pleine = ("Textarea", "CheckboxSelectMultiple", "SelectMultiple", "CheckboxInput")
    return "sm:col-span-2" if field.field.widget.__class__.__name__ in pleine else ""


@register.filter
def valeur_saisie(valeur):
    """Décimal prêt à être ressaisi : 152.400000 → « 152,4 » (vide si None)."""
    if valeur is None or valeur == "":
        return ""
    try:
        texte = format(Decimal(valeur).normalize(), "f")
    except ArithmeticError:  # saisie invalide renvoyée telle quelle
        return valeur
    return texte.replace(".", ",")


@register.simple_tag(takes_context=True)
def url_retour(context, defaut="/"):
    """Destination du bouton « Annuler » : `next`, sinon la page précédente (même site), sinon `defaut`."""
    from django.utils.http import url_has_allowed_host_and_scheme

    requete = context["request"]
    for candidat in (context.get("next"), requete.META.get("HTTP_REFERER")):
        if candidat and url_has_allowed_host_and_scheme(candidat, {requete.get_host()}, requete.is_secure()):
            return candidat
    return defaut
