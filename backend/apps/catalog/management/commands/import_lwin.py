"""Importe le dump du référentiel LWIN (Liv-ex) dans la table ReferenceLwin.

Le dump (~200 000 identités de vins) est téléchargeable gratuitement après
inscription sur https://www.liv-ex.com/lwin/ — livré en XLSX, accepté tel quel
(un CSV équivalent est aussi accepté). L'import est idempotent : les entrées
existantes (même code LWIN) sont mises à jour, les nouvelles créées.

La logique d'ingestion est partagée avec l'upload du panneau d'administration
(cf. ``apps.catalog.lwin_import``).

Usage :
    python manage.py import_lwin /chemin/vers/LWINdatabase.xlsx
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.lwin_import import LwinImportError, importer_lwin
from apps.catalog.models import ReferenceLwin


class Command(BaseCommand):
    help = (
        "Importe le dump XLSX/CSV du référentiel LWIN (Liv-ex) — repli local "
        "d'identification de vin (provider 'lwin'). Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument("chemin", help="Chemin du dump LWIN (.xlsx ou .csv).")
        parser.add_argument(
            "--delimiter", default=",", help="Séparateur si CSV (défaut : virgule)."
        )

    def handle(self, *args, chemin, delimiter, **options):
        try:
            total = importer_lwin(chemin, delimiter)
        except LwinImportError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(
            f"{total} références LWIN importées/mises à jour "
            f"({ReferenceLwin.objects.count()} au total en base)."
        ))
