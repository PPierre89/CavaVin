from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.cache import cache

from .base import EnrichmentError, EnrichmentProvider, NormalizedWine
from .normalize import clean, couleur_from_type, parse_vintage, strip_vintage

logger = logging.getLogger(__name__)

# Le JWT Vinou expire après 12 h : on le met en cache un peu en deçà et on le
# renouvelle (re-login) à l'expiration ou sur un 401.
_JWT_CACHE_KEY = "vinou:jwt"
_JWT_TTL = 11 * 60 * 60


class VinouProvider(EnrichmentProvider):
    """
    Vinou (``api.vinou.de``) — catalogue de vins alimenté par les domaines inscrits
    sur la plateforme Vinou (majoritairement allemands). Identification par texte
    (``POST /wines/search``) et par code-barres (filtre sur le champ ``gtin``).

    Toutes les routes sont en **POST JSON** ; la réponse est enveloppée
    ``{"info": "success", "data": ...}``.

    **Authentification (JWT).** Le flux documenté :
      1. générer un *API-Token* dans l'app Vinou (module Tokens) ;
      2. ``POST /service/login`` avec l'``AuthID`` (``VINOU_AUTH_ID``) et l'API-Token
         (``VINOU_API_TOKEN``) → renvoie un JWT valable **12 h** ;
      3. envoyer ``Authorization: Bearer <JWT>`` sur tous les autres appels.
    Le JWT est mis en cache (~11 h) et renouvelé automatiquement (re-login) à
    l'expiration ou sur un 401. Les routes ``/wines/search`` / ``/wines/getPublic``
    étant *publiques*, sans identifiants le provider fonctionne quand même en **mode
    public** (données potentiellement plus pauvres qu'au niveau *Service*). Un JWT
    déjà obtenu peut aussi être fourni directement via ``VINOU_TOKEN``.

    ⚠️ **Slot désactivé par défaut** (``VINOU_ENABLED``) : couverture de niche —
    Vinou expose les vins de ses domaines clients (surtout allemands), pas un
    référentiel mondial. Utile en complément (vins allemands, code-barres), faible
    taux de correspondance sur un vin quelconque.

    ⚠️ **Champs à IDs non résolus** : ``region`` et ``grapetypeIds`` sont des
    identifiants numériques Vinou (pas des noms) ; sans table de correspondance
    dédiée, on ne peut ni nommer la région/appellation ni les cépages. On mappe donc
    ce qui est directement exploitable (nom, domaine, couleur, pays, millésime,
    code-barres, alcool, description).
    """

    name = "vinou"

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        # Opt-in explicite. Les identifiants sont optionnels : les routes
        # /wines/search sont publiques (mode public sans jeton).
        return bool(settings.VINOU_ENABLED)

    # ---------------------------------------------------------------- auth JWT

    def _has_credentials(self) -> bool:
        return bool(settings.VINOU_AUTH_ID and settings.VINOU_API_TOKEN)

    def _jwt(self) -> str:
        """JWT courant : override explicite, sinon cache, sinon login (si identifiants)."""
        if settings.VINOU_TOKEN:  # JWT déjà obtenu, fourni tel quel
            return settings.VINOU_TOKEN
        if not self._has_credentials():
            return ""  # mode public
        cached = cache.get(_JWT_CACHE_KEY)
        if cached:
            return cached
        jwt = self._login()
        if jwt:
            cache.set(_JWT_CACHE_KEY, jwt, _JWT_TTL)
        return jwt

    def _login(self) -> str:
        """``POST /service/login`` (AuthID + API-Token) → JWT. '' si échec réseau."""
        payload = {"id": settings.VINOU_AUTH_ID, "token": settings.VINOU_API_TOKEN}
        body = self._raw_post("/service/login", payload, jwt="")
        return _extract_jwt(body)

    # ------------------------------------------------------------------ HTTP

    def _raw_post(self, path: str, payload: dict, jwt: str) -> dict | None:
        """POST JSON brut — renvoie le corps décodé (dict/str) ou None. Ne déballe
        pas l'enveloppe (utilisé aussi par le login, dont la réponse porte le JWT)."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            # Certains hôtes refusent le User-Agent par défaut d'urllib : on
            # s'identifie explicitement comme l'application.
            "User-Agent": "CavaVin/1.0 (+https://github.com/ppierre89/cavavin)",
        }
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"
        url = settings.VINOU_BASE_URL.rstrip("/") + path
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=settings.VINOU_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path: str, payload: dict, _retry: bool = True) -> dict | None:
        """POST authentifié vers une route de données — renvoie le ``data`` de
        l'enveloppe ``{"info","data"}``. Gère l'expiration du JWT (re-login sur 401)."""
        jwt = self._jwt()
        try:
            body = self._raw_post(path, payload, jwt)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise EnrichmentError(429, "Quota Vinou atteint, réessaie plus tard.") from exc
            if exc.code in (401, 403):
                # Jeton expiré/invalide : purge le cache et retente une fois via un
                # login frais (seulement en mode identifiants, pas en JWT override).
                if _retry and jwt and self._has_credentials() and not settings.VINOU_TOKEN:
                    cache.delete(_JWT_CACHE_KEY)
                    return self._post(path, payload, _retry=False)
                raise EnrichmentError(502, "Auth Vinou invalide (configuration serveur).") from exc
            logger.warning("vinou POST %s -> HTTP %s", path, exc.code)
            return None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            logger.warning("vinou POST %s: %s", path, exc)
            return None
        # Enveloppe standard Vinou : {"info": "success", "data": ...}.
        if not isinstance(body, dict) or body.get("info") != "success":
            return None
        return body.get("data")

    # -------------------------------------------------------------- lookups

    def lookup_by_text(self, query: str) -> NormalizedWine | None:
        q = clean(query)
        if not q:
            return None
        data = self._post("/wines/search", {"query": q, "pageSize": settings.VINOU_SEARCH_LIMIT})
        return self._premier(data)

    def lookup_by_barcode(self, ean: str) -> NormalizedWine | None:
        # Le champ `gtin` porte le code-barres. Vinou n'accepte pas `filter` sur ce
        # champ (400) mais bien la recherche par champ via `query` objet.
        code = clean(ean)
        if not code:
            return None
        data = self._post(
            "/wines/search",
            {"query": {"gtin": code}, "pageSize": settings.VINOU_SEARCH_LIMIT},
        )
        return self._premier(data, code_barres=code)

    def _premier(self, data, code_barres: str = "") -> NormalizedWine | None:
        """Retient le premier vin candidat d'une réponse de recherche."""
        candidats = _candidats(data)
        if not candidats:
            return None
        return self._to_normalized(candidats[0], code_barres=code_barres)

    # ----------------------------------------------------------- normalisation

    def _to_normalized(self, wine: dict, code_barres: str = "") -> NormalizedWine:
        nom = clean(wine.get("name"))
        base = strip_vintage(nom)
        winery = wine.get("winery") if isinstance(wine.get("winery"), dict) else {}
        domaine = clean(winery.get("company") or winery.get("name"))
        pays = clean(wine.get("countrycode")).upper()
        gtin = code_barres or clean(wine.get("gtin"))

        vintage = wine.get("vintage") or parse_vintage(nom)
        try:
            vintage = int(vintage) if vintage else None
        except (TypeError, ValueError):
            vintage = None

        return NormalizedWine(
            domaine_nom=domaine or base or "Domaine inconnu",
            cuvee_nom=base or nom or "Cuvée inconnue",
            couleur=couleur_from_type(clean(wine.get("type"))),
            # Région/appellation = ID numérique Vinou non résolu : laissé vide.
            appellation="",
            # grapetypeIds = IDs numériques : pas de noms de cépages exploitables.
            cepages=[],
            millesime=vintage,
            code_barres=gtin,
            source=self.name,
            reference_externe_id=str(wine.get("id") or ""),
            raw={
                "pays": pays,
                "description": clean(wine.get("description")),
                "degre_alcool": _nombre(wine.get("alcohol")),
                "prix": _prix(wine),
            },
        )


