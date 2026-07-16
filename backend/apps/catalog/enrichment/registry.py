from __future__ import annotations

from django.core.cache import cache

from .base import EnrichmentError, EnrichmentProvider
from .claude import ClaudeProvider
from .grapeminds import GrapeMindsProvider
from .lwin import LwinProvider
from .openfoodfacts import OpenFoodFactsProvider
from .stubs import CellarTrackerProvider, VivinoProvider
from .vinou import VinouProvider
from .wineapi import WineApiProvider

# Ordre de la cascade d'enrichissement externe.
#  - code-barres (US 01) : Open Food Facts (les autres n'exposent pas le barcode).
#  - texte / étiquette (US 02/03/04) : Claude (vision + connaissances œnologiques)
#    en premier — identification bien plus fiable et fiche plus complète —, puis
#    wineapi.io en repli (et pour ses données marchandes : prix, notes), et enfin
#    le repli 100 % local et gratuit (OCR Tesseract + référentiel LWIN importé),
#    qui garde l'identification fonctionnelle sans aucune clé d'API.
_PROVIDERS: list[EnrichmentProvider] = [
    OpenFoodFactsProvider(),
    ClaudeProvider(),
    WineApiProvider(),
    # GrapeMinds : repli œnologique supplémentaire, en aval de wineapi. Slot
    # désactivé par défaut (cf. GrapeMindsProvider : licence PSL + quota serré) :
    # il n'entre dans la cascade que si GRAPEMINDS_ENABLED est explicitement activé.
    GrapeMindsProvider(),
    # Vinou : catalogue des domaines inscrits sur Vinou (surtout allemands).
    # Slot désactivé par défaut (cf. VinouProvider : auth à valider + couverture
    # de niche) : n'entre dans la cascade que si VINOU_ENABLED est activé.
    VinouProvider(),
    LwinProvider(),
    VivinoProvider(),
    # CellarTracker : pas d'API publique de référentiel (wines.asp = page HTML
    # communautaire) ; scraping contraire aux CGU. Stub désactivé en permanence.
    CellarTrackerProvider(),
]

# Durée de mise en cache du détail wineapi (le profil d'un vin bouge lentement ;
# évite de consommer le quota et la latence à chaque ouverture de fiche).
_DETAIL_TTL = 6 * 60 * 60
# TTL court quand le détail est 'pending' (enrichissement asynchrone côté wineapi
# encore en cours) : on le re-fetch bientôt pour converger vers un détail complet.
_PENDING_TTL = 5 * 60


def get_enabled_providers() -> list[EnrichmentProvider]:
    """Retourne les fournisseurs activés, dans l'ordre de la cascade."""
    return [p for p in _PROVIDERS if p.enabled]


def get_all_providers() -> list[EnrichmentProvider]:
    """Retourne tous les fournisseurs (activés ou non), dans l'ordre de la cascade.

    Utile au panneau d'administration pour afficher l'état (actif / inactif) de
    chaque source d'enrichissement sans divulguer les clés d'API."""
    return list(_PROVIDERS)


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
        detail, pending = provider.wine_detail_with_status(wine_id)  # type: ignore[attr-defined]
    except EnrichmentError:
        return None
    # Un détail 'pending' est partiel : on le garde peu de temps pour le
    # rafraîchir bientôt, au lieu de figer des données incomplètes 6 h.
    cache.set(key, detail or {}, _PENDING_TTL if pending else _DETAIL_TTL)
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
