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

    # Conserve la réponse brute complète (toutes les informations remontées, même
    # celles non mappées ci-dessus). Le dernier appel fait foi : « actualiser »
    # remplace le snapshot par les données fraîches (prix, scores...).
    cuvee.wineapi_detail = detail
    cuvee.historique_prix = _fusionne_historique_prix(cuvee.historique_prix, detail, data)
    cuvee.enrichi_le = timezone.now()
    cuvee.save()

    # Complète les cépages seulement s'ils manquent (le référentiel prime).
    if cepages_noms and cuvee.cepages.count() == 0:
        cepages = [Cepage.objects.get_or_create(nom=n)[0] for n in cepages_noms]
        cuvee.cepages.set(cepages)

    return cuvee


# Nombre max de points d'historique conservés par cuvée (garde-fou de taille).
_HISTORIQUE_PRIX_MAX = 60


def _fusionne_historique_prix(existant, detail: dict, data: dict) -> list[dict]:
    """Fusionne les nouveaux points de prix dans la série accumulée sur la cuvée.

    Déduplique par date (un point par jour de relevé) : un nouveau relevé pour une
    date déjà présente met le point à jour. Repli : si l'API ne renvoie aucune
    offre datée mais une fourchette marché (``priceRange``), on ancre un point à la
    date du jour pour que la série se construise quand même. La liste est triée par
    date et plafonnée aux ``_HISTORIQUE_PRIX_MAX`` points les plus récents.
    """
    par_date: dict[str, dict] = {}
    for point in existant or []:
        if isinstance(point, dict) and point.get("date"):
            par_date[point["date"]] = point

    nouveaux = wine_profile.points_historique_prix(detail)
    if not nouveaux and data.get("prix_min") is not None and data.get("prix_max") is not None:
        # Aucune offre datée : on date la fourchette marché au jour de la synchro.
        nouveaux = [{
            "date": timezone.now().date().isoformat(),
            "prix_min": data["prix_min"],
            "prix_max": data["prix_max"],
            "devise": data.get("devise") or "EUR",
        }]
    for point in nouveaux:
        par_date[point["date"]] = point

    ordonnee = [par_date[d] for d in sorted(par_date)]
    return ordonnee[-_HISTORIQUE_PRIX_MAX:]


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
