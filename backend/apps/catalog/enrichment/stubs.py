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


class CellarTrackerProvider(EnrichmentProvider):
    """
    Slot pour CellarTracker — DÉSACTIVÉ en permanence, pour la même raison que Vivino.

    CellarTracker n'expose pas d'API publique de référentiel : la page
    ``/wines.asp`` est une page HTML communautaire (note moyenne, notes de
    dégustation, fenêtre de garde). En « récupérer des informations » reviendrait à
    scraper cette page — ce qu'interdisent leurs conditions d'utilisation (données
    communautaires, réutilisation non autorisée), ce qui casse à chaque évolution de
    leur front et comporte un risque juridique en cas de redistribution.

    Le seul accès programmatique officiel (``xlquery.asp``) renvoie les données du
    compte connecté (sa propre cave, ses propres notes) via ses identifiants : c'est
    un export personnel, pas un catalogue interrogeable pour enrichir des fiches. Un
    éventuel import de sa cave CellarTracker relève donc du tier privé (import de
    bouteilles/notes), pas de cette cascade d'enrichissement.
    """

    name = "cellartracker"
    enabled = False

    def lookup_by_text(self, query: str) -> NormalizedWine | None:  # pragma: no cover
        return None
