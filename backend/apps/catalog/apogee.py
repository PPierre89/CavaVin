"""Calcul de la fenêtre de dégustation (apogée) et du statut associé.

Logique métier *pure* (aucune dépendance base de données). À partir de la
couleur d'un vin et de son millésime, on estime une fenêtre de garde typique
(année de début et de fin d'apogée), puis on en déduit un statut — « à garder »,
« à boire » ou « dépassé » — qui pilote le code couleur de la cave.

Les potentiels de garde par couleur suivent des repères sommeliers usuels : un
rouge de garde s'ouvre en général ~3 à ~12 ans après la récolte, un blanc
~1 à ~5 ans, un rosé se boit dans les deux ans, etc. C'est une estimation
déterministe et testable, pas une donnée par cuvée. Une fenêtre saisie
manuellement sur la bouteille prime toujours sur cette estimation (voir
`Bouteille.fenetre_apogee`).
"""

from __future__ import annotations

from datetime import date

# Statuts — miroir de inventory.Bouteille.Statut, redéclarés ici pour garder ce
# module découplé de la couche inventaire (et donc purement testable).
A_GARDER = "A_GARDER"
A_BOIRE = "A_BOIRE"
DEPASSE = "DEPASSE"

# Potentiel de garde par couleur : (années avant le début d'apogée, années
# avant la fin d'apogée), comptées depuis le millésime.
POTENTIEL_GARDE: dict[str, tuple[int, int]] = {
    "ROUGE": (3, 12),
    "BLANC": (1, 5),
    "ROSE": (0, 2),
    "BULLES": (1, 4),
    "AUTRE": (1, 6),
}
_DEFAUT = POTENTIEL_GARDE["AUTRE"]


def fenetre_apogee(couleur: str, millesime: int | None) -> tuple[int | None, int | None]:
    """Estime (année de début, année de fin) d'apogée depuis couleur et millésime.

    Renvoie ``(None, None)`` pour un vin non millésimé : sans année de récolte,
    la fenêtre ne peut pas être datée.
    """
    if not millesime:
        return (None, None)
    debut_offset, fin_offset = POTENTIEL_GARDE.get(couleur, _DEFAUT)
    return (millesime + debut_offset, millesime + fin_offset)


def statut_pour_fenetre(
    debut: int | None, fin: int | None, annee: int | None = None
) -> str:
    """Déduit le statut (à garder / à boire / dépassé) d'une fenêtre d'apogée.

    ``annee`` vaut l'année courante par défaut. Une fenêtre partielle (une seule
    borne connue) est gérée : seule la borne présente contraint le statut. Sans
    aucune borne (vin non datable), on retombe sur « à garder », le statut neutre
    qui n'allume aucune alerte de couleur.
    """
    if annee is None:
        annee = date.today().year
    if debut is not None and annee < debut:
        return A_GARDER
    if fin is not None and annee > fin:
        return DEPASSE
    if debut is None and fin is None:
        return A_GARDER
    return A_BOIRE
