"""Ingestion du jeu de données X-Wines dans le référentiel partagé.

X-Wines (de Azambuja & al., *Big Data Cogn. Comput.* 2023) est un jeu de données
ouvert de ~100 000 vins de 62 pays, publié sur Kaggle sous licence Open Database
(ODbL) — la même famille de licence qu'Open Food Facts, déjà exploitée ici. Il
décrit chaque vin par son producteur, sa région, son pays, son type, ses cépages,
ses accords mets-vins, son degré, son corps et son acidité : exactement le
contenu d'une fiche `Cuvee`. C'est le seul canal *hors ligne* capable de remplir
massivement le référentiel sans clé d'API ni quota (cf. `docs/datasets-kaggle.md`
pour la veille et les jeux écartés).

Le mapping produit un détail **au format wineapi.io**, comme le fait déjà le
canal Claude : la persistance (`wine_profile.normalize_detail` + `ingest`) est
ainsi réutilisée telle quelle, et le relevé traverse le même chemin que
n'importe quel canal — observation horodatée puis consolidation arbitrée.

Fonctions pures (mapping) et ingestion sont séparées : ``detail_depuis_ligne``
ne touche ni au réseau ni à la base et se teste à partir d'un simple dict.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from django.db import IntegrityError, transaction

from .enrichment.base import NormalizedWine
from .enrichment.normalize import clean, couleur_from_type
from .ingest import upsert_cuvee
from .models import Cuvee, Domaine
from .tabular import ImportFichierError, lignes, texte

# Nom du canal, tel qu'il apparaît dans `SourceObservation.canal` et dans la
# carte de provenance des cuvées.
CANAL = "xwines"

# Préfixe des références externes posées par ce canal. `Cuvee.reference_externe_id`
# est mono-source (cf. docs/architecture-referentiel.md, §4.1 : le mapping
# multi-canal reste à faire) : sans préfixe, l'identifiant X-Wines « 100062 »
# désignerait le même vin que le wineapi.io « 100062 » et les deux canaux se
# réconcilieraient à tort. Le préfixe encode le couple (canal, id) attendu par la
# cible dans la colonne existante, sans migration.
PREFIXE_REFERENCE = "xwines:"

# Nombre de lignes par transaction. `upsert_cuvee` est déjà atomique ; sans lot
# englobant, SQLite paie un commit (donc un fsync) par vin, ce qui domine
# largement le temps d'import sur 100 000 lignes.
_LOT = 500


class XWinesImportError(ImportFichierError):
    """Dump X-Wines illisible ou de schéma inattendu."""


@dataclass
class ResultatImport:
    """Compte rendu d'un import (toutes les lignes du fichier sont comptées)."""

    lus: int = 0
    crees: int = 0
    maj: int = 0
    deja: int = 0  # déjà en base, ignorées faute de --rafraichir.
    ignores: int = 0  # lignes inexploitables ou en conflit.

    def __str__(self) -> str:  # pragma: no cover - confort d'affichage
        return (
            f"{self.lus} lignes lues — {self.crees} cuvées créées, {self.maj} mises à jour, "
            f"{self.deja} déjà présentes, {self.ignores} ignorées"
        )


def _liste(valeur) -> list[str]:
    """Parse une cellule X-Wines contenant une liste littérale Python.

    Le dump sérialise les colonnes multivaluées (`Grapes`, `Harmonize`,
    `Vintages`) sous la forme ``"['Merlot', 'Cabernet Sauvignon']``. On lit donc
    un littéral Python, en tolérant une cellule vide, une valeur simple ou un
    littéral malformé (le dump est communautaire) : dans le doute, on renvoie une
    liste vide plutôt que de faire échouer la ligne entière.
    """
    brut = texte(valeur)
    if not brut:
        return []
    if brut.startswith("["):
        try:
            valeurs = ast.literal_eval(brut)
        except (ValueError, SyntaxError):
            return []
        if not isinstance(valeurs, (list, tuple)):
            return []
        return [clean(str(v)) for v in valeurs if clean(str(v))]
    return [clean(brut)]


