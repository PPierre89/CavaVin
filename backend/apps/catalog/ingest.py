from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from . import wine_profile
from .enrichment import NormalizedWine
from .models import Cepage, Cuvee, Domaine, SourceObservation

# Champs de la cuvée alimentés par l'enrichissement wineapi (hors cépages M2M).
_ENRICH_FIELDS = (
    "region", "pays", "classification", "description", "elaborate", "corps",
    "acidite", "degre_alcool", "image_url", "lwin_code", "note_moyenne",
    "nb_notes", "prix_min", "prix_max", "devise", "accords", "scores",
    "prix_marchands",
)

# Confiance a priori par canal (0 à 1), utilisée par la consolidation (Phase 2)
# pour arbitrer entre relevés. Repères de la revue d'architecture (§5) : LWIN et
# code-barres = identité forte ; le LLM et wineapi (identité) suivent ; le
# scraping arbitre en dernier. Ajustables sans migration.
_CONFIANCE_CANAL = {
    "lwin": 0.95,
    "openfoodfacts": 0.90,
    "claude": 0.75,
    "wineapi": 0.70,
}
_CONFIANCE_DEFAUT = 0.50


def _confiance_pour(canal: str) -> float:
    """Confiance a priori d'un canal. Tout canal de scraping arbitre en dernier."""
    if canal.startswith("scrape"):
        return 0.40
    return _CONFIANCE_CANAL.get(canal, _CONFIANCE_DEFAUT)


# Champs d'identité normalisés retenus dans une observation (ce que le relevé
# affirme du vin, indépendamment du payload brut propre au canal).
_CHAMPS_IDENTITE = (
    "domaine_nom", "cuvee_nom", "couleur", "appellation", "cepages",
    "millesime", "code_barres", "reference_externe_id",
)


def enregistrer_observation(
    cuvee: Cuvee, *, canal: str, payload_brut: dict | None = None,
    champs: dict | None = None, confiance: float | None = None,
) -> SourceObservation:
    """Dépose un relevé brut d'un canal pour une cuvée (append-only).

    Point d'écriture unique du référentiel côté « sources » : chaque hit de canal
    y laisse une trace horodatée et pondérée, sans jamais écraser les
    précédentes. La consolidation (Phase 2) lira ces observations pour arbitrer
    la fiche.
    """
    return SourceObservation.objects.create(
        cuvee=cuvee,
        canal=canal or "inconnu",
        payload_brut=payload_brut or {},
        champs=champs or {},
        confiance=confiance if confiance is not None else _confiance_pour(canal),
    )


def _observation_depuis_wine(cuvee: Cuvee, wine: NormalizedWine) -> None:
    """Enregistre l'observation issue d'un ``NormalizedWine`` (tout canal)."""
    champs = {c: getattr(wine, c) for c in _CHAMPS_IDENTITE}
    enregistrer_observation(
        cuvee, canal=wine.source, payload_brut=wine.raw, champs=champs
    )


def synchroniser_wineapi(cuvee: Cuvee, detail: dict | None) -> Cuvee:
    """Projette *et* observe un détail wineapi (synchro manuelle / paresseuse).

    Utilisé par les vues de fiche : contrairement à ``upsert_cuvee`` (qui connaît
    le vrai canal via ``NormalizedWine.source``), ces chemins re-sollicitent
    spécifiquement wineapi. On dépose donc une observation « wineapi » en plus de
    rafraîchir la projection de la cuvée.
    """
    if detail:
        enregistrer_observation(
            cuvee,
            canal="wineapi",
            payload_brut=detail,
            champs=wine_profile.normalize_detail(detail),
        )
    return enrich_cuvee_from_wineapi(cuvee, detail)


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


def _domaine_pour(nom: str) -> Domaine:
    """Retrouve le domaine (producteur) par son nom, ou le crée.

    Le nom est l'identité canonique d'un producteur. On préfère réutiliser une
    fiche déjà renseignée avec une région ; à défaut la fiche sans région ; sinon
    on en crée une (région vide). Corrige le défaut de l'ancien
    ``get_or_create(nom=…, region="")`` qui scindait un même producteur en deux
    fiches quand un canal d'enrichissement le (re)créait sans région alors qu'une
    fiche régionale existait déjà (cf. docs/architecture-referentiel.md, D3).
    """
    existant = (
        Domaine.objects.filter(nom=nom).exclude(region="").order_by("pk").first()
        or Domaine.objects.filter(nom=nom, region="").order_by("pk").first()
    )
    return existant or Domaine.objects.create(nom=nom, region="")


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

    domaine = _domaine_pour(wine.domaine_nom)

    if wine.code_barres:
        lookup = {"code_barres": wine.code_barres}
    elif wine.reference_externe_id:
        lookup = {"reference_externe_id": wine.reference_externe_id}
    else:
        lookup = {"domaine": domaine, "nom": wine.cuvee_nom}

    # filter().first() plutôt que get_or_create : la clé de repli (domaine, nom)
    # n'a pas de contrainte unique, et get_or_create lèverait
    # MultipleObjectsReturned (donc 500 sur tous les scans suivants) si un doublon
    # historique subsistait. (Les clés fortes code-barres / référence externe sont
    # désormais contraintes uniques, cf. Phase 0.)
    detail = wine.raw.get("wineapi_detail")
    cuvee = Cuvee.objects.filter(**lookup).order_by("pk").first()
    if cuvee is not None:
        enrich_cuvee_from_wineapi(cuvee, detail)
        _observation_depuis_wine(cuvee, wine)
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
    _observation_depuis_wine(cuvee, wine)
    return cuvee, True
