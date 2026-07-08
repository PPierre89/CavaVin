"""Conseils de dégustation dérivés de la couleur du vin.

Logique métier *pure* (aucune dépendance base de données) : température de
service, carafage, profil gustatif type et accords mets-vins sont déduits de la
couleur de la cuvée. C'est une base de connaissance sommelière déterministe, pas
des données de démonstration — elle est directement testable et sert la fiche
vin tant que le référentiel n'expose pas de profil par cuvée.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Cuvee

Couleur = Cuvee.Couleur


@dataclass(frozen=True)
class AxeGustatif:
    """Un axe du profil gustatif, de 0 (pôle gauche) à 1 (pôle droit)."""

    gauche: str
    droite: str
    valeur: float


@dataclass(frozen=True)
class Accord:
    nom: str
    emoji: str


@dataclass(frozen=True)
class ConseilDegustation:
    """Recommandation de service pour une couleur donnée."""

    temperature: str
    carafage: str
    gustatif: list[AxeGustatif] = field(default_factory=list)
    accords: list[Accord] = field(default_factory=list)


def _axes(puissance: float, tanin: float, acidite: float) -> list[AxeGustatif]:
    return [
        AxeGustatif("Léger", "Puissant", puissance),
        AxeGustatif("Souple", "Tannique", tanin),
        AxeGustatif("Doux", "Acide", acidite),
    ]


# Table de correspondance couleur -> conseil. Les valeurs (températures, accords)
# suivent les usages sommeliers classiques.
_CONSEILS: dict[str, ConseilDegustation] = {
    Couleur.ROUGE: ConseilDegustation(
        temperature="16-18",
        carafage="1h-2h",
        gustatif=_axes(0.8, 0.75, 0.5),
        accords=[Accord("Bœuf", "🥩"), Accord("Agneau", "🍖"), Accord("Gibier", "🦌"), Accord("Fromage affiné", "🧀")],
    ),
    Couleur.BLANC: ConseilDegustation(
        temperature="8-11",
        carafage="Non requis",
        gustatif=_axes(0.4, 0.2, 0.7),
        accords=[Accord("Poisson", "🐟"), Accord("Volaille", "🍗"), Accord("Fruits de mer", "🦐"), Accord("Fromage de chèvre", "🧀")],
    ),
    Couleur.ROSE: ConseilDegustation(
        temperature="8-10",
        carafage="Non requis",
        gustatif=_axes(0.35, 0.2, 0.6),
        accords=[Accord("Salade", "🥗"), Accord("Grillades", "🍢"), Accord("Cuisine épicée", "🌶️"), Accord("Charcuterie", "🥓")],
    ),
    Couleur.BULLES: ConseilDegustation(
        temperature="6-8",
        carafage="Non requis",
        gustatif=_axes(0.45, 0.15, 0.75),
        accords=[Accord("Apéritif", "🥂"), Accord("Fruits de mer", "🦪"), Accord("Sushi", "🍣"), Accord("Dessert", "🍰")],
    ),
    Couleur.AUTRE: ConseilDegustation(
        temperature="10-14",
        carafage="Selon le vin",
        gustatif=_axes(0.5, 0.4, 0.5),
        accords=[Accord("Cuisine variée", "🍽️"), Accord("Fromages", "🧀"), Accord("Charcuterie", "🥓")],
    ),
}


def conseil_pour_couleur(couleur: str) -> ConseilDegustation:
    """Renvoie le conseil de dégustation pour une couleur (défaut : AUTRE)."""
    return _CONSEILS.get(couleur, _CONSEILS[Couleur.AUTRE])
