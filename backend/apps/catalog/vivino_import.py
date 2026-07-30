"""Import d'exports scrapés « façon Vivino » — canal de scraping, confiance basse.

Couvre les exports CSV de notes et de prix qui circulent sur Kaggle (Vivino,
wine.com…). Ces fichiers ont tous la même nature — un producteur, un nom de vin,
une région, une note communautaire, un prix — sous des **en-têtes différents** :
une table d'alias suffit donc là où quatre lecteurs seraient redondants.

Sources validées avec ce module (cf. `docs/datasets-kaggle.md` §6) :

| Fichier | Lignes | Particularité |
|---|---|---|
| `vivinoAllWineExportFrance.csv` | 30 018 | `Name_domain` / `Product_name` |
| `vivino_top_ten.csv` | 12 205 | `Winery` / `Wine`, porte un `Wine_ID` |
| `vivino_wines_2026.csv` | 10 344 | `winery_name` / `wine_name`, porte un `wine_id` |
| `vivno_dataset.csv` (wine.com) | 15 255 | **UTF-16**, sans colonne producteur |

**Ce canal est du scraping, et tout ici en découle.** Ces données proviennent de
Vivino ou d'un marchand, dont les conditions d'utilisation interdisent
l'extraction ; le dépôt refuse d'ailleurs d'implémenter Vivino comme fournisseur
(`enrichment/stubs.py`). Elles entrent donc sous le **cadre scraping** de la revue
d'architecture (Phase 5) : provenance obligatoire, confiance basse, arbitrage en
dernier. Le canal est préfixé `scrape:`, ce qui lui vaut `0.40` via
``ingest._confiance_pour`` — et, depuis ``consolidation._clef_marche``, le place
sous *toutes* les sources légitimes pour les prix et les notes, y compris plus
récentes que lui. Il comble des trous ; il ne dégrade rien.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from django.db import IntegrityError, transaction

from .enrichment.base import NormalizedWine
from .enrichment.normalize import clean, couleur_from_type, guess_couleur, strip_vintage
from .ingest import upsert_cuvee
from .models import Cuvee
from .tabular import ImportFichierError, lignes, texte

CANAL_DEFAUT = "scrape:vivino"
PREFIXE_REFERENCE = "vivino:"
_LOT = 500

# En-têtes acceptés pour chaque rôle, par ordre de préférence. C'est tout ce qui
# sépare les quatre schémas : ajouter une source revient à compléter cette table.
_ALIAS: dict[str, tuple[str, ...]] = {
    "producteur": ("NAME_DOMAIN", "WINERY_NAME", "WINERY"),
    "nom": ("PRODUCT_NAME", "WINE_NAME", "WINE", "FULL_NAME", "NAMES"),
    "region": ("REGION",),
    "pays": ("COUNTRY", "COUNTRYS"),
    "type": ("TYPE", "WINE_TYPE", "COLOR_WINE"),
    "note": ("AVERAGE_RATING", "RATING", "RATINGS"),
    "nb_notes": ("NUMBER_OF_RATES", "RATINGS_COUNT", "REVIEWS", "RATINGSNUM"),
    "prix": ("PRICE", "PRICES"),
    "cepage": ("PRIMARY_GRAPE", "GRAPE"),
    "degre": ("ABV %", "ABV"),
    "identifiant": ("WINE_ID", "VINTAGE_ID"),
    # wine.com range « Chardonnay from Willamette Valley, Oregon » sous un
    # en-tête « Countrys » trompeur : c'est un descripteur cépage + région.
    "descripteur": ("COUNTRYS",),
}

# « N.V. », « NV » : mention de non-millésimé, à retirer d'un nom de cuvée au même
# titre qu'un millésime (une `Cuvee` est indépendante du millésime).
_NON_MILLESIME = re.compile(r"\bN\.?\s?V\.?\b", re.I)
_NOMBRE = re.compile(r"-?\d+(?:[.,]\d+)?")
# « Chardonnay from Willamette Valley, Oregon » -> (cépage, région)
_DESCRIPTEUR = re.compile(r"^(.+?)\s+from\s+(.+)$", re.I)


class VivinoImportError(ImportFichierError):
    """Fichier illisible ou dépourvu des colonnes minimales."""


@dataclass
class ResultatImport:
    lus: int = 0
    crees: int = 0
    maj: int = 0
    deja: int = 0
    sans_identite: int = 0  # producteur ou nom manquant : aucune clé de dédup
    ignores: int = 0

    def __str__(self) -> str:  # pragma: no cover - confort d'affichage
        return (
            f"{self.lus} lignes lues — {self.crees} cuvées créées, {self.maj} mises à jour, "
            f"{self.deja} déjà présentes, {self.sans_identite} sans identité exploitable, "
            f"{self.ignores} ignorées"
        )


def valeur(ligne: dict, role: str) -> str:
    """Valeur du premier en-tête connu pour ``role`` (chaîne vide si absent)."""
    for entete in _ALIAS.get(role, ()):
        brut = texte(ligne.get(entete))
        if brut:
            return clean(brut)
    return ""


def _nombre(brut: str) -> float | None:
    """Premier nombre d'une cellule (« 79.99$ », « 4,2 ») ; None si illisible."""
    trouve = _NOMBRE.search((brut or "").replace(",", "."))
    if not trouve:
        return None
    try:
        return float(trouve.group(0))
    except ValueError:
        return None


