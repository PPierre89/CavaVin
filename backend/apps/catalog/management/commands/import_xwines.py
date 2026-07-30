"""Remplit le référentiel partagé depuis le jeu de données ouvert X-Wines.

X-Wines (~100 000 vins de 62 pays, licence Open Database) décrit chaque vin par
son producteur, sa région, son pays, son type, ses cépages, ses accords
mets-vins, son degré, son corps et son acidité. C'est le moyen le plus direct de
remplir le catalogue mutualisé sans clé d'API ni quota — voir
``docs/datasets-kaggle.md`` pour la veille (et les jeux écartés pour cause de
licence ou de scraping).

Téléchargement (une fois, hors application) :
  - Kaggle : https://www.kaggle.com/datasets/rogerioxavier/x-wines-slim-version
  - dépôt officiel (version complète 100 K) : https://github.com/rogerioxavier/X-Wines
On attend le fichier ``XWines_*_wines.csv`` (le fichier de *ratings* ne nous sert
pas : les notes communautaires ne sont pas modélisées ici).

Usage :
    python manage.py import_xwines /chemin/vers/XWines_Full_100K_wines.csv
    python manage.py import_xwines dump.csv --limite 500      # essai
    python manage.py import_xwines dump.csv --rafraichir      # nouvelle version
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import Cuvee, Domaine
from apps.catalog.tabular import ImportFichierError
from apps.catalog.xwines_import import importer_xwines


class Command(BaseCommand):
    help = (
        "Importe le jeu de données X-Wines (CSV) dans le référentiel partagé "
        "(domaines, cuvées, cépages, accords). Idempotent et reprenable."
    )

    def add_arguments(self, parser):
        parser.add_argument("chemin", help="Chemin du fichier XWines_*_wines.csv.")
        parser.add_argument(
            "--delimiter", default=",", help="Séparateur du CSV (défaut : virgule)."
        )
        parser.add_argument(
            "--limite", type=int, default=None,
            help="N'importer que les N premières lignes (essai à blanc).",
        )
        parser.add_argument(
            "--rafraichir", action="store_true",
            help=(
                "Re-déposer une observation pour les vins déjà importés "
                "(par défaut ils sont sautés, ce qui rend l'import reprenable)."
            ),
        )
        parser.add_argument(
            "--lot", type=int, default=500,
            help="Nombre de vins par transaction (défaut : 500).",
        )

    def handle(self, *args, chemin, delimiter, limite, rafraichir, lot, **options):
        def progression(resultat):
            # Un import complet dure plusieurs minutes : on montre l'avancement
            # plutôt que de laisser croire à un blocage.
            self.stdout.write(f"  … {resultat}", ending="\r")

        try:
            resultat = importer_xwines(
                chemin,
                delimiter=delimiter,
                limite=limite,
                rafraichir=rafraichir,
                lot=max(1, lot),
                progression=progression,
            )
        except ImportFichierError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(str(resultat)))
        self.stdout.write(
            f"Référentiel : {Cuvee.objects.count()} cuvées, "
            f"{Domaine.objects.count()} domaines."
        )
