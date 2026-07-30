"""Import d'un catalogue de caviste (CSV) — canal de scraping, confiance basse.

Alimente le référentiel depuis le jeu de données `elvinrustam/wine-dataset`
(Kaggle, ~1 290 vins, déposé en CC0). Il apporte une donnée qu'aucune autre
source locale ne porte à ce niveau de détail : l'**appellation** (« Napa Valley »,
« Puligny-Montrachet »), ainsi que des descriptions rédigées.

**Ce que ce canal est, et pourquoi il arbitre en dernier.** Contrairement à
X-Wines (jeu académique, ODbL) ou LWIN (référentiel Liv-ex), ce fichier est le
catalogue d'un détaillant britannique : prix en livres, colonnes « per bottle /
per case / each », marques de distributeur, et jusqu'à quelques spiritueux égarés
dans la liste. Le CC0 a été apposé par le déposant, non par la source. Il relève
donc du **cadre scraping** de la revue d'architecture (§6, Phase 5) : provenance
obligatoire, et confiance basse par défaut. Le canal se nomme `scrape:marchand`,
ce qui lui vaut automatiquement `0.40` via ``ingest._confiance_pour`` — il ne peut
donc jamais écraser une valeur venue de LWIN, Claude, wineapi ou X-Wines. Il
comble des trous ; il ne dégrade rien. Cf. `docs/datasets-kaggle.md`.

Trois garde-fous découlent de cette origine :

1. **Les produits non vinicoles sont écartés** (whisky, tequila…), de même que les
   `Type` qui ne décrivent pas un vin (`Brown`, `Mixed`).
2. **Aucune donnée de marché n'est reprise.** Le prix est le tarif de détail du
   marchand, la partie la plus manifestement propriétaire du fichier — et les
   champs de marché s'arbitrent à la **récence**, pas à la confiance : un tarif
   scrapé primerait donc sur un relevé wineapi légitime. On ne l'importe pas.
3. **Une ligne dont on ne sait pas isoler le producteur est ignorée.** Le fichier
   n'a pas de colonne producteur : il faut le tirer du titre, et sans producteur
   il n'existe aucune clé de déduplication (`(domaine, nom_normalise)`). Créer
   quand même reviendrait à semer des doublons dans un catalogue mutualisé.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from django.db import IntegrityError, transaction

from .enrichment.base import NormalizedWine
from .enrichment.normalize import clean, couleur_from_type, guess_couleur
from .ingest import upsert_cuvee
from .models import Cuvee
from .tabular import ImportFichierError, lignes, texte

CANAL = "scrape:marchand"
PREFIXE_REFERENCE = "marchand:"
_LOT = 500

# Produits qui ne sont pas du vin : le fichier est un catalogue de boissons.
_SPIRITUEUX = re.compile(
    r"whisky|whiskey|\bgin\b|\brum\b|vodka|tequila|cognac|brandy|liqueur|single malt|"
    r"\bcider\b|\bbeer\b|\bale\b",
    re.I,
)
# `Type` ne décrivant pas un vin (« Brown » = whisky, « Mixed » = carton panaché).
_TYPES_EXCLUS = {"brown", "mixed"}

# Millésime tel que le catalogue l'écrit : « 2022 », « 2020/21 », « NV ».
_MILLESIME = re.compile(r"\b(?:19|20)\d{2}(?:/\d{2,4})?\b|\bN\.?V\.?\b", re.I)
# Segment entre guillemets = nom de cuvée. L'apostrophe est ici ambiguë : elle
# délimite la cuvée (« 'Cristal' ») *et* marque l'élision française (« Caves
# d'Esclans », « Pays d'Oc »). On la désambiguïse par la position plutôt que par
# la gourmandise : un guillemet de cuvée **ouvre** après un blanc ou en début de
# chaîne et **ferme** avant un blanc, une virgule ou la fin, là où l'apostrophe
# d'élision est collée entre deux lettres. Sans cette règle, « Caves d'Esclans
# 'Whispering Angel' Rosé » donnait la cuvée « Esclans 'Whispering Angel ».
_ENTRE_GUILLEMETS = re.compile(r"(?:^|(?<=\s))['‘’\"“”](.+?)['‘’\"“”](?=\s|,|$)")
# « ABV 14.00% » -> 14.0
_ABV = re.compile(r"(\d+(?:[.,]\d+)?)\s*%?")


class MarchandImportError(ImportFichierError):
    """Fichier illisible ou de schéma inattendu."""


@dataclass
class ResultatImport:
    lus: int = 0
    crees: int = 0
    maj: int = 0
    deja: int = 0
    non_vin: int = 0  # spiritueux et cartons panachés
    sans_producteur: int = 0  # titre non scindable : ignoré, faute de clé
    ignores: int = 0

    def __str__(self) -> str:  # pragma: no cover - confort d'affichage
        return (
            f"{self.lus} lignes lues — {self.crees} cuvées créées, {self.maj} mises à jour, "
            f"{self.deja} déjà présentes, {self.non_vin} non vinicoles, "
            f"{self.sans_producteur} sans producteur identifiable, {self.ignores} ignorées"
        )


def est_du_vin(titre: str, type_: str) -> bool:
    """Le catalogue mélange vins et spiritueux : ne garder que le vin."""
    if _SPIRITUEUX.search(titre or ""):
        return False
    return (type_ or "").strip().lower() not in _TYPES_EXCLUS


def couleur_depuis_type(type_marchand: str, titre: str) -> str:
    """Couleur interne depuis `Type`, complétée par le titre pour l'effervescence.

    Le catalogue n'a pas de type « Sparkling » : un champagne est typé `White`.
    On laisse donc les mots du titre trancher en premier (`guess_couleur` fait
    primer les bulles), et on retombe sur le type sinon. `Tawny` (porto) et
    `Orange` sont des vins, mais hors nomenclature : ils tombent sur `AUTRE`."""
    par_titre = guess_couleur(titre or "")
    if par_titre == "BULLES":
        return par_titre
    return couleur_from_type(type_marchand)


def degre_depuis_abv(abv: str) -> float | None:
    """« ABV 14.00% » -> 14.0 ; None si absent ou illisible."""
    trouve = _ABV.search(texte(abv))
    if not trouve:
        return None
    try:
        valeur = float(trouve.group(1).replace(",", "."))
    except ValueError:
        return None
    # Un degré hors du plausible trahit une cellule mal formée.
    return valeur if 0 < valeur < 100 else None


def _sans_suffixe_geographique(titre: str, ligne: dict) -> str:
    """Retire le « , <région> » final, qui redit une colonne déjà exploitée.

    « Oyster Bay Sauvignon Blanc 2022, Marlborough » -> « Oyster Bay Sauvignon
    Blanc 2022 ». On ne coupe que si le segment final correspond exactement à
    l'appellation, la région ou le pays de la ligne : un nom de cuvée à virgule
    n'est pas amputé au hasard."""
    for colonne in ("APPELLATION", "REGION", "COUNTRY"):
        valeur = clean(texte(ligne.get(colonne)))
        if valeur and titre.lower().endswith(f", {valeur.lower()}"):
            return titre[: -(len(valeur) + 2)].strip()
    return titre