# --------------------------------------------------------------------- helpers


def _extract_jwt(body) -> str:
    """Extrait le JWT d'une réponse de login, tolérant à la forme (chaîne nue,
    ``{"token": ...}`` ou enveloppe ``{"data": {"token": ...}}``)."""
    if isinstance(body, str):
        return body.strip()
    if isinstance(body, dict):
        if isinstance(body.get("token"), str):
            return body["token"]
        data = body.get("data")
        if isinstance(data, str):
            return data.strip()
        if isinstance(data, dict) and isinstance(data.get("token"), str):
            return data["token"]
    return ""


def _candidats(data) -> list:
    """Liste de vins candidats depuis le ``data`` de la réponse : liste directe,
    dict enveloppant (``items``/``wines``), ou enregistrement unique."""
    if isinstance(data, list):
        return [w for w in data if isinstance(w, dict)]
    if isinstance(data, dict):
        for key in ("items", "wines", "results"):
            valeur = data.get(key)
            if isinstance(valeur, list):
                return [w for w in valeur if isinstance(w, dict)]
        # Enregistrement unique (getPublic) : présence d'un id/nom.
        if data.get("id") or data.get("name"):
            return [data]
    return []


def _nombre(valeur):
    """Vinou renvoie certains décimaux en chaîne (« 12.0 ») : converti en float."""
    try:
        return float(valeur) if valeur not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _prix(wine: dict) -> float | None:
    """Prix TTC du vin. Au niveau *Service*, Vinou renvoie un tableau ``prices`` de
    fourchettes tarifaires (on prend le plus bas ``gross``) ; en mode public, le prix
    est porté par les champs plats ``gross`` / ``price``."""
    p = _prix_min(wine.get("prices"))
    if p is not None:
        return p
    return _nombre(wine.get("gross") or wine.get("price"))


def _prix_min(prices) -> float | None:
    """Prix TTC (``gross``) le plus bas parmi les fourchettes tarifaires, si présent."""
    if not isinstance(prices, list):
        return None
    montants = [m for m in (_nombre(p.get("gross")) for p in prices if isinstance(p, dict)) if m is not None]
    return min(montants) if montants else None
