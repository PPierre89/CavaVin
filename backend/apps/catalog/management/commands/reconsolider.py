"""Re-consolide la fiche de vérité des cuvées depuis leurs observations.

Rejoue la consolidation (cf. consolidation.py, Phase 2) sur l'ensemble du
référentiel : utile après un ajustement des politiques d'arbitrage ou des
confiances par canal, ou pour reconstruire la carte de provenance sur des cuvées
enrichies avant l'introduction des observations. Idempotent — aucune source
externe n'est re-sollicitée, seules les observations déjà en base sont lues.

Usage :
    python manage.py reconsolider
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.catalog.consolidation import consolider
from apps.catalog.models import Cuvee


class Command(BaseCommand):
    help = "Re-consolide toutes les cuvées depuis leurs observations de source."

    def handle(self, *args, **options):
        # On ne traite que les cuvées ayant au moins une observation : les autres
        # n'ont rien à arbitrer et consolider() les renverrait inchangées.
        cuvees = Cuvee.objects.filter(observations__isnull=False).distinct()
        total = cuvees.count()
        for cuvee in cuvees.iterator():
            consolider(cuvee)
        self.stdout.write(self.style.SUCCESS(f"{total} cuvée(s) re-consolidée(s)."))
