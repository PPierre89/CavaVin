from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid

from django.conf import settings

from .. import wine_profile
from .base import EnrichmentError, EnrichmentProvider, NormalizedWine
from .normalize import clean, couleur_from_type, named, strip_vintage

logger = logging.getLogger(__name__)


class WineApiProvider(EnrichmentProvider):
    """
    wineapi.io — identification d'un vin à partir de texte (nom, domaine, sortie OCR).

    Auth par header ``X-API-Key`` (clé chargée depuis l'environnement). Deux appels :
    ``POST /identify/text`` pour identifier, puis ``GET /wines/{id}`` (best-effort)
    pour récupérer le détail complet. Le mapping de ce détail vers les champs de
    fiche est centralisé dans ``wine_profile.normalize_detail`` (source de vérité
    unique, partagée avec la persistance). Le provider est désactivé si aucune
    clé n'est configurée.

    Informations remontées par ``GET /wines/{id}`` (cf. OpenAPI wineapi.io) :
    id, name, vintage, type, body, acidity, elaborate, classification,
    averageRating, ratingsCount, winery{id,name}, region{id,name,country},
    appellation, grapes[{id,name,color}], alcoholContent, description, lwinCode,
    imageUrl, priceRange{min,max,currency}, prices[{merchantName,price,currency,
    url,fetchedAt}], scores[{score,scoreText,reviewer,reviewDate}] et
    pairings[{food,confidence,notes}]. Un header ``X-Update-Status: pending``
    signale un détail encore incomplet (enrichissement asynchrone en cours).
    """

    name = "wineapi"

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        return bool(settings.WINEAPI_KEY)

    def _request(self, method: str, path: str, payload: dict | None = None):
        """Appel JSON simple — renvoie le corps décodé (ou None)."""
        return self._request_full(method, path, payload)[0]

    def _request_full(self, method: str, path: str, payload: dict | None = None):
        """Comme ``_request`` mais renvoie ``(corps, headers)`` pour inspecter
        les en-têtes de réponse (ex. ``X-Update-Status``)."""
        headers = {"X-API-Key": settings.WINEAPI_KEY, "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return self._open(method, path, data, headers)

    def _request_multipart(self, path: str, field: str, filename: str, data: bytes, content_type: str):
        """POST multipart/form-data (upload d'image) via la stdlib, sans dépendance."""
        boundary = "----cavavin" + uuid.uuid4().hex
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
            + data
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        )
        headers = {
            "X-API-Key": settings.WINEAPI_KEY,
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        # L'identification par image (vision) est bien plus lente que le texte.
        return self._open("POST", path, body, headers, timeout=settings.WINEAPI_IMAGE_TIMEOUT)[0]

    def _open(self, method: str, path: str, data: bytes | None, headers: dict, timeout: int | None = None):
        """Exécute la requête et renvoie ``(corps JSON | None, headers)``.

        Les headers sont toujours renvoyés (dict vide en cas d'erreur) pour que
        l'appelant puisse lire ``X-Update-Status`` sans re-parser la réponse."""
        url = settings.WINEAPI_BASE_URL.rstrip("/") + path
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or settings.WINEAPI_TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body, dict(resp.headers)
        except urllib.error.HTTPError as exc:
            # Erreurs à faire remonter clairement (cf. spec : 401 / 429).
            if exc.code == 429:
                raise EnrichmentError(429, "Quota wineapi.io atteint, réessaie plus tard.") from exc
            if exc.code == 401:
                raise EnrichmentError(502, "Clé wineapi.io invalide (configuration serveur).") from exc
            # 400 / 404 / 5xx / autres : on traite comme un simple 'non trouvé'.
            logger.warning("wineapi %s %s -> HTTP %s", method, path, exc.code)
            return None, {}
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            logger.warning("wineapi %s %s: %s", method, path, exc)
            return None, {}

    def wine_detail(self, wine_id: str) -> dict | None:
        """`GET /wines/{id}` — profil complet (notes, corps, accords, prix)."""
        return self.wine_detail_with_status(wine_id)[0]

    def wine_detail_with_status(self, wine_id: str) -> tuple[dict | None, bool]:
        """`GET /wines/{id}` — renvoie ``(détail, pending)``.

        ``pending`` vaut ``True`` quand wineapi renvoie ``X-Update-Status: pending``:
        le vin vient d'être ajouté et son enrichissement tourne encore en tâche de
        fond ; le détail est alors partiel et mérite un re-fetch ultérieur."""
        if not wine_id:
            return None, False
        body, resp_headers = self._request_full("GET", f"/wines/{wine_id}")
        pending = str(resp_headers.get("X-Update-Status", "")).lower() == "pending"
        return body, pending

    def lookup_by_text(self, query: str) -> NormalizedWine | None:
        result = self._request("POST", "/identify/text", {"query": query})
        return self._from_identify_result(result)

    def lookup_by_image(self, data: bytes, content_type: str) -> NormalizedWine | None:
        """US 02/03 — identification par photo d'étiquette (JPEG/PNG ≤ 10 Mo)."""
        ext = "png" if "png" in (content_type or "") else "jpg"
        result = self._request_multipart(
            "/identify/image", "image", f"etiquette.{ext}", data, content_type
        )
        return self._from_identify_result(result)

    def _from_identify_result(self, result) -> NormalizedWine | None:
        """Réponse commune de /identify/text et /identify/image -> NormalizedWine."""
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
            wine,
            confidence=result.get("confidence"),
            suggestions=suggestions,
            auto_added=bool(result.get("autoAdded")),
            pending=bool(result.get("pendingEnrichment")),
        )

    def _to_normalized(self, wine, confidence, suggestions, auto_added=False, pending=False) -> NormalizedWine:
        detail = {}
        wine_id = wine.get("id")
        if settings.WINEAPI_ENRICH_DETAIL and wine_id:
            detail, detail_pending = self.wine_detail_with_status(wine_id)
            detail = detail or {}
            # Détail marqué 'pending' par l'API ou signalé à l'identification :
            # les données sont partielles, on ne les fige pas en base (enrichi_le).
            pending = pending or detail_pending

        # Mapping unique du détail wineapi -> champs de fiche (source de vérité
        # partagée avec la persistance : cf. wine_profile.normalize_detail).
        flat = wine_profile.normalize_detail(detail) if detail else {}

        wine_type = detail.get("type") or wine.get("type")
        name = clean(detail.get("name") or wine.get("name"))
        base = strip_vintage(name)
        winery = named(detail.get("winery")) or named(wine.get("winery"))

        region = flat.get("region") or named(wine.get("region"))
        pays = flat.get("pays") or _country_of(wine)
        # L'API renvoie `appellation` en chaîne ; `named` reste tolérant aux
        # anciennes réponses objet {name}.
        appellation = named(detail.get("appellation")) or named(wine.get("appellation")) or region

        scores = flat.get("scores") or []
        note = max(
            (s["score"] for s in scores if isinstance(s, dict) and s.get("score") is not None),
            default=None,
        )

        return NormalizedWine(
            domaine_nom=winery or base or "Domaine inconnu",
            cuvee_nom=base or name or "Cuvée inconnue",
            couleur=couleur_from_type(wine_type),
            appellation=appellation,
            cepages=flat.get("cepages") or [],
            millesime=wine.get("vintage") or detail.get("vintage"),
            source=self.name,
            reference_externe_id=str(wine_id or ""),
            raw={
                "confidence": confidence,
                "auto_added": auto_added,
                "pending": pending,
                "suggestions": suggestions,
                # Aperçu enrichi renvoyé dans la réponse d'identification.
                "region": region,
                "pays": pays,
                "description": flat.get("description", ""),
                "note": note,
                "alcool": flat.get("degre_alcool"),
                "prix": wine_profile.prix_marche(detail) if detail else None,
                "prix_marchands": flat.get("prix_marchands") or [],
                # Détail complet, persisté sur la cuvée par ingest.upsert_cuvee.
                # Écarté si 'pending' pour laisser la fiche re-fetcher un détail complet.
                "wineapi_detail": detail if (detail and not pending) else None,
            },
        )


def _country_of(wine: dict) -> str:
    """Pays d'un vin d'identification : ``region`` peut être un objet ou un str
    (dans ce cas le pays est porté par un champ ``country`` frère)."""
    region = wine.get("region")
    if isinstance(region, dict):
        return clean(region.get("country"))
    return clean(wine.get("country"))
