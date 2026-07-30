"""Importe un export scrapé « façon Vivino » (notes et prix) dans le référentiel.

Accepte, sans configuration, les schémas des exports qui circulent sur Kaggle :
`vivinoAllWineExportFrance.csv`, `vivino_top_ten.csv`, `vivino_wines_2026.csv`
et le `vivno_dataset.csv` de wine.com (UTF-16). Cf. `docs/datasets-kaggle.md` §6
pour la liste, les licences et les réserves.

**Ces données relèvent du scraping.** Elles proviennent de Vivino ou d'un
marchand, dont les conditions d'utilisation interdisent l'extraction — le dépôt
refuse d'ailleurs d'implémenter Vivino comme fournisseur d'enrichissement. Elles
entrent donc en confiance basse (`0.40`) via un canal préfixé `scrape:`, ce que
la commande impose, et la consolidation les range sous toute source légitime,
y compris pour les prix et les notes. Elles comblent des trous ; elles ne
dégradent rien.

Usage :
    python manage.py import_vivino /chemin/vivinoAllWineExportFrance.csv
    python manage.py import_vivino /chemin/vivno_dataset.csv \\
        --canal scrape:winecom --devise USD
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.models import Cuvee
from apps.catalog.tabular import ImportFichierError
from apps.catalog.vivino_import import CANAL_DEFAUT, importer_vivino


class Command(BaseCommand):
    help = (
        "Importe un export scrapé de notes/prix (Vivino, wine.com) en canal de "
        "scraping à confiance basse. Idempotent et reprenable."
    )

    def add_arguments(self, parser):
        parser.add_argument("chemin", help="Chemin du CSV à importer.")
        parser.add_argument(
            "--canal", default=CANAL_DEFAUT,
            help=(
                f"Canal de provenance (défaut : {CANAL_DEFAUT}). Doit être préfixé "
                "« scrape: » — c'est ce préfixe qui garantit la confiance basse."
            ),
        )
        parser.add_argument(
            "--devise", default="EUR",
            help="Devise des prix du fichier (défaut : EUR ; USD pour wine.com).",
        )
        parser.add_argument("--delimiter", default=",", help="Séparateur (défaut : virgule).")
        parser.add_argument(
            "--limite", type=int, default=None, help="N'importer que les N premières lignes."
        )
        parser.add_argument(
            "--rafraichir", action="store_true",
            help="Re-déposer une observation pour les vins déjà importés.",
        )
        parser.add_argument(
            "--lot", type=int, default=500, help="Vins par transaction (défaut : 500)."
        )

    def handle(self, *args, chemin, canal, devise, delimiter, limite, rafraichir, lot, **options):
        def progression(resultat):
            self.stdout.write(f"  … {resultat}", ending="\r")

        try:
            resultat = importer_vivino(
                chemin,
                canal=canal,
                devise=devise.upper(),
                delimiter=delimiter,
                limite=limite,
                rafraichir=rafraichir,
                lot=max(1, lot),
                progression=progression,
            )
        except ImportFichierError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"[{canal}] {resultat}"))
        self.stdout.write(f"Référentiel : {Cuvee.objects.count()} cuvées.")