def nom_de_cuvee(brut: str) -> str:
    """Nom de cuvée débarrassé du millésime et de la mention « N.V. ».

    Ces exports collent le millésime au nom (« Rosado de Lágrima 2020 ») : sans
    ce nettoyage, chaque millésime créerait une cuvée distincte alors que
    `Cuvee` en est indépendante."""
    # Le point final est retiré aussi : sur « Sweet White N.V. » la limite de mot
    # laisse la dernière ponctuation en dehors de la capture.
    return clean(strip_vintage(_NON_MILLESIME.sub("", brut or ""))).strip(" ,-.")


def _descripteur(ligne: dict) -> tuple[str, str]:
    """« Chardonnay from Willamette Valley, Oregon » -> (cépage, région).

    Renvoie ``("", "")`` si la colonne est absente ou d'une autre forme (auquel
    cas elle contient vraiment un pays, comme son en-tête le prétend)."""
    trouve = _DESCRIPTEUR.match(valeur(ligne, "descripteur"))
    if not trouve:
        return "", ""
    return clean(trouve.group(1)), clean(trouve.group(2))


def identite(ligne: dict) -> tuple[str, str] | None:
    """(producteur, nom de cuvée) pour une ligne, ou ``None`` si inexploitable.

    Quand la source n'a pas de colonne producteur (wine.com), on le tire du nom
    complet : le cépage y sépare le producteur du reste du libellé
    (« 00 Wines *Chardonnay* 2017 »). Sans producteur il n'existe aucune clé de
    déduplication `(domaine, nom_normalise)` : la ligne est alors écartée plutôt
    que de semer des doublons dans le catalogue mutualisé."""
    producteur = valeur(ligne, "producteur")
    nom = nom_de_cuvee(valeur(ligne, "nom"))
    if producteur and nom:
        return producteur, nom
    if not nom:
        return None

    cepage = valeur(ligne, "cepage") or _descripteur(ligne)[0]
    if cepage:
        position = nom.lower().find(cepage.lower())
        if position > 0:
            producteur = clean(nom[:position]).strip(" ,-")
            cuvee = clean(nom[position:]).strip(" ,-")
            if producteur and cuvee:
                return producteur, cuvee
    return None


def couleur_de_ligne(ligne: dict) -> str:
    """Couleur interne. `Type` vaut « Red », « White Wine », « Rose »… et les
    effervescents ne s'y distinguent pas : le nom du vin tranche en premier."""
    par_nom = guess_couleur(valeur(ligne, "nom"))
    if par_nom == "BULLES":
        return par_nom
    brut = valeur(ligne, "type")
    # « White Wine » -> « white » ; « Sparkling » est reconnu tel quel.
    return couleur_from_type(re.sub(r"\s*wine\s*$", "", brut, flags=re.I))


