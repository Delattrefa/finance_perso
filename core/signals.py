from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def creer_profil(sender, instance, created, **kwargs):
    """Chaque utilisateur dispose d'un profil dès sa création."""
    if created:
        Profile.objects.get_or_create(user=instance)