def decomposer_titre(titre: str, ligne: dict) -> tuple[str, str] | None:
    """`Title` -> (producteur, nom de cuvée), ou ``None`` si non scindable.

    Fonction *pure*. Le fichier n'ayant pas de colonne producteur, deux motifs
    fiables sont exploités, dans cet ordre :

    1. **Un segment entre guillemets est le nom de cuvée**, et ce qui le précède
       est le producteur — « Louis Roederer 'Cristal' Champagne 2015 ».
    2. **Le cépage de la colonne `Grape` apparaît dans le titre** : ce qui le
       précède est le producteur — « Oyster Bay Sauvignon Blanc 2022 ».

    Hors de ces deux cas (« Louis Latour Mâcon-Lugny », « The Guv'nor »), rien ne
    distingue le producteur du nom du vin : on renvoie ``None`` et l'appelant
    ignore la ligne, plutôt que d'inventer une découpe qui créerait des doublons.
    """
    propre = _sans_suffixe_geographique(clean(titre), ligne)
    propre = clean(_MILLESIME.sub("", propre)).strip(" ,")
    if not propre:
        return None

    entre_guillemets = _ENTRE_GUILLEMETS.search(propre)
    if entre_guillemets:
        producteur = clean(propre[: entre_guillemets.start()])
        cuvee = clean(entre_guillemets.group(1))
        if producteur and cuvee:
            return producteur, cuvee

    cepage = clean(texte(ligne.get("GRAPE")))
    if cepage:
        position = propre.lower().find(cepage.lower())
        if position > 0:
            producteur = clean(propre[:position]).strip(" ,")
            # Dans ce catalogue, une virgule restante n'introduit jamais qu'un
            # complément géographique que `_sans_suffixe_geographique` n'a pas su
            # reconnaître (la colonne ne coïncidait pas au caractère près) :
            # « Zinfandel, Lodi » désigne le zinfandel, pas une cuvée « , Lodi ».
            cuvee = clean(propre[position:].split(",")[0]).strip(" ,")
            if producteur and cuvee:
                return producteur, cuvee
    return None


