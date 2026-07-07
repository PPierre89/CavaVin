from __future__ import annotations

from .base import EnrichmentProvider
from .openfoodfacts import OpenFoodFactsProvider
from .stubs import VivinoProvider
from .wineapi import WineApiProvider

# Ordre de la cascade d'enrichissement externe.
#  - code-barres (US 01) : Open Food Facts (les autres n'exposent pas le barcode).
#  - texte / OCR (US 03/04) : wineapi.io.
_PROVIDERS: list[EnrichmentProvider] = [
    OpenFoodFactsProvider(),
    WineApiProvider(),
    VivinoProvider(),
]


def get_enabled_providers() -> list[EnrichmentProvider]:
    """Retourne les fournisseurs activés, dans l'ordre de la cascade."""
    return [p for p in _PROVIDERS if p.enabled]
