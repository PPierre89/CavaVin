"""Importe un catalogue de caviste (CSV) — canal de scraping, confiance basse.

Source : jeu de données `elvinrustam/wine-dataset` sur Kaggle (~1 290 vins,
déposé en CC0), fichier `WineDataset.csv`. Son apport propre est
l'**appellation** (« Napa Valley », « Puligny-Montrachet »), que ni X-Wines ni
un import en masse ne fournissent, et des descriptions rédigées.

C'est un catalogue de détaillant, pas un référentiel : il entre par le canal
`scrape:marchand`, dont la confiance (0,40) le place **derrière toutes les autres
sources**. Il comble des trous, il n'écrase jamais une valeur existante. Les
prix ne sont pas repris, les produits non vinicoles sont écartés, et une ligne
dont le producteur n'est pas isolable est ignorée. Cf. `docs/datasets-kaggle.md`
pour la veille et les réserves sur l'origine de ce jeu.

Usage :
    python manage.py import_catalogue_marchand /chemin/WineDataset.csv
    python manage.py import_catalogue_marchand WineDataset.csv --limite 100
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.marchand_import import importer_catalogue_marchand
from apps.catalog.models import Cuvee
from apps.catalog.tabular import ImportFichierError


class Command(BaseCommand):
    help = (
        "Importe un catalogue de caviste (CSV elvinrustam/wine-dataset) — "
        "appellations et descriptions, en canal de scraping (confiance basse)."
    )

    def add_arguments(self, parser):
        parser.add_argument("chemin", help="Chemin du fichier WineDataset.csv.")
        parser.add_argument("--delimiter", default=",", help="Séparateur (défaut : virgule).")
        parser.add_argument(
            "--limite", type=int, default=None,
            help="N'importer que les N premières lignes (essai).",
        )
        parser.add_argument(
            "--rafraichir", action="store_true",
            help="Re-déposer une observation pour les vins déjà importés.",
        )
        parser.add_argument(
            "--lot", type=int, default=500,
            help="Nombre de vins par transaction (défaut : 500).",
        )

    def handle(self, *args, chemin, delimiter, limite, rafraichir, lot, **options):
        def progression(resultat):
            self.stdout.write(f"  … {resultat}", ending="\r")

        try:
            resultat = importer_catalogue_marchand(
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
            f"{Cuvee.objects.exclude(appellation='').count()} avec appellation."
        )
