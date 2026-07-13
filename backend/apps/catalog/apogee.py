"""Calcul de la fenêtre de dégustation (apogée) et du statut associé.

Logique métier *pure* (aucune dépendance base de données). À partir de la
couleur d'un vin et de son millésime, on estime une fenêtre de garde typique
(année de début et de fin d'apogée), puis on en déduit un statut — « à garder »,
« à boire » ou « dépassé » — qui pilote le code couleur de la cave.

Les potentiels de garde par couleur suivent des repères sommeliers usuels : un
rouge de garde s'ouvre en général ~3 à ~12 ans après la récolte, un blanc
~1 à ~5 ans, un rosé se boit dans les deux ans, etc. Cette base couleur est
ensuite *affinée* par deux facteurs, quand l'information est disponible :

- **le cépage** — un Cabernet Sauvignon ou un Nebbiolo se garde bien plus
  longtemps qu'un Gamay ; un Riesling ou un Chenin vieillissent quand un
  Sauvignon se boit jeune. Pour un assemblage, c'est le cépage le plus taillé
  pour la garde qui donne le tempo (on retient le facteur maximal).
- **la qualité du millésime** — un grand millésime demande plus de patience et
  se conserve plus longtemps ; un petit millésime se boit plus tôt. Les notes
  s'appuient sur une table indicative de millésimes par grande région.

C'est une estimation déterministe et testable, pas une donnée par cuvée. Une
fenêtre saisie manuellement sur la bouteille prime toujours sur cette
estimation (voir `Bouteille.fenetre_apogee`).
"""

from __future__ import annotations

import unicodedata
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

# Facteur de garde par cépage : multiplicateur appliqué au potentiel couleur.
# >1 = cépage de garde (structure, tanins, acidité) ; <1 = cépage de plaisir
# immédiat. Clés normalisées (minuscules, sans accent). Un cépage inconnu est
# neutre (facteur 1.0) ; pour un assemblage on retient le facteur maximal — le
# cépage le plus apte à la garde impose le rythme.
FACTEUR_CEPAGE: dict[str, float] = {
    # --- Rouges de longue garde ---
    "nebbiolo": 1.5,
    "tannat": 1.45,
    "cabernet sauvignon": 1.4,
    "syrah": 1.35,
    "shiraz": 1.35,
    "mourvedre": 1.35,
    "petit verdot": 1.35,
    "sangiovese": 1.3,
    "tempranillo": 1.3,
    "malbec": 1.3,
    "cabernet franc": 1.15,
    # --- Rouges de garde moyenne ---
    "grenache": 1.05,
    "merlot": 1.0,
    "pinot noir": 1.0,
    "carignan": 1.0,
    # --- Rouges de plaisir immédiat ---
    "cinsault": 0.7,
    "gamay": 0.6,
    # --- Blancs de garde ---
    "savagnin": 1.7,
    "riesling": 1.6,
    "chenin blanc": 1.5,
    "chenin": 1.5,
    "semillon": 1.5,
    "furmint": 1.4,
    "chardonnay": 1.2,
    "gewurztraminer": 1.1,
    # --- Blancs à boire jeunes ---
    "pinot gris": 0.9,
    "pinot grigio": 0.7,
    "viognier": 0.8,
    "sauvignon blanc": 0.7,
    "sauvignon": 0.7,
    "muscat": 0.6,
}

# Qualité du millésime (note /5) → (facteur début, facteur fin). Un grand
# millésime (5) se ferme plus longtemps et tient plus longtemps ; un petit
# millésime (1) s'ouvre vite et ne se garde pas. 3 = année de référence (neutre).
AJUSTEMENT_MILLESIME: dict[int, tuple[float, float]] = {
    5: (1.15, 1.30),  # exceptionnel
    4: (1.05, 1.15),  # très bon
    3: (1.00, 1.00),  # moyen / référence
    2: (0.95, 0.85),  # correct
    1: (0.90, 0.70),  # faible
}
QUALITE_NEUTRE = 3

# Table indicative de qualité des millésimes par grande région (note /5,
# 3 = neutre / non renseigné). Repères de consensus, sciemment partiels : toute
# région ou année absente retombe sur la note neutre (aucun ajustement).
MILLESIMES: dict[str, dict[int, int]] = {
    "bordeaux": {
        2000: 5, 2005: 5, 2009: 5, 2010: 5, 2015: 5, 2016: 5, 2018: 5,
        2019: 5, 2020: 5, 2008: 4, 2012: 4, 2014: 4, 2022: 5,
        2004: 3, 2007: 2, 2011: 3, 2013: 1, 2017: 3, 2021: 3,
    },
    "bourgogne": {
        2005: 5, 2009: 4, 2010: 5, 2015: 5, 2017: 4, 2019: 5, 2020: 5,
        2018: 4, 2022: 4, 2004: 2, 2008: 3, 2011: 3, 2013: 3,
        2016: 3, 2021: 2,
    },
    "rhone": {
        2005: 4, 2007: 5, 2009: 4, 2010: 5, 2015: 5, 2016: 5, 2019: 5,
        2020: 4, 2017: 4, 2001: 5, 2002: 1, 2008: 2, 2011: 3,
        2013: 3, 2014: 2, 2021: 3,
    },
    "champagne": {
        2002: 5, 2004: 4, 2008: 5, 2012: 5, 2013: 4, 2015: 4, 2016: 3,
        2006: 3, 2009: 3, 2011: 2, 2018: 4,
    },
    "alsace": {
        2005: 4, 2007: 4, 2008: 4, 2010: 4, 2015: 5, 2017: 4, 2019: 4,
        2016: 3, 2018: 3, 2011: 3, 2021: 3,
    },
    "loire": {
        2005: 5, 2009: 4, 2010: 4, 2014: 4, 2015: 5, 2016: 4, 2018: 4,
        2019: 4, 2020: 4, 2011: 3, 2013: 2, 2021: 2,
    },
    "piemont": {
        2004: 4, 2006: 5, 2007: 4, 2010: 5, 2013: 5, 2015: 4, 2016: 5,
        2019: 4, 2011: 3, 2014: 2, 2002: 1, 2003: 2,
    },
    "toscane": {
        2004: 4, 2006: 5, 2007: 5, 2010: 5, 2015: 5, 2016: 5, 2019: 4,
        2011: 3, 2013: 3, 2014: 1, 2002: 1, 2020: 4,
    },
}

