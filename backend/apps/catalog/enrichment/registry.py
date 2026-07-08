from __future__ import annotations

from django.core.cache import cache

from .base import EnrichmentError, EnrichmentProvider
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

# Durée de mise en cache du détail wineapi (le profil d'un vin bouge lentement ;
# évite de consommer le quota et la latence à chaque ouverture de fiche).
_DETAIL_TTL = 6 * 60 * 60


def get_enabled_providers() -> list[EnrichmentProvider]:
    """Retourne les fournisseurs activés, dans l'ordre de la cascade."""
    return [p for p in _PROVIDERS if p.enabled]


def get_provider(name: str) -> EnrichmentProvider | None:
    """Retourne un fournisseur par son nom (activé uniquement), sinon None."""
    return next((p for p in _PROVIDERS if p.name == name and p.enabled), None)


def wineapi_detail(wine_id: str) -> dict | None:
    """Détail wineapi d'un vin (`GET /wines/{id}`), mis en cache.

    Best-effort : renvoie None si wineapi est désactivé, en erreur (quota…) ou
    si le vin est inconnu. Un résultat vide est mis en cache pour ne pas
    re-solliciter l'API en boucle."""
    if not wine_id:
        return None
    key = f"wineapi:detail:{wine_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    provider = get_provider("wineapi")
    if provider is None:
        return None
    try:
        detail = provider.wine_detail(wine_id)  # type: ignore[attr-defined]
    except EnrichmentError:
        return None
    cache.set(key, detail or {}, _DETAIL_TTL)
    return detail or None


def refresh_wineapi_detail(wine_id: str) -> dict | None:
    """Force le rafraîchissement du détail wineapi en ignorant le cache.

    Utilisé par le bouton de synchro de la fiche. L'appelant est responsable
    du garde-fou anti-quota (cooldown) : cette fonction re-sollicite wineapi
    à chaque appel."""
    if not wine_id:
        return None
    cache.delete(f"wineapi:detail:{wine_id}")
    return wineapi_detail(wine_id)
