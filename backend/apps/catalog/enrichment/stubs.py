from __future__ import annotations

from .base import EnrichmentProvider, NormalizedWine


class VivinoProvider(EnrichmentProvider):
    """
    Slot pour Vivino — DÉSACTIVÉ par défaut, et à activer en connaissance de cause.

    Vivino n'expose pas d'API publique. Requêter leur API interne ou scraper leur
    site viole leurs conditions d'utilisation, casse à chaque évolution de leur
    front, et comporte un risque juridique en cas de redistribution des données.
    Ce slot existe pour rester enfichable, pas pour être une dépendance centrale.

    Note : wineapi.io fournit déjà cépages / appellation / notes critiques, ce qui
    couvre le besoin d'origine de l'US 05 sans recourir au scraping.
    """

    name = "vivino"
    enabled = False

    def lookup_by_barcode(self, ean: str) -> NormalizedWine | None:  # pragma: no cover
        return None
