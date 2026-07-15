from __future__ import annotations

import base64
import json
import logging

import anthropic
from django.conf import settings

from .. import wine_profile
from ..runtime_config import get_parametre
from .base import EnrichmentError, EnrichmentProvider, NormalizedWine
from .normalize import clean, couleur_from_type, strip_vintage

logger = logging.getLogger(__name__)

# Formats d'image acceptés par l'API Anthropic (le sérialiseur amont accepte
# déjà uniquement JPEG/PNG, on reste tolérant pour WebP/GIF).
_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# Instructions système : identifier le vin et restituer un profil factuel.
# On interdit explicitement les données volatiles (prix, notes) que le modèle
# ne peut pas connaître de façon fiable — elles restent vides plutôt
# qu'inventées.
_SYSTEM = (
    "Tu es un sommelier expert chargé d'identifier un vin et de remplir sa fiche.\n"
    "À partir d'une photo d'étiquette ou d'une recherche texte, identifie le vin "
    "(domaine/producteur, cuvée, appellation, millésime) puis complète son profil "
    "à partir de tes connaissances œnologiques : région, pays, classification, "
    "cépages typiques de la cuvée ou de l'appellation, corps, acidité, degré "
    "d'alcool typique, courte description (2 à 3 phrases, en français) et accords "
    "mets-vins (noms de plats en français, confiance entre 0 et 1).\n"
    "Règles :\n"
    "- N'invente jamais de prix, de note ou d'avis critique.\n"
    "- Si une information est incertaine (ex. millésime illisible), laisse-la nulle.\n"
    "- `identifie` vaut false si l'entrée n'est pas un vin identifiable "
    "(photo qui n'est pas une étiquette de vin, texte sans rapport) ; dans ce cas "
    "`vin` est null.\n"
    "- `confiance` reflète ta certitude sur l'identification (0 à 1)."
)

# Schéma de sortie structurée. Le sous-objet `vin` reprend volontairement le
# format de détail wineapi (`GET /wines/{id}`) : il est ainsi consommé tel quel
# par `wine_profile.normalize_detail` et persisté par `ingest.upsert_cuvee`,
# sans mapping supplémentaire.
_NULLABLE_STR = {"type": ["string", "null"]}
_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["identifie", "confiance", "vin"],
    "properties": {
        "identifie": {"type": "boolean"},
        "confiance": {"type": ["number", "null"]},
        "vin": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "name", "vintage", "type", "winery", "region",
                        "appellation", "classification", "grapes", "body",
                        "acidity", "alcoholContent", "description", "pairings",
                    ],
                    "properties": {
                        "name": {"type": "string", "description": "Nom du vin sans le millésime"},
                        "vintage": {"type": ["integer", "null"]},
                        "type": {
                            "anyOf": [
                                {
                                    "type": "string",
                                    "enum": ["red", "white", "rosé", "sparkling", "dessert", "fortified"],
                                },
                                {"type": "null"},
                            ]
                        },
                        "winery": {"type": "string", "description": "Domaine / producteur"},
                        "region": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "country"],
                            "properties": {
                                "name": _NULLABLE_STR,
                                "country": _NULLABLE_STR,
                            },
                        },
                        "appellation": _NULLABLE_STR,
                        "classification": _NULLABLE_STR,
                        "grapes": {"type": "array", "items": {"type": "string"}},
                        "body": {
                            "anyOf": [
                                {"type": "string", "enum": ["Light-bodied", "Medium-bodied", "Full-bodied"]},
                                {"type": "null"},
                            ]
                        },
                        "acidity": {
                            "anyOf": [
                                {"type": "string", "enum": ["Low", "Medium", "High"]},
                                {"type": "null"},
                            ]
                        },
                        "alcoholContent": {"type": ["number", "null"]},
                        "description": _NULLABLE_STR,
                        "pairings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["food", "confidence"],
                                "properties": {
                                    "food": {"type": "string"},
                                    "confidence": {"type": ["number", "null"]},
                                },
                            },
                        },
                    },
                },
            ]
        },
    },
}


