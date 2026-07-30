"""Ingestion du dump du référentiel LWIN (Liv-ex) dans ``ReferenceLwin``.

Logique partagée entre la commande ``manage.py import_lwin`` (usage CLI /
conteneur) et l'endpoint d'upload du panneau d'administration. Accepte le XLSX
Liv-ex d'origine ou un export CSV équivalent. Idempotent : les entrées
existantes (même code LWIN) sont mises à jour, les nouvelles créées.
"""

from __future__ import annotations

from .enrichment.lwin import vider_cache_referentiel
from .enrichment.normalize import guess_couleur
from .models import ReferenceLwin
from .tabular import ImportFichierError, lignes, texte

# La lecture CSV/XLSX est mutualisée avec les autres imports de référentiel
# (cf. ``tabular``) ; l'alias conserve le nom historique de l'exception, utilisé
# par la commande CLI et par l'upload du panneau d'administration.
LwinImportError = ImportFichierError

# Taille des lots bulk_create : compromis mémoire / nombre de requêtes SQLite.
_LOT = 1000

_CHAMPS_MAJ = [
    "producteur", "vin", "pays", "region", "sous_region", "couleur", "classification",
]


def _enregistrer(lot: list[ReferenceLwin]) -> int:
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


def importer_lwin(chemin: str, delimiter: str = ",") -> int:
    """Ingest le dump LWIN situé à ``chemin``. Retourne le nombre d'entrées
    importées/mises à jour. Lève ``LwinImportError`` si le fichier est illisible."""
    total = 0
    lot: list[ReferenceLwin] = []
    for ligne in lignes(chemin, delimiter):
        code = texte(ligne.get("LWIN"))
        if not code:
            continue
        # Seules les entrées actives du référentiel sont importées.
        statut = texte(ligne.get("STATUS")).lower()
        if statut and statut != "live":
            continue
        producteur = " ".join(
            p for p in (texte(ligne.get("PRODUCER_TITLE")), texte(ligne.get("PRODUCER_NAME"))) if p
        ) or texte(ligne.get("DISPLAY_NAME"))
        if not producteur:
            continue
        lot.append(
            ReferenceLwin(
                lwin=code[:16],
                producteur=producteur[:255],
                vin=texte(ligne.get("WINE"))[:255],
                pays=texte(ligne.get("COUNTRY"))[:100],
                region=texte(ligne.get("REGION"))[:255],
                sous_region=texte(ligne.get("SUB_REGION"))[:255],
                # L'effervescence est portée par SUB_TYPE (« Sparkling » ;
                # TYPE vaut toujours « Wine ») et la couleur par COLOUR.
                couleur=guess_couleur(
                    texte(ligne.get("SUB_TYPE")), texte(ligne.get("COLOUR"))
                ),
                classification=texte(ligne.get("CLASSIFICATION"))[:255],
            )
        )
        if len(lot) >= _LOT:
            total += _enregistrer(lot)
            lot = []
    total += _enregistrer(lot)
    # bulk_create ne déclenche pas les signaux du modèle : on invalide à la main
    # l'index en mémoire pour que le nouveau dump soit servi immédiatement.
    vider_cache_referentiel()
    return total
