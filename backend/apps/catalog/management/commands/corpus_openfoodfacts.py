"""Construit un corpus d'évaluation depuis Open Food Facts (données ouvertes).

Pourquoi cette source plutôt qu'un site marchand : Open Food Facts publie ses
données en **ODbL** et ses photos sous licence libre, l'extraction y est donc
explicitement permise — contrairement aux CGU des enseignes, qui l'interdisent
(le projet s'interdit déjà le scraping pour cette raison, cf. les stubs Vivino et
CellarTracker). C'est aussi la source déjà branchée dans la cascade (provider
``openfoodfacts``).

Elle est surtout **meilleure sur le fond** : chaque produit porte son
**code-barres**, donc une vérité terrain non ambiguë obtenue sans annotation
manuelle. Là où un corpus de photos annotées à la main plafonne à quelques
dizaines de cas, on en tire ici des centaines, et l'on mesure d'un coup les deux
chemins d'identification — code-barres (US 01) et étiquette (US 02/03).

Contrepartie assumée : ce sont des **photos de produit prises par des
contributeurs** (bouteille entière, éclairage variable, parfois floue), pas des
scans d'étiquette bien cadrés. C'est plus proche de ce que produit un
utilisateur de CavaVin dans sa cave — donc plus représentatif, mais plus dur.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

# Open Food Facts demande un User-Agent identifiant (application + contact) et
# un usage raisonnable de l'API. On respecte les deux : c'est la contrepartie de
# données ouvertes maintenues par des bénévoles.
_UA = "CavaVin-corpus/1.0 (https://github.com/PPierre89/CavaVin)"
_RECHERCHE = "https://world.openfoodfacts.org/api/v2/search"
_PAUSE = 1.5  # seconde entre deux pages : on ne martèle pas un service bénévole.
_ESSAIS_MAX = 4  # tentatives par page, avec attente doublée entre chacune.

# Tag de catégorie EXIGÉ sur la fiche. Le paramètre de recherche
# `categories_tags_en=wines` d'Open Food Facts fait une correspondance *textuelle*
# et ramène donc les vinaigres de vin (`en:wine-vinegars`) — voire des produits
# sans aucun rapport. On revalide donc le tag sur la fiche renvoyée : sans ce
# contrôle, le corpus mesurait le bruit plutôt que la reconnaissance.
_TAG_REQUIS = "en:wines"
# Catégories incompatibles avec un vin de cave, à écarter **même quand
# `en:wines` est présent**. Ce n'est pas de la paranoïa : Open Food Facts est
# contributif, et des fiches sont réellement mal catégorisées — une confiture de
# clémentines corses y porte `en:wines` à côté de `en:jams` et `en:marmalades`.
# Exiger le tag ne suffit donc pas ; il faut aussi refuser les familles qui le
# contredisent, sans quoi le corpus contient des cas ingagnables qui font passer
# le moteur pour mauvais.
_TAGS_ECARTES = {
    # dérivés du vin, dont l'étiquette n'identifie pas un vin
    "en:vinegars", "en:wine-vinegars", "en:condiments", "en:sauces",
    # familles franchement étrangères, vues sur des fiches mal catégorisées
    "en:jams", "en:marmalades", "en:spreads", "en:sweet-spreads",
    "en:fruit-and-vegetable-preserves", "en:breakfasts",
    "en:fishes", "en:seafood", "en:smoked-fishes", "en:meats",
    "en:cheeses", "en:dairies", "en:biscuits", "en:snacks",
    "en:fruit-juices", "en:sodas", "en:waters", "en:beers",
}

# Noms de produit qui ne désignent aucun vin en particulier (contenance, mention
# de rayon) : le cas serait ingagnable pour le moteur comme pour un humain.
_NOMS_ECARTES = {"75cl", "750ml", "1l", "vin", "wine", "rouge", "blanc", "rose", "rosé"}

# Marques trop génériques pour servir de vérité terrain : ce sont des enseignes
# ou des mentions de rayon, pas des producteurs identifiables.
_MARQUES_ECARTEES = {
    "sans marque", "no name", "marque repere", "marque repère", "bio",
    "produit blanc", "unknown", "n/a", "divers", "vin", "wine",
}


class Command(BaseCommand):
    help = (
        "Construit un manifeste d'évaluation depuis Open Food Facts "
        "(catégorie vins, données ODbL) — code-barres + photo + identité."
    )

    def add_arguments(self, parser):
        parser.add_argument("--nombre", type=int, default=200, help="Cas visés (défaut 200).")
        parser.add_argument(
            "--sortie", default=None,
            help="Manifeste JSON à écrire (défaut : evaluation_corpus/openfoodfacts.json).",
        )
        parser.add_argument(
            "--pays", default="", metavar="TAG",
            help="Filtre pays Open Food Facts, ex. « france ». Vide = tous.",
        )
        parser.add_argument(
            "--page-max", type=int, default=25,
            help="Nombre de pages API au maximum (garde-fou de politesse). Défaut 25.",
        )

    def handle(self, *args, **options):
        sortie = Path(options["sortie"]) if options["sortie"] else (
            Path(__file__).resolve().parents[2] / "evaluation_corpus" / "openfoodfacts.json"
        )
        cas = self._collecter(options)
        if not cas:
            raise CommandError(
                "Aucun produit exploitable récupéré — Open Food Facts est peut-être "
                "momentanément indisponible (503). Réessaie plus tard."
            )

        manifeste = {
            "nom": "Étiquettes de vin — Open Food Facts",
            "source": "https://world.openfoodfacts.org/",
            "licence": "Données ODbL · photos sous licence libre (cf. chaque fiche produit)",
            "attribution": (
                "Données et photos © les contributeurs d'Open Food Facts, sous licence "
                "Open Database License (ODbL). Corpus régénérable via "
                "`manage.py corpus_openfoodfacts`."
            ),
            "note": (
                "Généré automatiquement : le code-barres fait office de vérité terrain "
                "non ambiguë (aucune annotation manuelle). « producteur » vient du champ "
                "`brands` et « vin » de `product_name` — champs contributifs, donc "
                "parfois approximatifs : un écart d'identification peut venir du corpus "
                "autant que du moteur. Les images ne sont pas versionnées, "
                "`evaluer_reconnaissance` les télécharge à la demande."
            ),
            "avertissement": (
                "NE PAS évaluer la source `openfoodfacts` sur ce corpus : il en est "
                "issu, le score serait de 100 % par construction et ne mesurerait rien. "
                "Ce corpus sert à évaluer les AUTRES sources (lwin, claude, wineapi…), "
                "pour lesquelles Open Food Facts joue le rôle d'arbitre indépendant."
            ),
            "cas": cas,
        }
        sortie.parent.mkdir(parents=True, exist_ok=True)
        sortie.write_text(
            json.dumps(manifeste, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self.stdout.write(self.style.SUCCESS(
            f"{len(cas)} cas écrits dans {sortie}\n"
            f"  évaluation : manage.py evaluer_reconnaissance --corpus {sortie}"
        ))

    # ------------------------------------------------------------------ #

    def _collecter(self, options) -> list[dict]:
        cas: list[dict] = []
        vus: set[str] = set()
        page = 1
        while len(cas) < options["nombre"] and page <= options["page_max"]:
            produits = self._page(page, options["pays"])
            if produits is None:
                break  # service indisponible : on garde ce qu'on a
            if not produits:
                self.stdout.write("  plus de résultats")
                break
            for produit in produits:
                entree = self._retenir(produit, vus)
                if entree:
                    cas.append(entree)
                    if len(cas) >= options["nombre"]:
                        break
            self.stdout.write(f"  page {page} — {len(cas)} cas retenus")
            page += 1
            time.sleep(_PAUSE)
        return cas

    def _page(self, page: int, pays: str) -> list[dict] | None:
        parametres = {
            "categories_tags_en": "wines",
            "fields": "code,product_name,brands,image_front_url,categories_tags",
            "page_size": 100,
            "page": page,
        }
        if pays:
            parametres["countries_tags_en"] = pays
        url = f"{_RECHERCHE}?{urllib.parse.urlencode(parametres)}"
        requete = urllib.request.Request(url, headers={"User-Agent": _UA})
        # Open Food Facts répond 503 quand il est chargé ou qu'on l'a trop
        # sollicité. Ce n'est pas une panne : on patiente en doublant l'attente,
        # plutôt que d'abandonner le corpus ou — pire — de marteler le service.
        attente = 5
        for essai in range(_ESSAIS_MAX):
            try:
                with urllib.request.urlopen(requete, timeout=30) as reponse:
                    return json.loads(reponse.read()).get("products", [])
            except Exception as exc:
                dernier = essai == _ESSAIS_MAX - 1
                self.stderr.write(self.style.WARNING(
                    f"  page {page} indisponible ({exc})"
                    + ("" if dernier else f" — nouvelle tentative dans {attente} s")
                ))
                if dernier:
                    return None
                time.sleep(attente)
                attente *= 2
        return None

    def _retenir(self, produit: dict, vus: set[str]) -> dict | None:
        """Filtre un produit -> cas d'évaluation, ou None s'il est inexploitable.

        Open Food Facts est contributif : beaucoup de fiches sont incomplètes. Un
        cas sans identité fiable mesurerait le bruit du corpus plutôt que le
        moteur, on préfère donc jeter large.
        """
        code = (produit.get("code") or "").strip()
        marque = (produit.get("brands") or "").split(",")[0].strip()
        nom = (produit.get("product_name") or "").strip()
        image = (produit.get("image_front_url") or "").strip()

        if not (code and marque and nom and image):
            return None

        # Vrai vin, et pas un dérivé (cf. _TAG_REQUIS).
        tags = set(produit.get("categories_tags") or [])
        if _TAG_REQUIS not in tags or tags & _TAGS_ECARTES:
            return None
        if code in vus:
            return None
        if marque.lower() in _MARQUES_ECARTEES:
            return None
        # Une marque d'un ou deux caractères n'identifie personne et ferait passer
        # n'importe quelle correspondance pour une réussite.
        if len(marque) < 3:
            return None
        # Un nom réduit à une contenance ou à « Rouge » ne distingue aucun vin.
        if len(nom) < 4 or nom.lower().replace(" ", "") in _NOMS_ECARTES:
            return None

        vus.add(code)
        return {
            "id": code,
            "url": image,
            "code_barres": code,
            "producteur": marque,
            "vin": nom,
        }
