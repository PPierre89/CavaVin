from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field


class EnrichmentError(Exception):
    """
    Erreur d'un fournisseur qui doit remonter à l'utilisateur (quota, clé invalide),
    par opposition à un simple 'non trouvé' (qui, lui, renvoie None).
    `status` est le code HTTP à propager côté API.
    """

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class NormalizedWine:
    """Résultat normalisé d'une recherche fournisseur, prêt à mapper sur Domaine/Cuvee."""

    domaine_nom: str
    cuvee_nom: str
    couleur: str = "AUTRE"  # une des valeurs de Cuvee.Couleur
    appellation: str = ""
    cepages: list[str] = field(default_factory=list)
    millesime: int | None = None
    code_barres: str = ""
    source: str = ""  # nom du fournisseur ayant répondu
    reference_externe_id: str = ""
    # Confiance propre à CE relevé (0 à 1), quand le fournisseur sait la qualifier
    # (ex: score de correspondance floue LWIN). None => la confiance a priori du
    # canal s'applique (cf. ingest._confiance_pour). Alimente la consolidation.
    confiance: float | None = None
    raw: dict = field(default_factory=dict)


class EnrichmentProvider(ABC):
    """
    Interface enfichable pour l'enrichissement de fiches vin.

    Chaque source externe (Open Food Facts, wineapi.io, ...) implémente cette
    interface. La cascade de recherche ignore les fournisseurs désactivés
    (``enabled = False``), ce qui permet d'ajouter un slot sans l'activer tant
    que ses conditions d'utilisation / endpoints n'ont pas été validés.
    """

    name: str = "base"
    enabled: bool = False

    def lookup_by_barcode(self, ean: str) -> NormalizedWine | None:
        """Recherche par code-barres (US 01). None si non supporté / non trouvé."""
        return None

    def lookup_by_text(self, query: str) -> NormalizedWine | None:
        """Recherche par texte libre / sortie OCR (US 03/04). None si non supporté / non trouvé."""
        return None

    def lookup_by_image(self, data: bytes, content_type: str) -> NormalizedWine | None:
        """Identification par photo d'étiquette (US 02/03). None si non supporté / non trouvé."""
        return None
