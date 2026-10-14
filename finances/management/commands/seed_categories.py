from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from finances.services.categories_defaut import creer_categories_par_defaut


class Command(BaseCommand):
    help = "Crée le jeu de catégories par défaut pour un ou plusieurs utilisateurs (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("usernames", nargs="*", help="Noms d'utilisateur")
        parser.add_argument("--tous", action="store_true", help="Tous les utilisateurs")

    def handle(self, *args, usernames, tous, **options):
        User = get_user_model()
        if tous:
            utilisateurs = User.objects.all()
        elif usernames:
            utilisateurs = User.objects.filter(username__in=usernames)
            manquants = set(usernames) - set(utilisateurs.values_list("username", flat=True))
            if manquants:
                raise CommandError(f"Utilisateur(s) introuvable(s) : {', '.join(sorted(manquants))}")
        else:
            raise CommandError("Indiquez des noms d'utilisateur ou --tous.")
        for user in utilisateurs:
            n = creer_categories_par_defaut(user)
            self.stdout.write(self.style.SUCCESS(f"{user.username} : {n} élément(s) créé(s)."))
