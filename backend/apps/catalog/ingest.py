from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from . import wine_profile
from .consolidation import consolider
from .enrichment import NormalizedWine
from .enrichment.normalize import normaliser_nom
from .models import Cepage, Cuvee, Domaine, SourceObservation

# Champs de la cuvée alimentés par l'enrichissement wineapi (hors cépages M2M).
# `lwin_code` en est exclu : c'est une identité canonique (clé de déduplication,
# contrainte unique), posée une fois à la création par ``upsert_cuvee`` — pas un
# attribut ré-écrit à chaque enrichissement (au risque de violer l'unicité).
_ENRICH_FIELDS = (
    "region", "pays", "classification", "description", "elaborate", "corps",
    "acidite", "degre_alcool", "image_url", "note_moyenne",
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
    # GrapeMinds : base œnologique structurée, confiance d'identité comparable à
    # wineapi (légèrement en dessous, provider plus récent et moins éprouvé ici).
    "grapeminds": 0.68,
    # Vinou : catalogue producteur (données saisies par les domaines), couverture
    # de niche mais identité directe (nom, domaine, gtin) — confiance modérée.
    "vinou": 0.65,
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
    """Enregistre l'observation issue d'un ``NormalizedWine`` (tout canal).

    Les ``champs`` sont rendus auto-descriptifs : identité (couleur, appellation…)
    complétée, quand le canal porte un détail au format wineapi, par les champs de
    profil/marché normalisés — de sorte que la consolidation puisse arbitrer cette
    observation comme n'importe quelle autre.
    """
    champs = {c: getattr(wine, c) for c in _CHAMPS_IDENTITE}
    detail = wine.raw.get("wineapi_detail")
    if detail:
        champs.update(wine_profile.normalize_detail(detail))
    # Confiance propre au relevé si le canal la qualifie (ex: score LWIN), bornée
    # à 1 ; sinon la confiance a priori du canal s'applique.
    confiance = None if wine.confiance is None else min(1.0, max(0.0, wine.confiance))
    enregistrer_observation(
        cuvee, canal=wine.source, payload_brut=wine.raw, champs=champs,
        confiance=confiance,
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
    enrich_cuvee_from_wineapi(cuvee, detail)
    return consolider(cuvee)


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


def _completer_identites(cuvee: Cuvee, identites: list[tuple[str, str]]) -> None:
    """Complète les identités fortes *manquantes* d'une cuvée déjà connue.

    Un vin identifié par son nom (référence wineapi) puis scanné par son
    code-barres doit finir par porter les deux : le scan suivant devient alors un
    hit local, sans appel externe ni quota consommé. On n'écrase jamais une
    identité déjà posée (elle fait autorité), et on ne revendique une valeur que
    si aucune autre cuvée ne la détient — deux vins distincts peuvent
    exceptionnellement partager un code LWIN, et le premier arrivé le garde.
    """
    a_ecrire = []
    for champ, valeur in identites:
        if getattr(cuvee, champ):
            continue
        if Cuvee.objects.filter(**{champ: valeur}).exclude(pk=cuvee.pk).exists():
            continue
        setattr(cuvee, champ, valeur)
        a_ecrire.append(champ)
    if a_ecrire:
        cuvee.save(update_fields=a_ecrire)


@transaction.atomic
def upsert_cuvee(wine: NormalizedWine) -> tuple[Cuvee, bool]:
    """
    Enregistre (ou retrouve) une cuvée à partir d'un vin normalisé — c'est le
    cache local partagé par le scan code-barres (US 01) et l'identification texte
    (US 04). Retourne (cuvee, created).

    Clés de déduplication *fortes* (contraintes uniques), par ordre de priorité :
      1. code-barres  (chemin US 01)
      2. référence externe wineapi  (chemin US 04)
      3. code LWIN  (réconcilie les relevés LWIN entre eux et avec les autres canaux)
    puis, si le relevé n'en porte aucune, le repli (domaine, nom).

    Un code-barres inconnu **ne conclut pas** : un vin déjà connu par sa référence
    wineapi doit être retrouvé quand le relevé courant apporte *en plus* un
    code-barres inédit (un même vin se décline en plusieurs conditionnements). On
    poursuit donc sur la clé suivante. Une référence externe ou un code LWIN
    inconnus, eux, sont décisifs : ils désignent un vin distinct, et on crée.
    Sans ce repli sur la référence externe, la création violait sa contrainte
    d'unicité et le scan répondait 500 (cf. tests).

    Si le vin porte un détail wineapi (``raw["wineapi_detail"]``), la cuvée est
    enrichie et persistée dans la foulée (corps, notes, prix, accords, avis...).
    """
    couleurs_valides = dict(Cuvee.Couleur.choices)
    couleur = wine.couleur if wine.couleur in couleurs_valides else Cuvee.Couleur.AUTRE

    domaine = _domaine_pour(wine.domaine_nom)

    detail = wine.raw.get("wineapi_detail")
    lwin_code = (detail or {}).get("lwinCode") or ""

    # Identités fortes affirmées par ce relevé, par ordre de priorité.
    identites = [
        (champ, valeur)
        for champ, valeur in (
            ("code_barres", wine.code_barres),
            ("reference_externe_id", wine.reference_externe_id),
            ("lwin_code", lwin_code),
        )
        if valeur
    ]

    # filter().first() plutôt que get_or_create : la clé de repli (domaine, nom)
    # n'a pas de contrainte unique, et get_or_create lèverait
    # MultipleObjectsReturned (donc 500 sur tous les scans suivants) si un doublon
    # historique subsistait. (Les clés fortes code-barres / référence externe /
    # code LWIN sont désormais contraintes uniques, cf. Phases 0 et 3.)
    cuvee = None
    for champ, valeur in identites:
        cuvee = Cuvee.objects.filter(**{champ: valeur}).order_by("pk").first()
        if cuvee is not None or champ != "code_barres":
            break  # seul un code-barres inconnu autorise à essayer la clé suivante.
    if cuvee is None and not identites:
        # Repli sur la forme *normalisée* du nom : une comparaison brute laissait
        # « Grand Vin » et « Grand vin » cohabiter (cf. Cuvee.nom_normalise).
        cuvee = (
            Cuvee.objects.filter(
                domaine=domaine, nom_normalise=normaliser_nom(wine.cuvee_nom)
            )
            .order_by("pk")
            .first()
        )

    created = cuvee is None
    if created:
        # Garde-fou d'unicité : une identité déjà revendiquée par une autre cuvée
        # (deux vins distincts peuvent exceptionnellement partager un code LWIN)
        # est abandonnée plutôt que de violer la contrainte — et de faire échouer
        # le scan. Le premier à l'avoir revendiquée la garde.
        valeurs = {
            champ: valeur
            for champ, valeur in identites
            if not Cuvee.objects.filter(**{champ: valeur}).exists()
        }
        cuvee = Cuvee.objects.create(
            domaine=domaine,
            nom=wine.cuvee_nom,
            couleur=couleur,
            appellation=wine.appellation,
            code_barres=valeurs.get("code_barres", ""),
            reference_externe_id=valeurs.get("reference_externe_id", ""),
            # Renseigné dès la création pour que la réconciliation par code LWIN
            # fonctionne au relevé suivant, sans attendre la consolidation.
            lwin_code=valeurs.get("lwin_code", ""),
        )
        if wine.cepages:
            cepages = [Cepage.objects.get_or_create(nom=nom)[0] for nom in wine.cepages]
            cuvee.cepages.set(cepages)
    else:
        _completer_identites(cuvee, identites)

    # Snapshot wineapi (brut + historique de prix), puis journalisation du relevé
    # de ce canal, et enfin arbitrage de la fiche consolidée sur l'ensemble des
    # observations (la consolidation fait autorité sur les champs projetés).
    enrich_cuvee_from_wineapi(cuvee, detail)
    _observation_depuis_wine(cuvee, wine)
    consolider(cuvee)
    return cuvee, created


@transaction.atomic
def upsert_multi(hits: list[NormalizedWine]) -> tuple[Cuvee, bool]:
    """Fusion multi-sources d'une identification.

    La **première** source identifie (ou crée) la cuvée via ``upsert_cuvee`` — elle
    fixe l'identité canonique (clés de déduplication : code-barres, référence
    externe, LWIN, (domaine, nom)). Les sources **suivantes** déposent chacune leur
    observation sur *cette même* cuvée (sans re-dédupliquer ni créer de doublon),
    puis la fiche est consolidée sur l'ensemble : chaque champ retient la valeur du
    canal le plus fiable (cf. ``consolidation.consolider``). On récupère ainsi le
    meilleur de chaque source (identité + cépages d'un canal, prix d'un autre,
    description d'un troisième…). ``hits`` doit être non vide.
    """
    cuvee, created = upsert_cuvee(hits[0])
    if len(hits) > 1:
        for wine in hits[1:]:
            _observation_depuis_wine(cuvee, wine)
        consolider(cuvee)
    return cuvee, created