def _cepages(ligne: dict) -> list[str]:
    """Cépage principal + cépages secondaires (séparés par des virgules)."""
    noms: list[str] = []
    for colonne in ("GRAPE", "SECONDARY GRAPE VARIETIES"):
        for morceau in texte(ligne.get(colonne)).split(","):
            nom = clean(morceau)
            if nom and nom not in noms:
                noms.append(nom)
    return noms


def detail_depuis_ligne(ligne: dict) -> dict | None:
    """Ligne du catalogue -> détail **au format wineapi**. Fonction pure.

    Aucune donnée de marché n'y figure (cf. l'en-tête du module) : ni prix, ni
    note, ni offre marchande."""
    titre = clean(texte(ligne.get("TITLE")))
    if not titre or not est_du_vin(titre, texte(ligne.get("TYPE"))):
        return None
    decoupe = decomposer_titre(titre, ligne)
    if decoupe is None:
        return None
    producteur, nom = decoupe

    detail: dict = {
        "name": nom,
        "winery": {"name": producteur},
        "description": clean(texte(ligne.get("DESCRIPTION"))),
        "grapes": [{"name": c} for c in _cepages(ligne)],
        "region": {
            "name": clean(texte(ligne.get("REGION"))),
            "country": clean(texte(ligne.get("COUNTRY"))),
        },
        "titre_origine": titre,
    }
    degre = degre_depuis_abv(texte(ligne.get("ABV")))
    if degre is not None:
        detail["alcoholContent"] = degre
    return detail


def wine_depuis_ligne(ligne: dict) -> NormalizedWine | None:
    """Ligne -> ``NormalizedWine``. Fonction pure."""
    detail = detail_depuis_ligne(ligne)
    if detail is None:
        return None
    titre = detail["titre_origine"]
    # Le fichier n'a pas d'identifiant : le titre en tient lieu. Son empreinte
    # rend l'import idempotent et reprenable, comme la référence X-Wines.
    empreinte = hashlib.sha1(titre.encode("utf-8")).hexdigest()[:16]
    return NormalizedWine(
        domaine_nom=detail["winery"]["name"][:255],
        cuvee_nom=detail["name"][:255],
        couleur=couleur_depuis_type(texte(ligne.get("TYPE")), titre),
        # L'apport principal de ce canal. `appellation` étant désormais un champ
        # consolidé, il peut aussi combler une cuvée déjà présente au catalogue.
        appellation=clean(texte(ligne.get("APPELLATION")))[:255],
        cepages=[g["name"] for g in detail["grapes"]],
        source=CANAL,
        reference_externe_id=f"{PREFIXE_REFERENCE}{empreinte}",
        raw={"wineapi_detail": detail},
    )


def importer_catalogue_marchand(
    chemin: str,
    *,
    delimiter: str = ",",
    limite: int | None = None,
    rafraichir: bool = False,
    lot: int = _LOT,
    progression=None,
) -> ResultatImport:
    """Ingest le catalogue marchand situé à ``chemin``.

    Idempotent : une ligne déjà importée (même empreinte de titre) est sautée,
    sauf ``rafraichir=True``. Lève ``MarchandImportError`` si le schéma n'est pas
    celui attendu."""
    resultat = ResultatImport()
    tampon: list[NormalizedWine] = []
    schema_verifie = False

    def vider():
        with transaction.atomic():
            for wine in tampon:
                try:
                    _, cree = upsert_cuvee(wine)
                except IntegrityError:
                    resultat.ignores += 1
                    continue
                resultat.crees += 1 if cree else 0
                resultat.maj += 0 if cree else 1
        tampon.clear()

    for ligne in lignes(chemin, delimiter):
        if not schema_verifie:
            if "TITLE" not in ligne or "APPELLATION" not in ligne:
                raise MarchandImportError(
                    "Schéma inattendu : les colonnes Title / Appellation sont absentes. "
                    "Attendu le fichier « WineDataset.csv » du jeu elvinrustam/wine-dataset."
                )
            schema_verifie = True

        resultat.lus += 1
        titre = clean(texte(ligne.get("TITLE")))
        if titre and not est_du_vin(titre, texte(ligne.get("TYPE"))):
            resultat.non_vin += 1
        else:
            wine = wine_depuis_ligne(ligne)
            if wine is None:
                # Titre vide, ou producteur non isolable (le cas dominant).
                resultat.sans_producteur += 1
            elif (
                not rafraichir
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
