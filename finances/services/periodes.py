"""Calcul des bornes de périodes usuelles (recherche, rapports, comparatifs)."""
from __future__ import annotations

import calendar
from datetime import date

from django.utils import timezone

PERIODES = [
    ("", "Toutes les dates"),
    ("mois", "Mois en cours"),
    ("mois_prec", "Mois précédent"),
    ("3m", "3 derniers mois"),
    ("annee", "Année en cours"),
    ("annee_prec", "Année précédente"),
    ("12m", "12 derniers mois"),
    ("perso", "Personnalisée"),
]


def fin_de_mois(annee: int, mois: int) -> date:
    return date(annee, mois, calendar.monthrange(annee, mois)[1])


def decaler_mois(jour: date, nb_mois: int) -> date:
    """Premier jour du mois situé `nb_mois` avant (négatif) ou après `jour`."""
    index = jour.year * 12 + jour.month - 1 + nb_mois
    return date(index // 12, index % 12 + 1, 1)


def bornes_periode(code: str, reference: date | None = None) -> tuple[date | None, date | None]:
    """Renvoie (début, fin) inclus pour un code de PERIODES ; (None, None) si non borné."""
    jour = reference or timezone.localdate()
    if code == "mois":
        return jour.replace(day=1), fin_de_mois(jour.year, jour.month)
    if code == "mois_prec":
        debut = decaler_mois(jour, -1)
        return debut, fin_de_mois(debut.year, debut.month)
    if code == "3m":
        return decaler_mois(jour, -2), fin_de_mois(jour.year, jour.month)
    if code == "annee":
        return date(jour.year, 1, 1), date(jour.year, 12, 31)
    if code == "annee_prec":
        return date(jour.year - 1, 1, 1), date(jour.year - 1, 12, 31)
    if code == "12m":
        return decaler_mois(jour, -11), fin_de_mois(jour.year, jour.month)
    return None, None


MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
MOIS_LONGS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def libelle_mois(annee: int, mois: int, court: bool = False) -> str:
    noms = MOIS_COURTS if court else MOIS_LONGS
    return f"{noms[mois - 1]} {annee}"


def meme_jour_annee_precedente(jour: date) -> date:
    try:
        return jour.replace(year=jour.year - 1)
    except ValueError:  # 29 février
        return jour.replace(year=jour.year - 1, day=28)


def fins_de_mois(nb_mois: int, reference: date | None = None) -> list[date]:
    """Dernier jour des `nb_mois` derniers mois ; le mois en cours est arrêté à `reference`."""
    jour = reference or timezone.localdate()
    fins = []
    for decalage in range(-(nb_mois - 1), 1):
        debut = decaler_mois(jour, decalage)
        fins.append(min(fin_de_mois(debut.year, debut.month), jour))
    return fins