def couleur_depuis_type(type_xwines: str) -> str:
    """Couleur interne à partir de la colonne `Type` de X-Wines.

    Les valeurs sont ``Red``, ``White``, ``Rosé``, ``Sparkling``, ``Dessert`` et
    ``Dessert/Port`` : on ne garde que le segment avant le « / » avant de passer
    la main au mapping wineapi commun. Un vin de dessert / porto n'a pas de
    couleur dans notre nomenclature — il tombe donc sur ``AUTRE``, ce qui est
    honnête (`sommellerie` retombera sur le conseil générique) plutôt que de le
    déclarer rouge à tort.
    """
    principal = (type_xwines or "").split("/")[0]
    return couleur_from_type(principal)


def _nombre(valeur) -> float | None:
    """Cellule numérique -> float, ou None si absente/illisible."""
    brut = texte(valeur)
    if not brut:
        return None
    try:
        return float(brut)
    except ValueError:
        return None


def detail_depuis_ligne(ligne: dict) -> dict | None:
    """Ligne X-Wines -> détail **au format wineapi.io**. Fonction pure.

    Renvoie ``None`` si la ligne n'identifie pas un vin (producteur ou nom
    manquant) : sans ces deux champs il n'y a pas de clé de déduplication, et la
    cuvée créée polluerait le catalogue mutualisé.
    """
    producteur = clean(texte(ligne.get("WINERYNAME")))
    nom = clean(texte(ligne.get("WINENAME")))
    if not producteur or not nom:
        return None

    detail: dict = {
        "id": texte(ligne.get("WINEID")),
        "name": nom,
        "winery": {"name": producteur},
        "type": texte(ligne.get("TYPE")),
        "elaborate": texte(ligne.get("ELABORATE")),
        "body": texte(ligne.get("BODY")),
        "acidity": texte(ligne.get("ACIDITY")),
        "grapes": [{"name": g} for g in _liste(ligne.get("GRAPES"))],
        # X-Wines ne pondère pas ses accords : `confidence` reste absent, la fiche
        # les affichera sans score (cf. wine_profile.accords_mets).
        "pairings": [{"food": h} for h in _liste(ligne.get("HARMONIZE"))],
        "region": {
            "name": texte(ligne.get("REGIONNAME")),
            "country": texte(ligne.get("COUNTRY")),
        },
        # Millésimes connus pour ce vin : sans équivalent dans notre modèle (une
        # `Cuvee` est indépendante du millésime), on les conserve dans le payload
        # brut de l'observation plutôt que de les perdre.
        "vintages": _liste(ligne.get("VINTAGES")),
        "website": texte(ligne.get("WEBSITE")),
        "countryCode": texte(ligne.get("CODE")),
    }
    abv = _nombre(ligne.get("ABV"))
    if abv is not None:
        detail["alcoholContent"] = abv
    return detail


def wine_depuis_ligne(ligne: dict) -> NormalizedWine | None:
    """Ligne X-Wines -> ``NormalizedWine`` prêt pour ``ingest``. Fonction pure."""
    detail = detail_depuis_ligne(ligne)
    if detail is None:
        return None
    identifiant = detail["id"]
    return NormalizedWine(
        domaine_nom=detail["winery"]["name"][:255],
        cuvee_nom=detail["name"][:255],
        couleur=couleur_depuis_type(detail["type"]),
        cepages=[g["name"] for g in detail["grapes"]],
        source=CANAL,
        reference_externe_id=f"{PREFIXE_REFERENCE}{identifiant}" if identifiant else "",
        raw={"wineapi_detail": detail},
    )


