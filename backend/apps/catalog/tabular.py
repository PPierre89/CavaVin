"""Lecture d'un dump tabulaire (CSV ou XLSX) en dictionnaires de lignes.

Socle partagé par les imports de référentiel (``lwin_import``, ``xwines_import``) :
les dumps publics arrivent tantôt en XLSX (Liv-ex), tantôt en CSV (Kaggle), et
les deux imports ont exactement le même besoin — itérer des lignes
``{EN-TÊTE: valeur}`` sans charger le fichier entier en mémoire.

Aucune dépendance au modèle Django : fonctions pures, testables directement.
"""

from __future__ import annotations

import csv


class ImportFichierError(Exception):
    """Fichier de dump illisible / introuvable (message destiné à l'utilisateur)."""


def texte(valeur) -> str:
    """Valeur de cellule -> chaîne nettoyée.

    Les dumps encodent l'absence par la chaîne « NA » (ramenée à vide), et la
    lecture XLSX renvoie les identifiants numériques en flottants
    (1000001.0 -> "1000001")."""
    if valeur is None:
        return ""
    if isinstance(valeur, float) and valeur.is_integer():
        valeur = int(valeur)
    brut = str(valeur).strip()
    return "" if brut.upper() == "NA" else brut


def lignes(chemin: str, delimiter: str = ","):
    """Itère les lignes du dump en dictionnaires ``{EN-TÊTE MAJUSCULE: valeur brute}``.

    Accepte indifféremment un classeur XLSX/XLSM et un export CSV. Les en-têtes
    sont normalisés en majuscules pour que les appelants n'aient pas à se soucier
    de la casse du dump."""
    if chemin.lower().endswith((".xlsx", ".xlsm")):
        yield from _lignes_xlsx(chemin)
    else:
        yield from _lignes_csv(chemin, delimiter)


def _lignes_xlsx(chemin: str):
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - dépendance épinglée
        raise ImportFichierError("openpyxl est requis pour lire un dump XLSX.") from exc
    try:
        classeur = openpyxl.load_workbook(chemin, read_only=True)
    except (OSError, ValueError) as exc:
        raise ImportFichierError(f"Impossible d'ouvrir {chemin} : {exc}") from exc
    feuille = classeur[classeur.sheetnames[0]]
    iterateur = feuille.iter_rows(values_only=True)
    entetes = [texte(c).upper() for c in next(iterateur, [])]
    for valeurs in iterateur:
        yield dict(zip(entetes, valeurs))
    classeur.close()


def _lignes_csv(chemin: str, delimiter: str):
    try:
        fichier = open(chemin, newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise ImportFichierError(f"Impossible d'ouvrir {chemin} : {exc}") from exc
    with fichier:
        for brut in csv.DictReader(fichier, delimiter=delimiter):
            yield {(k or "").strip().upper(): v for k, v in brut.items()}
