from decimal import Decimal

from django.utils.formats import number_format

SYMBOLES_DEVISE = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "CAD": "CA$", "JPY": "¥"}


def formater_montant(valeur, devise: str = "EUR", signe: bool = False, decimales: int = 2) -> str:
    """1234.5 → « 1 234,50 € » (format localisé) ; `signe` force le « + » des montants positifs."""
    if valeur is None:
        return "—"
    valeur = Decimal(valeur)
    texte = number_format(valeur, decimal_pos=decimales, use_l10n=True, force_grouping=True)
    if signe and valeur > 0:
        texte = f"+{texte}"
    return f"{texte} {SYMBOLES_DEVISE.get(devise, devise)}"