def detail_depuis_ligne(ligne: dict, devise: str = "EUR") -> dict | None:
    """Ligne -> détail **au format wineapi**. Fonction pure.

    Les notes et le prix sont repris — ils sont l'essentiel de ce que ces
    exports apportent — mais restent inoffensifs : la consolidation range le
    scraping sous toute source légitime pour les champs de marché."""
    couple = identite(ligne)
    if couple is None:
        return None
    producteur, nom = couple
    cepage_descripteur, region_descripteur = _descripteur(ligne)

    detail: dict = {
        "name": nom,
        "winery": {"name": producteur},
        "region": {
            "name": valeur(ligne, "region") or region_descripteur,
            # Sur wine.com, « Countrys » n'est pas un pays : on ne l'y lit pas.
            "country": "" if region_descripteur else valeur(ligne, "pays"),
        },
        "grapes": [{"name": c} for c in ([valeur(ligne, "cepage") or cepage_descripteur] if
                                         (valeur(ligne, "cepage") or cepage_descripteur) else [])],
    }
    note = _nombre(valeur(ligne, "note"))
    if note:  # une note à 0 signifie « pas encore notée », pas « nulle ».
        detail["averageRating"] = note
        detail["ratingsCount"] = int(_nombre(valeur(ligne, "nb_notes")) or 0)
    prix = _nombre(valeur(ligne, "prix"))
    if prix:
        detail["priceRange"] = {"min": prix, "max": prix, "currency": devise}
    degre = _nombre(valeur(ligne, "degre"))
    if degre and 0 < degre < 100:
        detail["alcoholContent"] = degre
    return detail


def wine_depuis_ligne(ligne: dict, *, canal: str, devise: str = "EUR") -> NormalizedWine | None:
    """Ligne -> ``NormalizedWine``. Fonction pure."""
    detail = detail_depuis_ligne(ligne, devise)
    if detail is None:
        return None
    producteur = detail["winery"]["name"]
    nom = detail["name"]
    # L'identifiant de la source quand elle en a un, sinon l'empreinte du couple
    # (producteur, nom) : dans les deux cas l'import reste idempotent.
    brut = valeur(ligne, "identifiant")
    cle = brut or hashlib.sha1(f"{producteur}|{nom}".encode("utf-8")).hexdigest()[:16]
    return NormalizedWine(
        domaine_nom=producteur[:255],
        cuvee_nom=nom[:255],
        couleur=couleur_de_ligne(ligne),
        cepages=[g["name"] for g in detail["grapes"]],
        source=canal,
        reference_externe_id=f"{PREFIXE_REFERENCE}{cle}",
        raw={"wineapi_detail": detail},
    )


def importer_vivino(
    chemin: str,
    *,
    canal: str = CANAL_DEFAUT,
    devise: str = "EUR",
    delimiter: str = ",",
    limite: int | None = None,
    rafraichir: bool = False,
    lot: int = _LOT,
    progression=None,
) -> ResultatImport:
    """Ingest un export scrapé. ``canal`` doit être préfixé ``scrape:``."""
    if not canal.startswith("scrape"):
        # Garde-fou : c'est le préfixe qui fixe la confiance basse (0,40) et
        # relègue la source sous les canaux légitimes. Sans lui, ces données
        # arbitreraient à égalité avec wineapi ou Claude.
        raise VivinoImportError(
            f"Canal « {canal} » refusé : ces données relèvent du scraping et doivent "
            "porter un canal préfixé « scrape: » pour rester en confiance basse."
        )

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
            if not valeur(ligne, "nom") and not any(a in ligne for a in _ALIAS["nom"]):
                raise VivinoImportError(
                    "Schéma inattendu : aucune colonne de nom de vin reconnue "
                    f"(attendu l'un de {', '.join(_ALIAS['nom'])})."
                )
            schema_verifie = True

        resultat.lus += 1
        wine = wine_depuis_ligne(ligne, canal=canal, devise=devise)
        if wine is None:
            resultat.sans_identite += 1
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
