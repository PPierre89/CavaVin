import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """
    Crée un superuser à partir des variables d'environnement, s'il n'existe pas déjà.

    Idempotent : à relancer sans risque à chaque démarrage du conteneur. Ne fait
    rien si DJANGO_SUPERUSER_USERNAME ou DJANGO_SUPERUSER_PASSWORD est vide.
    """

    help = "Crée le superuser depuis DJANGO_SUPERUSER_* s'il est absent (idempotent)."

    def handle(self, *args, **options):
        username = os.getenv("DJANGO_SUPERUSER_USERNAME")
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD")
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "")

        if not username or not password:
            self.stdout.write(
                "DJANGO_SUPERUSER_USERNAME/PASSWORD non définis — aucun superuser créé."
            )
            return

        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Superuser '{username}' déjà présent — rien à faire.")
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' créé."))
