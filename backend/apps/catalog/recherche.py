"""Recherche plein texte du catalogue, adossée à l'index FTS5.

Le filtre de recherche de DRF traduit ``?search=`` en ``LIKE '%terme%'`` sur
chaque champ de ``search_fields``. Le joker initial interdit tout index : à
127 952 cuvées, chaque frappe de l'autocomplétion balayait la table entière
(mesuré : 180 ms au cache chaud sur NVMe, plusieurs secondes sur un NAS).

Ce module interroge à la place la table virtuelle ``catalog_cuvee_fts``
(migration `0018`), maintenue à jour par des triggers SQLite — 0,1 à 2,7 ms sur
le même catalogue.

Il **retombe silencieusement** sur le filtre standard si l'index est absent :
base non SQLite, ou migration pas encore appliquée. La recherche reste donc
correcte en toutes circonstances, seulement plus lente.
"""

from __future__ import annotations

import re

from django.db import connection
from rest_framework.filters import SearchFilter

# Nombre maximal de cuvées remontées de l'index.
#
# **Ce plafond tronque les résultats**, y compris le `count` de la pagination :
# une recherche large annonce donc au plus ce nombre de correspondances. C'est
# assumé — cet endpoint sert l'autocomplétion de l'écran d'ajout, qui n'en
# affiche que six, et le classement par pertinence met les meilleures en tête.
#
# La valeur est le point d'équilibre mesuré sur un catalogue de 127 952 cuvées :
# le coût ne vient plus de FTS5 (moins d'une milliseconde) mais de la
# reconstruction de l'ordre de pertinence côté ORM, un `CASE` d'autant de
# branches. Mesuré pour « margaux » : 300 -> 69 ms, 100 -> 14 ms, 50 -> 9 ms.
_PLAFOND = 100

# Un terme FTS5 est une suite de caractères de mot ; tout le reste est de la
# syntaxe (opérateurs, guillemets, colonnes). On ne laisse donc passer que des
# tokens alphanumériques, et on les met entre guillemets : la saisie de
# l'utilisateur ne peut pas être interprétée comme une expression FTS.
_TOKEN = re.compile(r"\w+", re.UNICODE)

_MIN_TOKEN = 1


def index_disponible() -> bool:
    """L'index FTS5 existe-t-il dans la base courante ?"""
    if connection.vendor != "sqlite":
        return False
    with connection.cursor() as curseur:
        curseur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_cuvee_fts'"
        )
        return curseur.fetchone() is not None


def expression_fts(recherche: str) -> str:
    """Saisie utilisateur -> expression ``MATCH`` FTS5, ou "" si inexploitable.

    Chaque token devient un préfixe (``"marg"*``) et tous sont exigés : taper
    « margaux 2015 » restreint, il n'élargit pas. C'est la sémantique du filtre
    de DRF, qui exige lui aussi tous les termes.
    """
    tokens = [t for t in _TOKEN.findall(recherche or "") if len(t) >= _MIN_TOKEN]
    if not tokens:
        return ""
    # Les guillemets neutralisent la syntaxe FTS ; le `*` final rend le dernier
    # caractère extensible, ce qu'attend une recherche au fil de la frappe.
    return " AND ".join(f'"{t}"*' for t in tokens)


def identifiants(recherche: str, plafond: int = _PLAFOND) -> list[int]:
    """Identifiants de cuvées correspondant à ``recherche``, du plus pertinent
    au moins pertinent (classement ``bm25`` de FTS5)."""
    expression = expression_fts(recherche)
    if not expression:
        return []
    with connection.cursor() as curseur:
        curseur.execute(
            "SELECT rowid FROM catalog_cuvee_fts WHERE catalog_cuvee_fts MATCH %s "
            "ORDER BY rank LIMIT %s",
            [expression, plafond],
        )
        return [ligne[0] for ligne in curseur.fetchall()]


class RechercheCuvee(SearchFilter):
    """Filtre de recherche du catalogue : FTS5 si disponible, sinon `LIKE`.

    Se substitue au ``SearchFilter`` de DRF sur ``CuveeViewSet``. En repli, le
    comportement d'origine est conservé à l'identique — mêmes résultats, mêmes
    ``search_fields``.
    """

    def filter_queryset(self, request, queryset, view):
        recherche = request.query_params.get(self.search_param, "").strip()
        if not recherche or not index_disponible():
            return super().filter_queryset(request, queryset, view)

        pks = identifiants(recherche)
        if not pks:
            return queryset.none()
        # `order_by()` neutralise l'ordre par défaut du modèle (domaine, nom) :
        # sur une recherche, la pertinence prime sur l'alphabet. Django préserve
        # l'ordre du `IN` via `Case/When` construit ci-dessous.
        from django.db.models import Case, IntegerField, When

        rang = Case(
            *[When(pk=pk, then=position) for position, pk in enumerate(pks)],
            output_field=IntegerField(),
        )
        return queryset.filter(pk__in=pks).order_by(rang)


def reconstruire() -> int:
    """Reconstruit l'index depuis le catalogue. Retourne le nombre de cuvées.

    Les triggers maintiennent l'index à jour en fonctionnement normal ; cette
    fonction sert après une manipulation qui les contourne — restauration d'une
    sauvegarde, chargement d'un catalogue transportable dont l'index avait été
    purgé, ou simple doute.
    """
    if not index_disponible():
        return 0
    with connection.cursor() as curseur:
        curseur.execute("DELETE FROM catalog_cuvee_fts")
        curseur.execute(
            "INSERT INTO catalog_cuvee_fts(rowid, nom, domaine, appellation, region) "
            "SELECT c.id, c.nom, d.nom, c.appellation, c.region "
            "FROM catalog_cuvee c JOIN catalog_domaine d ON d.id = c.domaine_id"
        )
        curseur.execute("SELECT COUNT(*) FROM catalog_cuvee_fts")
        return curseur.fetchone()[0]
