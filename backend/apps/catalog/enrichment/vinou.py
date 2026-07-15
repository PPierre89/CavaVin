from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

from .base import EnrichmentError, EnrichmentProvider, NormalizedWine
from .normalize import clean, couleur_from_type, parse_vintage, strip_vintage

logger = logging.getLogger(__name__)


class VinouProvider(EnrichmentProvider):
    """
    Vinou (``api.vinou.de``) — catalogue de vins alimenté par les domaines inscrits
    sur la plateforme Vinou (majoritairement allemands). Identification par texte
    (``POST /wines/search``) et par code-barres (filtre sur le champ ``gtin``).

    Toutes les routes sont en **POST JSON** ; la réponse est enveloppée
    ``{"info": "success", "data": ...}``. Les routes ``/wines/search`` et
    ``/wines/getPublic`` sont publiques ; les routes ``/service/...`` exigent un
    jeton de service (non utilisées ici). Un jeton optionnel (``VINOU_TOKEN``) est
    envoyé en ``Authorization: Bearer`` s'il est configuré.

    ⚠️ **Slot désactivé par défaut** (``VINOU_ENABLED``). Deux raisons :
      1. **Handshake d'auth à confirmer** — la page « Authentication » de la doc
         n'était pas fournie : le mécanisme exact (jeton anonyme ? en-tête ?) doit
         être validé en conditions réelles avant d'activer.
      2. **Couverture de niche** — Vinou expose les vins de ses domaines clients
         (surtout allemands), pas un référentiel mondial : utile en complément, mais
         faible taux de correspondance sur un vin quelconque.

    ⚠️ **Champs à IDs non résolus** : ``region`` et ``grapetypeIds`` sont des
    identifiants numériques Vinou (pas des noms) ; sans table de correspondance
    dédiée, on ne peut ni nommer la région/appellation ni les cépages. On mappe donc
    ce qui est directement exploitable (nom, domaine, couleur, pays, millésime,
    code-barres, alcool, description).
    """

    name = "vinou"

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        # Opt-in explicite (cf. docstring : auth à valider + couverture de niche).
        # Le jeton reste optionnel : les routes /wines/search sont publiques.
        return bool(settings.VINOU_ENABLED)

    # ------------------------------------------------------------------ HTTP

    def _post(self, path: str, payload: dict) -> dict | None:
        """POST JSON — renvoie le contenu de ``data`` (ou None en cas de miss)."""
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if settings.VINOU_TOKEN:
            headers["Authorization"] = f"Bearer {settings.VINOU_TOKEN}"
        url = settings.VINOU_BASE_URL.rstrip("/") + path
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=settings.VINOU_TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise EnrichmentError(429, "Quota Vinou atteint, réessaie plus tard.") from exc
            if exc.code in (401, 403):
                raise EnrichmentError(502, "Jeton Vinou invalide (configuration serveur).") from exc
            logger.warning("vinou POST %s -> HTTP %s", path, exc.code)
            return None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            logger.warning("vinou POST %s: %s", path, exc)
            return None
        # L'enveloppe standard Vinou : {"info": "success", "data": ...}.
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
        # Le champ `gtin` porte le code-barres : filtre exact sur ce champ.
        code = clean(ean)
        if not code:
            return None
        data = self._post(
            "/wines/search",
            {"filter": {"gtin": code}, "pageSize": settings.VINOU_SEARCH_LIMIT},
        )
        wine = self._premier(data, code_barres=code)
        return wine

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
                "prix": _prix_min(wine.get("prices")),
            },
        )


# --------------------------------------------------------------------- helpers


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


def _prix_min(prices) -> float | None:
    """Prix TTC (``gross``) le plus bas parmi les fourchettes tarifaires, si présent."""
    if not isinstance(prices, list):
        return None
    montants = [m for m in (_nombre(p.get("gross")) for p in prices if isinstance(p, dict)) if m is not None]
    return min(montants) if montants else None