# Sous-régions / synonymes → clé de la table `MILLESIMES`. La région d'une cuvée
# est un texte libre (« Médoc », « Châteauneuf-du-Pape », « Barolo »…) ; on la
# rattache à sa grande région viticole par recherche de sous-chaîne normalisée.
_ALIAS_REGION: dict[str, str] = {
    "bordeaux": "bordeaux", "medoc": "bordeaux", "saint-emilion": "bordeaux",
    "st-emilion": "bordeaux", "pomerol": "bordeaux", "pauillac": "bordeaux",
    "margaux": "bordeaux", "saint-julien": "bordeaux", "saint-estephe": "bordeaux",
    "graves": "bordeaux", "pessac": "bordeaux", "sauternes": "bordeaux",
    "bourgogne": "bourgogne", "burgundy": "bourgogne", "cote de nuits": "bourgogne",
    "cote de beaune": "bourgogne", "chablis": "bourgogne", "meursault": "bourgogne",
    "chambertin": "bourgogne", "montrachet": "bourgogne", "beaujolais": "bourgogne",
    "rhone": "rhone", "chateauneuf": "rhone", "hermitage": "rhone",
    "cote rotie": "rhone", "gigondas": "rhone", "cornas": "rhone",
    "condrieu": "rhone", "crozes": "rhone",
    "champagne": "champagne",
    "alsace": "alsace",
    "loire": "loire", "vouvray": "loire", "chinon": "loire", "sancerre": "loire",
    "saumur": "loire", "muscadet": "loire", "bourgueil": "loire", "anjou": "loire",
    "piemont": "piemont", "piedmont": "piemont", "piedmonte": "piemont",
    "barolo": "piemont", "barbaresco": "piemont",
    "toscane": "toscane", "tuscany": "toscane", "chianti": "toscane",
    "brunello": "toscane", "montalcino": "toscane", "bolgheri": "toscane",
}


def _normaliser(texte: str) -> str:
    """Minuscule + suppression des accents, pour des clés robustes à la saisie."""
    decompose = unicodedata.normalize("NFKD", texte or "")
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return sans_accent.strip().lower()


def _facteur_cepage(cepages: list[str] | None) -> float:
    """Multiplicateur de garde issu des cépages (facteur maximal d'un assemblage).

    Aucun cépage connu (liste vide, ou tous inconnus) → 1.0 (neutre).
    """
    facteurs = [
        FACTEUR_CEPAGE[nom]
        for c in (cepages or [])
        if (nom := _normaliser(c)) in FACTEUR_CEPAGE
    ]
    return max(facteurs) if facteurs else 1.0


def qualite_millesime(
    region: str | None,
    millesime: int | None,
    millesimes: dict[str, dict[int, int]] | None = None,
) -> int:
    """Note de qualité (1 à 5) du millésime pour la région, 3 par défaut.

    La région (texte libre) est rattachée à sa grande région viticole via
    `_ALIAS_REGION`. Région ou année inconnue → note neutre (3).

    `millesimes` permet d'injecter la table de qualité des millésimes (la table
    sourçable `MillesimeReference`, cf. Phase 4). À défaut, on utilise la table
    intégrée `MILLESIMES` — repli hors-ligne qui garde ce module *pur* et
    testable sans base de données.
    """
    if not region or not millesime:
        return QUALITE_NEUTRE
    table = MILLESIMES if millesimes is None else millesimes
    region_norm = _normaliser(region)
    for alias, cle in _ALIAS_REGION.items():
        if alias in region_norm:
            return table.get(cle, {}).get(millesime, QUALITE_NEUTRE)
    return QUALITE_NEUTRE


def fenetre_apogee(
    couleur: str,
    millesime: int | None,
    cepages: list[str] | None = None,
    region: str | None = None,
    millesimes: dict[str, dict[int, int]] | None = None,
) -> tuple[int | None, int | None]:
    """Estime (année de début, année de fin) d'apogée.

    Part du potentiel de garde de la couleur, puis l'affine — quand
    l'information est fournie — par le cépage (aptitude à la garde) et la qualité
    du millésime (repères régionaux). Sans cépage ni région, on retombe sur la
    seule base couleur (rétro-compatible).

    `millesimes` (facultatif) injecte la table de qualité des millésimes ; sinon
    la table intégrée `MILLESIMES` s'applique (repli hors-ligne).

    Renvoie ``(None, None)`` pour un vin non millésimé : sans année de récolte,
    la fenêtre ne peut pas être datée.
    """
    if not millesime:
        return (None, None)
    debut_offset, fin_offset = POTENTIEL_GARDE.get(couleur, _DEFAUT)
    facteur = _facteur_cepage(cepages)
    q_debut, q_fin = AJUSTEMENT_MILLESIME[qualite_millesime(region, millesime, millesimes)]
    debut = millesime + round(debut_offset * facteur * q_debut)
    fin = millesime + round(fin_offset * facteur * q_fin)
    # Un ajustement agressif (petit millésime d'un cépage de plaisir) ne doit pas
    # inverser les bornes : la fin ne peut pas précéder le début.
    return (debut, max(debut, fin))


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
