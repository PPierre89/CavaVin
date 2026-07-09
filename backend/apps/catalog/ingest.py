from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from . import wine_profile
from .enrichment import NormalizedWine
from .models import Cepage, Cuvee, Domaine

# Champs de la cuvée alimentés par l'enrichissement wineapi (hors cépages M2M).
_ENRICH_FIELDS = (
    "region", "pays", "classification", "description", "elaborate", "corps",
    "acidite", "degre_alcool", "image_url", "lwin_code", "note_moyenne",
    "nb_notes", "prix_min", "prix_max", "devise", "accords", "scores",
    "prix_marchands",
)


def enrich_cuvee_from_wineapi(cuvee: Cuvee, detail: dict | None) -> Cuvee:
    """Persiste le détail wineapi (`GET /wines/{id}`) sur la cuvée.

    Appelée à l'identification (via ``upsert_cuvee``) et lors de la synchro
    manuelle. Ne remplace pas une valeur existante par du vide, pour ne pas
    effacer des données lors d'un enrichissement partiel.
    """
    if not detail:
        return cuvee

    data = wine_profile.normalize_detail(detail)
    cepages_noms = data.pop("cepages")

    for champ in _ENRICH_FIELDS:
        valeur = data.get(champ)
        # Ne pas écraser une valeur existante par du vide (enrichissement partiel).
        if valeur not in (None, "", []):
            setattr(cuvee, champ, valeur)

    cuvee.enrichi_le = timezone.now()
    cuvee.save()

    # Complète les cépages seulement s'ils manquent (le référentiel prime).
    if cepages_noms and cuvee.cepages.count() == 0:
        cepages = [Cepage.objects.get_or_create(nom=n)[0] for n in cepages_noms]
        cuvee.cepages.set(cepages)

    return cuvee


@transaction.atomic
def upsert_cuvee(wine: NormalizedWine) -> tuple[Cuvee, bool]:
    """
    Enregistre (ou retrouve) une cuvée à partir d'un vin normalisé — c'est le
    cache local partagé par le scan code-barres (US 01) et l'identification texte
    (US 04). Retourne (cuvee, created).

    Clé de déduplication, par ordre de priorité :
      1. code-barres  (chemin US 01)
      2. référence externe wineapi  (chemin US 04)
      3. (domaine, nom)  (dernier recours)

    Si le vin porte un détail wineapi (``raw["wineapi_detail"]``), la cuvée est
    enrichie et persistée dans la foulée (corps, notes, prix, accords, avis...).
    """
    couleurs_valides = dict(Cuvee.Couleur.choices)
    couleur = wine.couleur if wine.couleur in couleurs_valides else Cuvee.Couleur.AUTRE

    domaine, _ = Domaine.objects.get_or_create(nom=wine.domaine_nom, region="")

    if wine.code_barres:
        lookup = {"code_barres": wine.code_barres}
    elif wine.reference_externe_id:
        lookup = {"reference_externe_id": wine.reference_externe_id}
    else:
        lookup = {"domaine": domaine, "nom": wine.cuvee_nom}

    # filter().first() plutôt que get_or_create : ces clés n'ont pas de contrainte
    # unique en base, et get_or_create lèverait MultipleObjectsReturned (donc 500
    # sur tous les scans suivants) si un doublon existait déjà.
    detail = wine.raw.get("wineapi_detail")
    cuvee = Cuvee.objects.filter(**lookup).order_by("pk").first()
    if cuvee is not None:
        enrich_cuvee_from_wineapi(cuvee, detail)
        return cuvee, False

    cuvee = Cuvee.objects.create(
        domaine=domaine,
        nom=wine.cuvee_nom,
        couleur=couleur,
        appellation=wine.appellation,
        code_barres=wine.code_barres,
        reference_externe_id=wine.reference_externe_id,
    )
    if wine.cepages:
        cepages = [Cepage.objects.get_or_create(nom=nom)[0] for nom in wine.cepages]
        cuvee.cepages.set(cepages)

    enrich_cuvee_from_wineapi(cuvee, detail)
    return cuvee, True
