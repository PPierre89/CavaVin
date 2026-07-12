"""Importe le dump CSV du référentiel LWIN (Liv-ex) dans la table ReferenceLwin.

Le dump (~100 000 identités de vins) est téléchargeable gratuitement après
inscription sur https://www.liv-ex.com/lwin/ (exporter/convertir en CSV si
besoin). L'import est idempotent : les entrées existantes (même code LWIN)
sont mises à jour, les nouvelles créées.

Usage :
    python manage.py import_lwin /chemin/vers/LWINdatabase.csv
"""

from __future__ import annotations

import csv

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.enrichment.normalize import guess_couleur
from apps.catalog.models import ReferenceLwin

# Taille des lots bulk_create : compromis mémoire / nombre de requêtes SQLite.
_LOT = 1000

_CHAMPS_MAJ = [
    "producteur", "vin", "pays", "region", "sous_region", "couleur", "classification",
]


class Command(BaseCommand):
    help = (
        "Importe le dump CSV du référentiel LWIN (Liv-ex) — repli local "
        "d'identification de vin (provider 'lwin'). Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument("chemin", help="Chemin du fichier CSV du dump LWIN.")
        parser.add_argument(
            "--delimiter", default=",", help="Séparateur du CSV (défaut : virgule)."
        )

    def handle(self, *args, chemin, delimiter, **options):
        try:
            fichier = open(chemin, newline="", encoding="utf-8-sig")
        except OSError as exc:
            raise CommandError(f"Impossible d'ouvrir {chemin} : {exc}") from exc

        total = 0
        lot: list[ReferenceLwin] = []
        with fichier:
            lecteur = csv.DictReader(fichier, delimiter=delimiter)
            for brut in lecteur:
                # En-têtes tolérées en toute casse ; valeurs nettoyées.
                ligne = {(k or "").strip().upper(): (v or "").strip() for k, v in brut.items()}
                code = ligne.get("LWIN", "")
                if not code:
                    continue
                # Seules les entrées actives du référentiel sont importées
                # (les dumps récents portent une colonne STATUS).
                statut = ligne.get("STATUS", "").lower()
                if statut and statut != "live":
                    continue
                producteur = " ".join(
                    p for p in (ligne.get("PRODUCER_TITLE", ""), ligne.get("PRODUCER_NAME", "")) if p
                ) or ligne.get("DISPLAY_NAME", "")
                if not producteur:
                    continue
                lot.append(
                    ReferenceLwin(
                        lwin=code[:16],
                        producteur=producteur[:255],
                        vin=ligne.get("WINE", "")[:255],
                        pays=ligne.get("COUNTRY", "")[:100],
                        region=ligne.get("REGION", "")[:255],
                        sous_region=ligne.get("SUB_REGION", "")[:255],
                        couleur=guess_couleur(ligne.get("TYPE", ""), ligne.get("COLOUR", "")),
                        classification=ligne.get("CLASSIFICATION", "")[:255],
                    )
                )
                if len(lot) >= _LOT:
                    total += self._enregistrer(lot)
                    lot = []
            total += self._enregistrer(lot)

        self.stdout.write(self.style.SUCCESS(
            f"{total} références LWIN importées/mises à jour "
            f"({ReferenceLwin.objects.count()} au total en base)."
        ))

    def _enregistrer(self, lot: list[ReferenceLwin]) -> int:
        """Upsert d'un lot : création, ou mise à jour si le code LWIN existe déjà."""
        if not lot:
            return 0
        ReferenceLwin.objects.bulk_create(
            lot,
            update_conflicts=True,
            unique_fields=["lwin"],
            update_fields=_CHAMPS_MAJ,
        )
        return len(lot)
