"""Exporte le catalogue mutualisé dans un fichier SQLite transportable.

Le fichier produit ne contient **que** le catalogue partagé (domaines, cépages,
cuvées, observations, référentiel LWIN) : toutes les autres tables sont vidées,
à commencer par les données privées (caves, bouteilles, carnet) et les comptes.
Il se charge ensuite sur une autre installation avec `charger_catalogue`.

Usage :
    python manage.py exporter_catalogue /data/catalogue-cavavin.sqlite3
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.catalog.catalogue_portable import CataloguePortableError, exporter


class Command(BaseCommand):
    help = "Exporte le catalogue mutualisé (sans aucune donnée privée) vers un fichier SQLite."

    def add_arguments(self, parser):
        parser.add_argument("destination", help="Chemin du fichier à produire.")
        parser.add_argument(
            "--source", default=None,
            help="Base à exporter (défaut : la base courante).",
        )

    def handle(self, *args, destination, source, **options):
        source = source or str(settings.DATABASES["default"]["NAME"])
        try:
            resultat = exporter(source, destination)
        except CataloguePortableError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(str(resultat)))
        self.stdout.write(
            "Ne contient aucune donnée privée : caves, bouteilles, carnet et comptes "
            "ont été purgés."
        )
