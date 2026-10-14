"""
Socle transverse de PyFinance : utilisateur, profil, classes de base et devises.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class Devise(models.TextChoices):
    EUR = "EUR", "Euro (€)"
    USD = "USD", "Dollar US ($)"
    GBP = "GBP", "Livre sterling (£)"
    CHF = "CHF", "Franc suisse (CHF)"
    CAD = "CAD", "Dollar canadien (CA$)"
    JPY = "JPY", "Yen (¥)"


# ---------------------------------------------------------------------------
# Classes de base
# ---------------------------------------------------------------------------
class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("créé le", auto_now_add=True)
    updated_at = models.DateTimeField("modifié le", auto_now=True)

    class Meta:
        abstract = True


class UserOwnedQuerySet(models.QuerySet):
    def for_user(self, user):
        """Point d'entrée obligatoire des vues : restreint aux données de l'utilisateur."""
        return self.filter(user=user)


class UserOwnedModel(TimeStampedModel):
    """Modèle appartenant à un utilisateur (isolation stricte des données)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="utilisateur",
    )

    objects = UserOwnedQuerySet.as_manager()

    # Nom du FK parent dont l'objet hérite son utilisateur s'il n'est pas renseigné
    # (formulaires enfants, inlines d'administration).
    proprietaire_via: str | None = None

    class Meta:
        abstract = True

    def heriter_proprietaire(self) -> None:
        if self.proprietaire_via and not self.user_id and getattr(self, f"{self.proprietaire_via}_id"):
            self.user_id = getattr(self, self.proprietaire_via).user_id

    def full_clean(self, *args, **kwargs):
        self.heriter_proprietaire()
        super().full_clean(*args, **kwargs)

    def save(self, *args, **kwargs):
        self.heriter_proprietaire()
        super().save(*args, **kwargs)

    def verifier_proprietaire(self, **objets) -> dict[str, str]:
        """
        Vérifie que chaque objet lié appartient au même utilisateur.
        Renvoie un dict {champ: message} exploitable par ValidationError.
        """
        erreurs = {}
        for champ, obj in objets.items():
            if obj is not None and obj.user_id != self.user_id:
                erreurs[champ] = "Cet élément n'appartient pas à votre espace."
        return erreurs


# ---------------------------------------------------------------------------
# Utilisateur & profil
# ---------------------------------------------------------------------------
class User(AbstractUser):
    """Modèle utilisateur personnalisé (extensible sans migration douloureuse)."""

    email = models.EmailField("adresse e-mail", unique=True)

    class Meta:
        verbose_name = "utilisateur"
        verbose_name_plural = "utilisateurs"


class Profile(TimeStampedModel):
    class Theme(models.TextChoices):
        SYSTEME = "SYSTEME", "Selon le système"
        CLAIR = "CLAIR", "Clair"
        SOMBRE = "SOMBRE", "Sombre"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="utilisateur",
    )
    devise_principale = models.CharField(
        "devise principale", max_length=3, choices=Devise.choices, default=Devise.EUR
    )
    theme = models.CharField("thème", max_length=10, choices=Theme.choices, default=Theme.SYSTEME)
    objectif_taux_epargne = models.DecimalField(
        "objectif de taux d'épargne (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("20.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    seuil_alerte_budget = models.DecimalField(
        "seuil d'alerte budget (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("90.00"),
        validators=[MinValueValidator(0), MaxValueValidator(200)],
        help_text="Pourcentage de consommation à partir duquel une ligne de budget est signalée.",
    )
    elements_par_page = models.PositiveSmallIntegerField(
        "éléments par page", default=50, validators=[MinValueValidator(10), MaxValueValidator(500)]
    )

    class Meta:
        verbose_name = "profil"
        verbose_name_plural = "profils"

    def __str__(self) -> str:
        return f"Profil de {self.user}"


# ---------------------------------------------------------------------------
# Taux de change (données de référence, non personnelles)
# ---------------------------------------------------------------------------
class TauxChange(models.Model):
    """1 unité de `devise_source` = `taux` unités de `devise_cible` à la `date`."""

    devise_source = models.CharField(max_length=3, choices=Devise.choices)
    devise_cible = models.CharField(max_length=3, choices=Devise.choices)
    date = models.DateField(default=timezone.localdate)
    taux = models.DecimalField(max_digits=18, decimal_places=8, validators=[MinValueValidator(Decimal("0.00000001"))])

    class Meta:
        verbose_name = "taux de change"
        verbose_name_plural = "taux de change"
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(fields=["devise_source", "devise_cible", "date"], name="taux_change_unique"),
            models.CheckConstraint(condition=~models.Q(devise_source=models.F("devise_cible")), name="taux_change_devises_distinctes"),
        ]
        indexes = [models.Index(fields=["devise_source", "devise_cible", "-date"])]

    def __str__(self) -> str:
        return f"1 {self.devise_source} = {self.taux} {self.devise_cible} ({self.date:%d/%m/%Y})"

    @classmethod
    def taux_au(cls, source: str, cible: str, au: date | None = None) -> Decimal:
        """Dernier taux connu à la date `au` (direct, sinon inverse)."""
        if source == cible:
            return Decimal("1")
        au = au or timezone.localdate()
        direct = cls.objects.filter(devise_source=source, devise_cible=cible, date__lte=au).order_by("-date").first()
        if direct:
            return direct.taux
        inverse = cls.objects.filter(devise_source=cible, devise_cible=source, date__lte=au).order_by("-date").first()
        if inverse:
            return Decimal("1") / inverse.taux
        raise ValidationError(f"Aucun taux de change {source}→{cible} disponible au {au:%d/%m/%Y}.")

    @classmethod
    def convertir(cls, montant: Decimal, source: str, cible: str, au: date | None = None) -> Decimal:
        return (montant * cls.taux_au(source, cible, au)).quantize(Decimal("0.01"))
