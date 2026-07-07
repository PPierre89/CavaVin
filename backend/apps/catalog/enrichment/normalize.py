"""Petites fonctions de nettoyage/normalisation partagées par les fournisseurs."""

COULEUR_KEYWORDS = {
    "BULLES": [
        "champagne", "crémant", "cremant", "mousseux", "sparkling",
        "prosecco", "cava", "pétillant", "petillant", "bulles", "effervescent",
    ],
    # "rose" sans accent couvre les catégories OFF non accentuées ("vins roses").
    "ROSE": ["rosé", "rose", "rosado", "rosato"],
    "ROUGE": ["rouge", "red", "tinto", "rosso"],
    "BLANC": ["blanc", "white", "blanco", "bianco", "weiss", "weißwein"],
}


import re

# Type wineapi.io -> couleur interne (Cuvee.Couleur).
TYPE_COULEUR = {
    "red": "ROUGE",
    "white": "BLANC",
    "rosé": "ROSE",
    "rose": "ROSE",
    "sparkling": "BULLES",
    "dessert": "AUTRE",
    "fortified": "AUTRE",
}

_VINTAGE_RE = re.compile(r"\s*\b(?:19|20)\d{2}\b")


def clean(text) -> str:
    """
    Réduit les espaces multiples et retire les blancs de bordure. Défensif face
    aux réponses d'API : une valeur inattendue (dict/list) est ignorée, un nombre
    est converti en chaîne.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        if isinstance(text, (int, float)):
            text = str(text)
        else:
            return ""
    return " ".join(text.split()).strip()


def named(value) -> str:
    """Extrait un nom propre d'un champ qui peut être une chaîne ou un objet {name}."""
    if isinstance(value, dict):
        return clean(value.get("name", ""))
    return clean(value)


def strip_vintage(name: str) -> str:
    """Retire un millésime (4 chiffres) du nom pour isoler domaine/cuvée."""
    return clean(_VINTAGE_RE.sub("", name or ""))


def couleur_from_type(wine_type: str) -> str:
    """Mappe le 'type' wineapi vers la couleur interne (défaut AUTRE)."""
    return TYPE_COULEUR.get((wine_type or "").strip().lower(), "AUTRE")


def guess_couleur(*texts) -> str:
    """
    Devine la couleur à partir de champs texte libres (nom, catégories, labels).
    Les bulles priment (un 'champagne rosé' reste des bulles), puis rosé, rouge, blanc.
    """
    blob = " ".join(t for t in texts if t).lower()
    for couleur in ("BULLES", "ROSE", "ROUGE", "BLANC"):
        if any(kw in blob for kw in COULEUR_KEYWORDS[couleur]):
            return couleur
    return "AUTRE"
