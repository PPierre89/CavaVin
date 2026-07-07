from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

from .base import EnrichmentError, EnrichmentProvider, NormalizedWine
from .normalize import clean, couleur_from_type, named, strip_vintage

logger = logging.getLogger(__name__)


def _grape_names(grapes) -> list[str]:
    """Normalise la liste de cépages (accepte des str ou des objets {name})."""
    out = []
    for g in grapes or []:
        nom = named(g) if isinstance(g, dict) else clean(g)
        if nom:
            out.append(nom)
    return out


class WineApiProvider(EnrichmentProvider):
    """
    wineapi.io — identification d'un vin à partir de texte (nom, domaine, sortie OCR).

    Auth par header ``X-API-Key`` (clé chargée depuis l'environnement). Deux appels :
    ``POST /identify/text`` pour identifier, puis ``GET /wines/{id}`` (best-effort)
    pour récupérer cépages / appellation / notes. Le provider est désactivé si
    aucune clé n'est configurée.
    """

    name = "wineapi"

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        return bool(settings.WINEAPI_KEY)

    def _request(self, method: str, path: str, payload: dict | None = None):
        url = settings.WINEAPI_BASE_URL.rstrip("/") + path
        headers = {"X-API-Key": settings.WINEAPI_KEY, "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=settings.WINEAPI_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Erreurs à faire remonter clairement (cf. spec : 401 / 429).
            if exc.code == 429:
                raise EnrichmentError(429, "Quota wineapi.io atteint, réessaie plus tard.") from exc
            if exc.code == 401:
                raise EnrichmentError(502, "Clé wineapi.io invalide (configuration serveur).") from exc
            # 400 / 5xx / autres : on traite comme un simple 'non trouvé'.
            logger.warning("wineapi %s %s -> HTTP %s", method, path, exc.code)
            return None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            logger.warning("wineapi %s %s: %s", method, path, exc)
            return None

    def lookup_by_text(self, query: str) -> NormalizedWine | None:
        result = self._request("POST", "/identify/text", {"query": query})
        if not result:
            return None
        wine = result.get("wine")
        if not wine:
            return None
        suggestions = [
            clean(s.get("name", ""))
            for s in (result.get("suggestions") or [])
            if isinstance(s, dict) and s.get("name")
        ]
        return self._to_normalized(
            wine, confidence=result.get("confidence"), suggestions=suggestions
        )

    def _to_normalized(self, wine: dict, confidence, suggestions) -> NormalizedWine:
        detail = {}
        wine_id = wine.get("id")
        if settings.WINEAPI_ENRICH_DETAIL and wine_id:
            detail = self._request("GET", f"/wines/{wine_id}") or {}

        wine_type = detail.get("type") or wine.get("type")
        name = clean(detail.get("name") or wine.get("name"))
        base = strip_vintage(name)
        winery = named(detail.get("winery"))

        region_obj = detail.get("region") or wine.get("region")
        region = named(region_obj)
        country = (
            clean(region_obj.get("country", "")) if isinstance(region_obj, dict) else clean(wine.get("country"))
        )
        appellation = named(detail.get("appellation")) or region

        scores = detail.get("scores") or []
        note = max(
            (s.get("score") for s in scores if isinstance(s, dict) and s.get("score")),
            default=None,
        )

        return NormalizedWine(
            domaine_nom=winery or base or "Domaine inconnu",
            cuvee_nom=base or name or "Cuvée inconnue",
            couleur=couleur_from_type(wine_type),
            appellation=appellation,
            cepages=_grape_names(detail.get("grapes")),
            millesime=wine.get("vintage") or detail.get("vintage"),
            source=self.name,
            reference_externe_id=str(wine_id or ""),
            raw={
                "confidence": confidence,
                "region": region,
                "country": country,
                "description": clean(detail.get("description")),
                "note": note,
                "alcool": detail.get("alcoholContent"),
                "prix": detail.get("priceRange"),
                "suggestions": suggestions,
                "wineapi_type": wine_type,
            },
        )
