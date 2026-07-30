"""Charge un catalogue transportable dans la base courante.

Fusionne le catalogue mutualisé d'un fichier produit par `exporter_catalogue`
avec celui de cette installation. **Aucune donnée privée n'est touchée** : la
commande n'écrit que dans les tables du catalogue partagé, jamais dans les caves,
bouteilles ou notes de dégustation.

Les cuvées sont rapprochées par les mêmes clés d'identité que l'ingestion
(code-barres, référence externe, code LWIN, puis (domaine, nom normalisé)), donc
un vin déjà connu est **complété**, pas dupliqué. Les relevés du fichier sont
rejoués puis la fiche est consolidée selon la politique de *cette* installation.

Idempotent : recharger le même fichier ne crée ni doublon ni relevé fantôme.

Usage :
    python manage.py charger_catalogue /data/catalogue-cavavin.sqlite3
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.catalogue_portable import CataloguePortableError, charger
from apps.catalog.models import Cuvee, Domaine


class Command(BaseCommand):
    help = (
        "Charge un catalogue transportable (fichier SQLite) dans le référentiel, "
        "sans toucher aux données privées. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument("chemin", help="Fichier de catalogue à charger.")
        parser.add_argument(
            "--limite", type=int, default=None,
            help="Ne charger que les N premières cuvées (essai).",
        )

    def handle(self, *args, chemin, limite, **options):
        def progression(resultat):
            self.stdout.write(f"  … {resultat}", ending="\r")

        try:
            resultat = charger(chemin, limite=limite, progression=progression)
        except CataloguePortableError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(str(resultat)))
        self.stdout.write(
            f"Référentiel : {Cuvee.objects.count()} cuvées, {Domaine.objects.count()} domaines."
        )
