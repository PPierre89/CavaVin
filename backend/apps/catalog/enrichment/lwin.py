"""Repli 100 % local : OCR Tesseract + correspondance floue sur le référentiel LWIN.

Dernier maillon de la cascade d'enrichissement (derrière Claude et wineapi) :
aucune clé d'API, aucun appel réseau. L'étiquette est lue par le binaire
``tesseract`` (sous-processus, désactivé s'il est absent), puis la sortie OCR —
ou la saisie texte — est rapprochée du référentiel LWIN importé en base via
``manage.py import_lwin`` (correspondance floue rapidfuzz, tolérante aux
erreurs d'OCR).

L'OCR est optimisé pour les photos d'étiquettes, un cas hostile à Tesseract
(polices stylisées, texte clair sur fond sombre, photos de téléphone) :
pré-traitement Pillow (orientation EXIF, niveaux de gris, redimensionnement,
autocontraste, inversion des étiquettes sombres), passes multiples de
segmentation (mise en page automatique + texte épars) dont les sorties sont
fusionnées, et filtrage des mots par confiance Tesseract (sortie TSV) pour ne
pas semer de faux tokens dans la correspondance floue.

Qualité volontairement orientée *précision* : on exige que tous les tokens
significatifs d'une référence soient retrouvés dans l'entrée, plutôt que de
proposer un vin douteux qui polluerait le catalogue partagé. Un doute = un
simple miss (404 côté API, l'utilisateur saisit à la main).
"""

from __future__ import annotations

import logging
import re
import subprocess
import unicodedata
from io import BytesIO

from django.conf import settings
from rapidfuzz import fuzz

from .base import EnrichmentProvider, NormalizedWine
from .normalize import parse_vintage

logger = logging.getLogger(__name__)

# Mots trop fréquents sur les étiquettes pour identifier un vin : on ne les
# compte pas comme tokens « significatifs » (sinon « château » ou « grand cru »
# suffiraient à faire correspondre n'importe quelle référence).
_STOPWORDS = {
    "chateau", "domaine", "clos", "maison", "cave", "caves", "vignobles",
    "grand", "grande", "cru", "crus", "classe", "premier", "vin", "vins",
    "wine", "appellation", "controlee", "protegee", "origine", "mis", "mise",
    "bouteille", "bouteilles", "propriete", "proprietaire", "recolte",
    "product", "produce", "produit", "france", "les", "des", "the", "and",
    "vieilles", "vignes", "reserve", "cuvee", "selection", "millesime",
}

# Score rapidfuzz minimal (0-100) pour considérer deux tokens équivalents —
# la tolérance aux coquilles d'OCR (« margaux » lu « rnargaux »).
_TOKEN_RATIO = 85

# --- Réglages OCR ---
# Modes de segmentation Tesseract essayés sur chaque variante d'image :
# 3 = mise en page automatique, 11 = texte épars (mentions dispersées d'une étiquette).
_PSM_PASSES = (3, 11)
# Confiance Tesseract minimale (0-100) pour garder un mot de la sortie TSV.
_OCR_CONF_MIN = 40
# Confiance moyenne minimale des mots gardés d'une passe : sous ce seuil, la
# passe entière est du bruit (sur une photo illisible, le mode texte épars
# hallucine des centaines de « mots » à confiance moyenne ~50, quand du vrai
# texte sort à ~90) et est écartée en bloc.
_OCR_CONF_MOYENNE = 65
# Grand côté cible du redimensionnement : Tesseract lit mal les petites photos
# et perd du temps sur les très grandes.
_OCR_TAILLE_CIBLE = 1600
# Luminance moyenne (0-255) sous laquelle une étiquette est considérée sombre
# (texte clair) : Tesseract préfère du texte sombre sur fond clair, on inverse.
_OCR_LUMINANCE_SOMBRE = 110
# Arrêt anticipé des passes : nombre de tokens significatifs jugé suffisant.
_OCR_TOKENS_SUFFISANTS = 4

# Mémo module : passe à False au premier échec du pack fra+eng, pour ne pas
# payer un essai voué à l'échec à chaque passe suivante.
_langues_ok = True


