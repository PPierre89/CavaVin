"""Reconstruit l'index de recherche plein texte du catalogue (FTS5).

Les triggers SQLite (migration `0018`) maintiennent l'index à jour en
fonctionnement normal. Cette commande sert quand ils ont été contournés :
restauration d'une sauvegarde, chargement d'un catalogue transportable dont
l'index avait été purgé, ou simple doute sur sa cohérence.

Usage :
    python manage.py reconstruire_index
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.catalog.models import Cuvee
from apps.catalog.recherche import index_disponible, reconstruire


class Command(BaseCommand):
    help = "Reconstruit l'index FTS5 de recherche du catalogue."

    def handle(self, *args, **options):
        if not index_disponible():
            self.stdout.write(self.style.WARNING(
                "Index FTS5 absent (base non SQLite, ou migration 0018 non appliquée) : "
                "la recherche retombe sur le filtre standard."
            ))
            return
        total = reconstruire()
        self.stdout.write(self.style.SUCCESS(
            f"{total} cuvées indexées (sur {Cuvee.objects.count()} au catalogue)."
        ))