def _completer_domaine(domaine: Domaine, detail: dict) -> None:
    """Renseigne région / pays / site web du producteur s'ils manquent.

    Les canaux d'identification créent le producteur avec une région vide
    (`ingest._domaine_pour`) : X-Wines, lui, connaît sa région, son pays et son
    site officiel. On ne complète que les champs **vides**, pour ne jamais
    écraser une fiche renseignée à la main ou par un canal plus fiable.
    """
    region = clean(detail.get("region", {}).get("name", ""))[:255]
    pays = clean(detail.get("region", {}).get("country", ""))[:100]
    site = clean(detail.get("website", ""))[:200]
    a_ecrire = []
    for champ, valeur in (("region", region), ("pays", pays), ("site_web", site)):
        if valeur and not getattr(domaine, champ):
            setattr(domaine, champ, valeur)
            a_ecrire.append(champ)
    if not a_ecrire:
        return
    try:
        with transaction.atomic():
            domaine.save(update_fields=a_ecrire)
    except IntegrityError:
        # `Domaine` porte une contrainte unique (nom, région) : renseigner la
        # région peut faire entrer en collision avec une fiche régionale déjà
        # créée à la main. Le producteur reste rattaché à sa fiche actuelle —
        # une région manquante est bien moins grave qu'un import interrompu.
        domaine.refresh_from_db()


def importer_xwines(
    chemin: str,
    *,
    delimiter: str = ",",
    limite: int | None = None,
    rafraichir: bool = False,
    lot: int = _LOT,
    progression=None,
) -> ResultatImport:
    """Ingest le dump X-Wines situé à ``chemin`` dans le référentiel.

    Idempotent : par défaut une ligne dont la référence X-Wines est déjà au
    catalogue est **sautée** (une requête indexée, aucune écriture), ce qui rend
    un import interrompu reprenable et une seconde passe quasi gratuite. Passer
    ``rafraichir=True`` re-dépose une observation pour chaque ligne — utile à la
    sortie d'une nouvelle version du jeu de données.

    ``progression`` est un appelable ``(ResultatImport) -> None`` invoqué à
    chaque lot, pour l'affichage CLI. Lève ``XWinesImportError`` si le fichier est
    illisible ou si son schéma n'est pas celui de X-Wines.
    """
    resultat = ResultatImport()
    tampon: list[NormalizedWine] = []
    schema_verifie = False

    def vider():
        # Un lot par transaction : `upsert_cuvee` étant lui-même atomique, chaque
        # vin ne pose plus qu'un point de reprise interne, et un échec isolé ne
        # perd que sa propre ligne.
        with transaction.atomic():
            for wine in tampon:
                try:
                    cuvee, cree = upsert_cuvee(wine)
                except IntegrityError:
                    # Conflit d'identité résiduel (deux lignes du dump décrivant
                    # le même vin sous deux références, collision avec une cuvée
                    # existante…) : on saute la ligne plutôt que l'import.
                    resultat.ignores += 1
                    continue
                if cree:
                    resultat.crees += 1
                else:
                    resultat.maj += 1
                _completer_domaine(cuvee.domaine, wine.raw["wineapi_detail"])
        tampon.clear()

    for ligne in lignes(chemin, delimiter):
        if not schema_verifie:
            if "WINENAME" not in ligne or "WINERYNAME" not in ligne:
                raise XWinesImportError(
                    "Schéma inattendu : les colonnes WineName / WineryName sont absentes. "
                    "Attendu le fichier « XWines_*_wines.csv » du jeu de données X-Wines."
                )
            schema_verifie = True

        resultat.lus += 1
        wine = wine_depuis_ligne(ligne)
        if wine is None:
            resultat.ignores += 1
        elif (
            not rafraichir
            and wine.reference_externe_id
            and Cuvee.objects.filter(reference_externe_id=wine.reference_externe_id).exists()
        ):
            resultat.deja += 1
        else:
            tampon.append(wine)
            if len(tampon) >= lot:
                vider()
                if progression is not None:
                    progression(resultat)
        if limite is not None and resultat.lus >= limite:
            break

    if tampon:
        vider()
    if progression is not None:
        progression(resultat)
    return resultat