def _variantes(data: bytes) -> list[bytes]:
    """Variantes PNG pré-traitées d'une photo d'étiquette pour l'OCR.

    Pré-traitement Pillow : orientation EXIF (photos de téléphone), niveaux de
    gris, redimensionnement vers ``_OCR_TAILLE_CIBLE``, autocontraste. Si
    l'étiquette est sombre (texte clair sur fond foncé, cas fréquent), une
    seconde variante inversée est produite. En cas d'image illisible ou de
    Pillow indisponible, on retombe sur les octets bruts (comportement
    d'origine, Tesseract se débrouille)."""
    try:
        from PIL import Image, ImageOps, ImageStat

        image = Image.open(BytesIO(data))
        image = ImageOps.exif_transpose(image)
        image = image.convert("L")
        grand_cote = max(image.size)
        if grand_cote and grand_cote != _OCR_TAILLE_CIBLE:
            facteur = _OCR_TAILLE_CIBLE / grand_cote
            image = image.resize(
                (max(1, round(image.width * facteur)), max(1, round(image.height * facteur))),
                Image.LANCZOS,
            )
        image = ImageOps.autocontrast(image, cutoff=1)

        variantes = [image]
        if ImageStat.Stat(image).mean[0] < _OCR_LUMINANCE_SOMBRE:
            variantes.append(ImageOps.invert(image))

        sorties = []
        for v in variantes:
            tampon = BytesIO()
            v.save(tampon, "PNG")
            sorties.append(tampon.getvalue())
        return sorties
    except Exception as exc:  # image corrompue, format exotique...
        logger.warning("pré-traitement OCR impossible (%s), image brute utilisée", exc)
        return [data]


def _texte_tsv(tsv: str) -> str:
    """Extrait le texte d'une sortie TSV Tesseract, mots douteux écartés.

    Colonnes TSV : level, page, block, par, line, word, left, top, width,
    height, conf, text — on garde les mots (level 5) dont la confiance
    atteint ``_OCR_CONF_MIN``. Si la confiance moyenne des mots gardés reste
    sous ``_OCR_CONF_MOYENNE``, la passe entière est considérée comme une
    hallucination sur du bruit et écartée."""
    mots: list[str] = []
    confs: list[float] = []
    for ligne in tsv.splitlines()[1:]:
        champs = ligne.split("\t")
        if len(champs) < 12:
            continue
        try:
            conf = float(champs[10])
        except ValueError:
            continue
        mot = champs[11].strip()
        if conf >= _OCR_CONF_MIN and mot:
            mots.append(mot)
            confs.append(conf)
    if not mots or sum(confs) / len(confs) < _OCR_CONF_MOYENNE:
        return ""
    return " ".join(mots)


def _tokens(texte: str) -> set[str]:
    """Tokens significatifs d'un texte : minuscules, sans accents, sans
    ponctuation, sans millésimes/nombres, sans mots d'étiquette génériques."""
    sans_accents = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    mots = re.split(r"[^a-z0-9]+", sans_accents.lower())
    return {
        m for m in mots
        if len(m) >= 3 and not m.isdigit() and m not in _STOPWORDS
    }


def _token_trouve(token_ref: str, tokens_entree: set[str]) -> float:
    """Meilleur score de correspondance (0-100) d'un token du référentiel dans
    l'entrée. Exact d'abord (rapide), floue ensuite (coquilles d'OCR)."""
    if token_ref in tokens_entree:
        return 100.0
    return max((fuzz.ratio(token_ref, t) for t in tokens_entree), default=0.0)


# Cache en mémoire du référentiel (rechargé quand le nombre d'entrées change,
# c.-à-d. après un import) : évite de relire ~100 000 lignes à chaque scan.
_cache: dict = {"version": None, "refs": [], "idf": {}}


def _referentiel() -> tuple[list[tuple[int, tuple, tuple, str]], dict[str, float]]:
    """Référentiel LWIN prêt pour la correspondance.

    Renvoie ``(refs, idf)`` où ``refs`` liste (id, tokens requis, tokens bonus,
    chaîne complète) et ``idf`` pondère chaque token par sa rareté dans le
    corpus — « palmer » (rare) pèse plus que « margaux » (nom de commune
    omniprésent), ce qui départage les étiquettes qui mentionnent plusieurs noms.

    - Tokens **requis** : ceux du nom du vin s'il existe, sinon ceux du
      producteur — « La Tâche » doit correspondre sans exiger « Domaine de la
      Romanée-Conti » dans l'entrée.
    - Tokens **bonus** : ceux de la sous-région. Sur le dump réel, l'identité
      des vins de Bourgogne est portée par la sous-région (climat) avec un nom
      de vin vide — « Romanée-Conti / La Tâche » désigne le bon climat parmi
      toutes les entrées du même domaine. Jamais requis (la commune d'un
      Bordeaux ne doit pas suffire à le faire correspondre), seulement comptés
      quand ils sont retrouvés.
    """
    import math
    import sys

    from ..models import ReferenceLwin

    version = ReferenceLwin.objects.count()
    if _cache["version"] != version:
        refs = []
        df: dict[str, int] = {}
        lignes = ReferenceLwin.objects.values_list("pk", "producteur", "vin", "sous_region")
        for pk, producteur, vin, sous_region in lignes:
            requis = _tokens(vin) or _tokens(producteur)
            if not requis:
                continue
            bonus = _tokens(sous_region) - requis
            # Tokens internés + tuples : le dump réel fait ~200 000 lignes, on
            # partage les chaînes récurrentes (« margaux », « pinot »…) en mémoire.
            refs.append((
                pk,
                tuple(sys.intern(t) for t in requis),
                tuple(sys.intern(t) for t in bonus),
                f"{producteur} {vin} {sous_region}".strip(),
            ))
            for t in requis | bonus:
                df[t] = df.get(t, 0) + 1
        n = max(len(refs), 1)
        _cache["version"] = version
        _cache["refs"] = refs
        _cache["idf"] = {t: math.log(n / d) + 1 for t, d in df.items()}
    return _cache["refs"], _cache["idf"]


