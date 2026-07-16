from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

from .. import quotas
from ..runtime_config import source_activee
from .base import EnrichmentError, EnrichmentProvider, NormalizedWine
from .normalize import clean, couleur_from_type, named, parse_vintage, strip_vintage

logger = logging.getLogger(__name__)

# GrapeMinds (et d'autres) refusent le User-Agent par défaut d'urllib : on
# s'identifie comme l'application.
_USER_AGENT = "CavaVin/1.0 (+https://github.com/ppierre89/cavavin)"


class GrapeMindsProvider(EnrichmentProvider):
    """
    GrapeMinds (``api.grapeminds.eu``) — base de données œnologique : identification
    d'un vin par texte (``GET /wines/search``) puis détail complet
    (``GET /wines/{id}`` : couleur, cépages, région, descriptions, accords).
    L'analyse d'étiquette par photo (``POST /photo/analyze``) existe mais est
    réservée à l'offre *Enterprise*.

    Auth par header ``Authorization: Bearer <clé>`` (clé chargée depuis
    l'environnement, jamais codée en dur) ; la langue des libellés est négociée
    par ``Accept-Language`` (fr par défaut).

    ⚠️ **Slot désactivé par défaut.** Contrairement aux autres fournisseurs, il ne
    suffit pas de renseigner la clé : il faut aussi passer ``GRAPEMINDS_ENABLED=True``.
    Deux raisons :
      1. **Licence de stockage (PSL)** — les conditions GrapeMinds exigent une
         *Persistent Storage License* payante (``POST /licence/{id}`` + acceptation
         des conditions) pour **conserver durablement** leurs données « dans vos
         propres systèmes ». Or l'enrichissement CavaVin persiste dans le catalogue
         local : n'activer ce provider qu'une fois la situation de licence validée.
      2. **Quota serré** (offre publique : ~250 appels/mois) : le garde-fou
         explicite évite de le consommer par inadvertance.

    Schéma de réponse **validé** contre l'API réelle (``/wines/search`` et
    ``/wines/{id}``) : ``{id, display_name, color, sub_type, producer{name} |
    producer_name, region{name, country}, grapes[{name}], description{text,
    text_long}, ...}``. Les constantes de clés en tête de module restent tolérantes
    aux variantes ; l'ajustement se fait en un seul endroit si le schéma évolue.
    """

    name = "grapeminds"

    # --- Noms de champs (validés contre une vraie réponse /wines/search et
    # /wines/{id}) ---. On tolère plusieurs variantes par prudence. Réponse réelle :
    # {id, display_name, color, sub_type, producer|producer_name, region{name,country},
    #  grapes[{name}], description{text,text_long}, ...}.
    _KEYS_NOM = ("display_name", "name", "wine_name", "title")
    _KEYS_PRODUCTEUR = ("producer", "producer_display_name", "producer_name", "winery", "domaine")
    _KEYS_REGION = ("region", "region_name", "appellation")
    _KEYS_PAYS = ("country", "country_code", "country_name")
    _KEYS_COULEUR = ("color", "colour", "wine_color")
    _KEYS_SOUS_TYPE = ("sub_type", "subtype", "style")
    _KEYS_CEPAGES = ("grapes", "grape_varieties", "grapes_list", "cepages")
    _KEYS_ALCOOL = ("alcohol", "alcohol_content", "alcohol_percentage", "abv")
    # description/pairing/tasting_notes sont des objets {text, text_long, language}.
    _KEYS_DESCRIPTION = ("description", "tasting_notes", "notes")
    _KEYS_MILLESIME = ("vintage", "year")

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        # Clé présente ET activation (drapeau .env, surchargeable depuis l'admin).
        return bool(settings.GRAPEMINDS_KEY) and source_activee("grapeminds", settings.GRAPEMINDS_ENABLED)

    # ------------------------------------------------------------------ HTTP

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {settings.GRAPEMINDS_KEY}",
            "Accept": "application/json",
            "Accept-Language": settings.GRAPEMINDS_LANG,
            # GrapeMinds renvoie 403 sur le User-Agent par défaut d'urllib
            # (Python-urllib/x) : on s'identifie explicitement comme l'app.
            "User-Agent": _USER_AGENT,
        }

    def _request(self, method: str, path: str, payload: dict | None = None, timeout: int | None = None):
        """Appel JSON — renvoie le corps décodé (ou None en cas de miss)."""
        headers = self._headers()
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        url = settings.GRAPEMINDS_BASE_URL.rstrip("/") + path
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        quotas.compter(self.name)  # appel réseau réel : décompte le quota mensuel
        try:
            with urllib.request.urlopen(req, timeout=timeout or settings.GRAPEMINDS_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Erreurs à faire remonter clairement à l'utilisateur.
            if exc.code == 429:
                raise EnrichmentError(429, "Quota GrapeMinds atteint, réessaie plus tard.") from exc
            if exc.code == 401:
                raise EnrichmentError(502, "Clé GrapeMinds invalide (configuration serveur).") from exc
            # 402 (abonnement requis) / 403 (droits ou conditions non acceptées, ex.
            # analyse photo Enterprise) / 404 / 5xx : simple 'non trouvé', la cascade
            # continue et l'utilisateur n'a pas d'erreur bloquante.
            logger.warning("grapeminds %s %s -> HTTP %s", method, path, exc.code)
            return None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            logger.warning("grapeminds %s %s: %s", method, path, exc)
            return None

    # -------------------------------------------------------------- lookups

    def lookup_by_text(self, query: str) -> NormalizedWine | None:
        """`GET /wines/search?q=` (min. 3 caractères) puis détail du 1er candidat."""
        q = clean(query)
        if len(q) < 3:  # l'API rejette les requêtes plus courtes
            return None
        params = urllib.parse.urlencode({"q": q, "limit": settings.GRAPEMINDS_SEARCH_LIMIT})
        result = self._request("GET", f"/wines/search?{params}")
        return self._from_candidates(result)

    def lookup_by_image(self, data: bytes, content_type: str) -> NormalizedWine | None:
        """`POST /photo/analyze` — réservé à l'offre Enterprise.

        Désactivé par défaut (``GRAPEMINDS_PHOTO_ANALYSIS``) : sur l'offre publique
        l'endpoint répond 402/403, autant ne pas gaspiller un appel de quota."""
        if not settings.GRAPEMINDS_PHOTO_ANALYSIS:
            return None
        mime = content_type or "image/jpeg"
        photo = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
        payload = {"photo": photo, "max_results": settings.GRAPEMINDS_SEARCH_LIMIT}
        # La vision est bien plus lente que le texte : timeout dédié.
        result = self._request("POST", "/photo/analyze", payload, timeout=settings.GRAPEMINDS_IMAGE_TIMEOUT)
        return self._from_candidates(result)

    def _from_candidates(self, result) -> NormalizedWine | None:
        """Retient le meilleur candidat d'une recherche / analyse, puis (option)
        récupère son détail complet avant de normaliser."""
        candidats = _candidats(result)
        if not candidats:
            return None
        wine = candidats[0]
        wine_id = _pick(wine, "id", "wine_id")
        if settings.GRAPEMINDS_ENRICH_DETAIL and wine_id:
            detail = self.wine_detail(wine_id)
            if detail:
                wine = detail
        return self._to_normalized(wine)

    def wine_detail(self, wine_id) -> dict | None:
        """`GET /wines/{id}` — profil complet (cépages, région, descriptions, accords)."""
        if not wine_id:
            return None
        return _objet(self._request("GET", f"/wines/{wine_id}"))

    # ----------------------------------------------------------- normalisation

    def _to_normalized(self, wine: dict) -> NormalizedWine:
        nom = clean(_pick(wine, *self._KEYS_NOM))
        base = strip_vintage(nom)
        producteur = named(_pick(wine, *self._KEYS_PRODUCTEUR))
        region = named(_pick(wine, *self._KEYS_REGION))
        pays = _pays(_pick(wine, *self._KEYS_PAYS) or wine.get("region"))
        wine_id = _pick(wine, "id", "wine_id")

        millesime = _pick(wine, *self._KEYS_MILLESIME) or parse_vintage(nom)
        try:
            millesime = int(millesime) if millesime else None
        except (TypeError, ValueError):
            millesime = None

        return NormalizedWine(
            domaine_nom=producteur or base or "Domaine inconnu",
            cuvee_nom=base or nom or "Cuvée inconnue",
            couleur=_couleur(_pick(wine, *self._KEYS_COULEUR), _pick(wine, *self._KEYS_SOUS_TYPE)),
            # GrapeMinds raisonne par région (pas d'appellation dédiée documentée) :
            # la région tient lieu d'appellation, faute de mieux.
            appellation=region,
            cepages=_cepages(_pick(wine, *self._KEYS_CEPAGES)),
            millesime=millesime,
            source=self.name,
            reference_externe_id=str(wine_id or ""),
            raw={
                "region": region,
                "pays": pays,
                "description": _texte_riche(_pick(wine, *self._KEYS_DESCRIPTION)),
                "degre_alcool": _pick(wine, *self._KEYS_ALCOOL) or None,
            },
        )


# --------------------------------------------------------------------- helpers
# Fonctions pures et défensives face à un schéma de réponse encore non figé.


def _texte_riche(valeur) -> str:
    """Texte d'un champ descriptif : GrapeMinds renvoie ``description`` /
    ``tasting_notes`` sous forme d'objet ``{text, text_long, language}`` — on prend
    le texte court. Tolère aussi une chaîne directe."""
    if isinstance(valeur, dict):
        return clean(valeur.get("text") or valeur.get("text_long"))
    return clean(valeur)


def _pick(objet, *keys, default=""):
    """Première valeur non vide parmi ``keys`` d'un dict (sinon ``default``)."""
    if not isinstance(objet, dict):
        return default
    for key in keys:
        valeur = objet.get(key)
        if valeur not in (None, "", [], {}):
            return valeur
    return default


def _objet(payload):
    """Déballe un objet éventuellement enveloppé dans ``{"data": {...}}``."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    return None


def _candidats(payload) -> list:
    """Liste de vins candidats, quelle que soit l'enveloppe (liste nue ou
    ``{"data"|"wines"|"results": [...]}``)."""
    if isinstance(payload, list):
        return [w for w in payload if isinstance(w, dict)]
    if isinstance(payload, dict):
        for key in ("data", "wines", "results", "items"):
            valeur = payload.get(key)
            if isinstance(valeur, list):
                return [w for w in valeur if isinstance(w, dict)]
    return []


def _couleur(color, sous_type) -> str:
    """Couleur interne à partir de ``color`` (red/white/rose) ; les effervescents
    (``sub_type = sparkling``) priment et deviennent des bulles."""
    if "sparkl" in str(sous_type or "").lower():
        return "BULLES"
    return couleur_from_type(str(color or ""))


def _cepages(valeur) -> list[str]:
    """Noms des cépages : liste de chaînes ou d'objets ``{name}``."""
    if not isinstance(valeur, list):
        return []
    return [named(g) for g in valeur if named(g)]


def _pays(valeur) -> str:
    """Pays : chaîne directe, ou porté par l'objet région ``{country}``."""
    if isinstance(valeur, dict):
        return clean(valeur.get("country") or valeur.get("country_code"))
    return clean(valeur)
