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
import unicodedata

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


def normaliser_nom(texte: str) -> str:
    """Forme canonique d'un nom, pour la **déduplication** du catalogue.

    Minuscules, sans accents ni ligatures, toute ponctuation et tout blanc
    ramenés à une espace simple. « Château Margaux », « CHATEAU MARGAUX » et
    « Chateau-Margaux » se rejoignent donc sur « chateau margaux ».

    C'est ce dont dépend l'unicité d'une cuvée sans identité forte : un LLM ne
    rend pas deux fois exactement la même chaîne, si bien qu'une comparaison
    brute laissait deux scans du même vin créer deux cuvées dans le catalogue
    mutualisé (cf. `Cuvee.nom_normalise` et sa contrainte d'unicité).
    """
    if not texte:
        return ""
    # Les ligatures n'ont pas de décomposition NFKD : sans ce remplacement,
    # « Cœur » deviendrait « cur » et ne rejoindrait plus « coeur ».
    for source, cible in (("œ", "oe"), ("Œ", "OE"), ("æ", "ae"), ("Æ", "AE")):
        texte = texte.replace(source, cible)
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return " ".join(m for m in re.split(r"[^a-z0-9]+", sans_accents.lower()) if m)


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


def parse_vintage(*texts) -> int | None:
    """Extrait un millésime (année 19xx/20xx) d'un ou plusieurs champs texte, sinon None."""
    for t in texts:
        if not t:
            continue
        match = re.search(r"\b(?:19|20)\d{2}\b", str(t))
        if match:
            return int(match.group(0))
    return None


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