class LwinProvider(EnrichmentProvider):
    """
    OCR local (Tesseract) + référentiel LWIN — repli gratuit et hors-ligne.

    - ``lookup_by_text`` : correspondance floue de la saisie sur le référentiel.
    - ``lookup_by_image`` : OCR de l'étiquette via le binaire ``tesseract``
      (sous-processus, aucune dépendance Python), puis même correspondance.

    Ne lève jamais d'``EnrichmentError`` : une source locale n'a ni quota ni
    clé, tout échec est un simple miss. Sans référentiel importé (table vide)
    ou sans binaire tesseract, le provider répond None sans bruit.
    """

    name = "lwin"

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        return settings.LWIN_ENABLED

    def lookup_by_text(self, query: str) -> NormalizedWine | None:
        return self._correspondre(query)

    def lookup_by_image(self, data: bytes, content_type: str) -> NormalizedWine | None:
        texte = self._ocr(data)
        if not texte:
            return None
        # ocr=True : la sortie OCR d'une photo illisible est un flot de faux
        # mots — on exige une corroboration plus forte qu'une saisie humaine.
        return self._correspondre(texte, ocr=True)

    def _ocr(self, data: bytes) -> str:
        """Texte de l'étiquette, fusion de plusieurs passes Tesseract.

        Chaque variante d'image (pré-traitée, et inversée si l'étiquette est
        sombre) est lue avec deux modes de segmentation : mise en page
        automatique (PSM 3) et texte épars (PSM 11, bien adapté aux mentions
        dispersées d'une étiquette). Les sorties sont concaténées : la
        correspondance en aval n'exige que la présence des tokens du
        référentiel, le surplus ne coûte rien. Arrêt anticipé dès qu'une
        variante a livré assez de tokens significatifs."""
        import time

        # TESSERACT_TIMEOUT est le budget TOTAL de l'OCR : les passes se
        # partagent le temps restant, une photo pathologique (le mode texte
        # épars peut s'enliser sur du bruit) ne bloque jamais plus longtemps.
        echeance = time.monotonic() + settings.TESSERACT_TIMEOUT
        morceaux: list[str] = []
        for image in _variantes(data):
            for psm in _PSM_PASSES:
                restant = echeance - time.monotonic()
                if restant <= 0:
                    return "\n".join(morceaux)
                texte = self._tesseract(image, psm, timeout=restant)
                if texte:
                    morceaux.append(texte)
                if len(_tokens(" ".join(morceaux))) >= _OCR_TOKENS_SUFFISANTS:
                    return "\n".join(morceaux)  # inutile de payer les passes suivantes
        return "\n".join(morceaux)

    def _tesseract(self, image: bytes, psm: int, timeout: float | None = None) -> str:
        """Une passe tesseract (stdin -> TSV), filtrée par confiance par mot.

        La sortie TSV donne une confiance 0-100 par mot : écarter les mots
        douteux évite que du bruit d'OCR aille fuzzy-matcher une mauvaise
        référence. ``--dpi 300`` lève l'avertissement des images sans
        métadonnées (photos recadrées)."""
        global _langues_ok
        commande = [settings.TESSERACT_CMD, "stdin", "stdout"]
        if _langues_ok:
            commande += ["-l", "fra+eng"]
        commande += ["--dpi", "300", "--psm", str(psm), "tsv"]
        try:
            resultat = subprocess.run(
                commande,
                input=image,
                capture_output=True,
                timeout=timeout if timeout is not None else settings.TESSERACT_TIMEOUT,
            )
            if resultat.returncode != 0 and _langues_ok:
                # Pack de langue absent : on retombe sur la langue par défaut,
                # et on s'en souvient pour les passes suivantes.
                _langues_ok = False
                return self._tesseract(image, psm, timeout=timeout)
            if resultat.returncode != 0:
                logger.warning("tesseract a échoué: %s", resultat.stderr[:200])
                return ""
            return _texte_tsv(resultat.stdout.decode("utf-8", errors="replace"))
        except FileNotFoundError:
            logger.info("tesseract introuvable (%s) : OCR local inactif", settings.TESSERACT_CMD)
            return ""
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("tesseract: %s", exc)
            return ""

    def _correspondre(self, texte: str, ocr: bool = False) -> NormalizedWine | None:
        """Meilleure référence LWIN dont TOUS les tokens significatifs sont
        retrouvés dans l'entrée (tolérance floue par token, pour les coquilles
        d'OCR). À égalité, la référence la plus spécifique (tokens les plus
        rares, pondération IDF) puis la plus proche de l'entrée l'emporte.

        En mode ``ocr``, l'entrée peut être un flot de faux mots hallucinés
        par Tesseract sur une photo illisible : un candidat n'est retenu que
        s'il est corroboré par au moins deux tokens retrouvés, ou par un
        unique token long (≥ 5 caractères) retrouvé à l'identique — un junk
        de trois lettres ne suffit plus à « identifier » un vin."""
        tokens_entree = _tokens(texte)
        if not tokens_entree:
            return None
        refs, idf = _referentiel()

        # Mémoïsation par token unique du référentiel : ~100 000 références
        # partagent un vocabulaire bien plus petit, on ne score chaque token
        # qu'une fois par requête.
        scores_vocab: dict[str, float] = {}

        def score_token(token_ref: str) -> float:
            if token_ref not in scores_vocab:
                scores_vocab[token_ref] = _token_trouve(token_ref, tokens_entree)
            return scores_vocab[token_ref]

        # Classement : une référence dont TOUS les tokens requis sont retrouvés
        # à l'identique prime toujours sur une correspondance floue (« La Tâche »
        # exact bat « Taches » flou) ; puis l'IDF pondéré par la qualité du match
        # départage (token rare ET bien retrouvé), les tokens bonus (sous-région)
        # retrouvés s'y ajoutant — ils désignent le bon climat parmi les entrées
        # d'un même domaine ; enfin la proximité globale à l'entrée.
        meilleur: tuple | None = None  # (exact, idf pondéré, ratio global, score moyen, -pk)
        for pk, requis, bonus, chaine in refs:
            scores = [score_token(t) for t in requis]
            if min(scores) < _TOKEN_RATIO:
                continue  # au moins un token requis est absent : trop risqué
            bonus_trouves = [t for t in bonus if score_token(t) >= _TOKEN_RATIO]
            if ocr and len(requis) + len(bonus_trouves) < 2 and not (
                len(requis[0]) >= 5 and scores[0] == 100.0
            ):
                continue  # sortie OCR : préférer un miss à un vin douteux
            poids = sum(idf.get(t, 1.0) * (s / 100) ** 2 for t, s in zip(requis, scores))
            poids += sum(
                idf.get(t, 1.0) * (score_token(t) / 100) ** 2 for t in bonus_trouves
            )
            candidat = (
                min(scores) == 100.0,
                poids,
                fuzz.token_set_ratio(chaine.lower(), texte.lower()),
                sum(scores) / len(scores),
                -pk,  # départage stable
            )
            if meilleur is None or candidat > meilleur:
                meilleur = candidat
        if meilleur is None:
            return None

        from ..models import ReferenceLwin

        ref = ReferenceLwin.objects.get(pk=-meilleur[4])
        return self._to_normalized(ref, confiance=meilleur[3] / 100, millesime=parse_vintage(texte))

    def _to_normalized(self, ref, confiance: float, millesime: int | None) -> NormalizedWine:
        # Détail minimal au format wineapi : persisté par ingest.upsert_cuvee
        # via wine_profile.normalize_detail (région, pays, classification, LWIN).
        detail = {
            "name": ref.vin or ref.producteur,
            "winery": {"name": ref.producteur},
            "region": {"name": ref.region, "country": ref.pays},
            "classification": ref.classification,
            "lwinCode": ref.lwin,
        }
        return NormalizedWine(
            domaine_nom=ref.producteur,
            cuvee_nom=ref.vin or ref.producteur,
            couleur=ref.couleur,
            appellation=ref.sous_region or ref.region,
            cepages=[],
            millesime=millesime,
            source=self.name,
            reference_externe_id="",  # source locale : rien à re-synchroniser
            raw={
                "confidence": round(confiance, 2),
                "auto_added": False,
                "pending": False,
                "suggestions": [],
                "region": ref.region,
                "pays": ref.pays,
                "description": "",
                "note": None,
                "alcool": None,
                "prix": None,
                "prix_marchands": [],
                "wineapi_detail": detail,
            },
        )