class ClaudeProvider(EnrichmentProvider):
    """
    Claude (Anthropic) — identification par photo d'étiquette (vision) et par
    texte, avec enrichissement œnologique issu des connaissances du modèle.

    Remplace wineapi.io en tête de cascade : la lecture d'étiquette est bien
    plus fiable (vision multimodale) et le profil renvoyé est complet (cépages,
    corps, acidité, accords, description) au lieu d'être souvent lacunaire.
    Les données volatiles que le modèle ne peut pas connaître (prix marchands,
    notes communautaires) ne sont volontairement pas demandées ; wineapi reste
    dans la cascade en repli et peut les compléter.

    La sortie est contrainte par un schéma JSON (structured outputs) au format
    du détail wineapi, si bien que la persistance (`ingest.upsert_cuvee` +
    `wine_profile.normalize_detail`) est réutilisée sans adaptation. Le
    provider est désactivé si aucune clé ANTHROPIC_API_KEY n'est configurée.
    """

    name = "claude"

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        return bool(get_parametre("ANTHROPIC_API_KEY"))

    def _client(self) -> anthropic.Anthropic:
        # max_retries=1 : le SDK réessaie les 429/5xx ; au-delà on préfère
        # rendre la main à la cascade plutôt que de consommer le budget du
        # worker gunicorn (le timeout total peut atteindre timeout × (retries+1)).
        return anthropic.Anthropic(
            api_key=get_parametre("ANTHROPIC_API_KEY"),
            timeout=settings.ANTHROPIC_TIMEOUT,
            max_retries=1,
        )

    def lookup_by_text(self, query: str) -> NormalizedWine | None:
        """US 04 — identification à partir d'un nom / d'une saisie libre."""
        blocs = [{"type": "text", "text": f"Identifie ce vin : {query}"}]
        return self._identifier(blocs)

    def lookup_by_image(self, data: bytes, content_type: str) -> NormalizedWine | None:
        """US 02/03 — identification par photo d'étiquette (vision)."""
        media_type = (content_type or "").split(";")[0].strip().lower()
        if media_type not in _MEDIA_TYPES:
            media_type = "image/jpeg"
        blocs = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(data).decode("ascii"),
                },
            },
            {"type": "text", "text": "Identifie le vin sur cette étiquette."},
        ]
        return self._identifier(blocs)

    def _identifier(self, blocs: list[dict]) -> NormalizedWine | None:
        """Appel Claude commun texte/image -> NormalizedWine (ou None si miss)."""
        try:
            response = self._client().messages.create(
                model=get_parametre("ANTHROPIC_MODEL"),
                max_tokens=2048,
                system=_SYSTEM,
                # Adaptatif : le modèle dose lui-même sa réflexion (lecture d'une
                # étiquette dégradée vs simple recherche texte).
                thinking={"type": "adaptive"},
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": _SCHEMA},
                },
                messages=[{"role": "user", "content": blocs}],
            )
        except anthropic.RateLimitError as exc:
            raise EnrichmentError(429, "Quota Anthropic atteint, réessaie plus tard.") from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise EnrichmentError(502, "Clé Anthropic invalide (configuration serveur).") from exc
        except anthropic.APIStatusError as exc:
            # 400 (image trop lourde…) / 5xx / autres : simple 'non trouvé'.
            logger.warning("claude identify -> HTTP %s", exc.status_code)
            return None
        except anthropic.APIConnectionError as exc:
            logger.warning("claude identify: %s", exc)
            return None

        if response.stop_reason == "refusal":
            logger.warning("claude identify: requête refusée par les garde-fous")
            return None
        texte = next((b.text for b in response.content if b.type == "text"), "")
        try:
            resultat = json.loads(texte)
        except (TypeError, ValueError):
            logger.warning("claude identify: réponse JSON invalide")
            return None
        return self._to_normalized(resultat)

    def _to_normalized(self, resultat: dict) -> NormalizedWine | None:
        """Réponse structurée Claude -> NormalizedWine (format wineapi réutilisé)."""
        detail = resultat.get("vin")
        if not resultat.get("identifie") or not isinstance(detail, dict):
            return None

        # Mapping unique du détail -> champs de fiche (source de vérité partagée
        # avec la persistance : cf. wine_profile.normalize_detail).
        flat = wine_profile.normalize_detail(detail)

        name = clean(detail.get("name"))
        base = strip_vintage(name)
        winery = clean(detail.get("winery"))
        if not base and not winery:
            return None

        return NormalizedWine(
            domaine_nom=winery or base or "Domaine inconnu",
            cuvee_nom=base or name or "Cuvée inconnue",
            couleur=couleur_from_type(detail.get("type")),
            appellation=clean(detail.get("appellation")) or flat.get("region", ""),
            cepages=flat.get("cepages") or [],
            millesime=detail.get("vintage"),
            source=self.name,
            reference_externe_id="",  # pas de source distante à re-synchroniser
            raw={
                "confidence": resultat.get("confiance"),
                "auto_added": False,
                "pending": False,
                "suggestions": [],
                # Aperçu enrichi renvoyé dans la réponse d'identification.
                "region": flat.get("region", ""),
                "pays": flat.get("pays", ""),
                "description": flat.get("description", ""),
                "note": None,
                "alcool": flat.get("degre_alcool"),
                "prix": None,
                "prix_marchands": [],
                # Détail complet (format wineapi), persisté par ingest.upsert_cuvee.
                "wineapi_detail": detail,
            },
        )
