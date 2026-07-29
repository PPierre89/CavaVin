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
from rapidfuzz import fuzz, process

from ..runtime_config import source_activee
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
    # Mobilier d'étiquette anglophone, symétrique de château/domaine/cave :
    # omniprésent sur les vins du Nouveau Monde, il n'identifie personne.
    "winery", "vineyard", "vineyards", "estate", "estates", "cellars",
    "county", "wines", "bottled",
}

# Noms de cépages et de styles : présents sur d'innombrables étiquettes, ils
# ne suffisent pas à identifier un vin. Une référence dont le nom n'est fait
# que de ces mots (ex. vin « Riesling », « Brut Rosé ») exige en plus qu'au
# moins un token du producteur soit retrouvé dans l'entrée — sinon
# « chardonnay » seul renverrait le chardonnay d'un producteur au hasard.
_GENERIQUES = {
    # cépages
    "riesling", "chardonnay", "sauvignon", "merlot", "cabernet", "franc",
    "pinot", "noir", "gris", "meunier", "syrah", "shiraz", "grenache",
    "gamay", "malbec", "viognier", "chenin", "semillon", "muscat",
    "gewurztraminer", "zinfandel", "sangiovese", "nebbiolo", "barbera",
    "tempranillo", "carmenere", "mourvedre", "cinsault", "carignan",
    "vermentino", "marsanne", "roussanne", "aligote", "sylvaner", "savagnin",
    "trousseau", "poulsard", "melon", "colombard", "petit", "verdot",
    # styles / mentions
    "blanc", "blancs", "rouge", "rose", "brut", "extra", "demi", "sec",
    "doux", "moelleux", "nature", "tradition", "prestige", "classique",
    "annee", "vendanges", "tardives",
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


def _variantes(data: bytes, rotation: int = 0, zone: tuple | None = None) -> list[bytes]:
    """Variantes PNG pré-traitées d'une photo d'étiquette pour l'OCR.

    Pré-traitement Pillow : orientation EXIF (photos de téléphone), rotation
    corrective (OSD, photos tournées sans EXIF), recadrage optionnel sur la
    ``zone`` de texte (coordonnées dans l'image déjà redimensionnée : le
    recadrage est fait en pleine résolution PUIS remis à la taille cible, ce
    qui rend à Tesseract les détails d'une étiquette petite dans le cadre),
    niveaux de gris, redimensionnement vers ``_OCR_TAILLE_CIBLE``,
    autocontraste. Si l'étiquette est sombre (texte clair sur fond foncé, cas
    fréquent), une seconde variante inversée est produite. En cas d'image
    illisible ou de Pillow indisponible, on retombe sur les octets bruts
    (comportement d'origine, Tesseract se débrouille)."""
    try:
        from PIL import Image, ImageOps, ImageStat

        image = Image.open(BytesIO(data))
        image = ImageOps.exif_transpose(image)
        if rotation:
            # « Rotate » OSD = rotation horaire à appliquer ; PIL tourne en
            # anti-horaire, d'où le signe.
            image = image.rotate(-rotation, expand=True)
        image = image.convert("L")
        if zone:
            # La zone est exprimée dans l'image redimensionnée de la passe
            # précédente : on la ramène à la pleine résolution avant recadrage.
            facteur_prec = _OCR_TAILLE_CIBLE / max(image.size)
            gauche, haut, droite, bas = (round(v / facteur_prec) for v in zone)
            image = image.crop((
                max(0, gauche), max(0, haut),
                min(image.width, max(gauche + 1, droite)),
                min(image.height, max(haut + 1, bas)),
            ))
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


def _pivoter(image: bytes, degres: int) -> bytes:
    """Pivote une variante PNG (sens horaire) — force brute d'orientation sur
    un recadrage quand l'OSD n'a rien détecté. Octets illisibles : inchangés."""
    try:
        from PIL import Image

        pivotee = Image.open(BytesIO(image)).rotate(-degres, expand=True)
        tampon = BytesIO()
        pivotee.save(tampon, "PNG")
        return tampon.getvalue()
    except Exception:
        return image


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


# Part maximale de l'image couverte par la zone de texte au-delà de laquelle
# le recadrage n'apporte rien (l'étiquette occupe déjà le cadre).
_ZONE_COUVERTURE_MAX = 0.5
# Marge ajoutée autour de la zone de texte détectée (part de chaque dimension).
_ZONE_MARGE = 0.08


def _zone_texte(tsvs: list[str]) -> tuple[int, int, int, int] | None:
    """Boîte englobante (gauche, haut, droite, bas) des mots confiants des
    sorties TSV, marge comprise — le « détecteur d'étiquette » du pipeline
    (l'esprit de WineNot, sans réseau de neurones) : quand la bouteille est
    loin dans le cadre, l'union des boîtes de mots localise l'étiquette, que
    l'on recadre puis re-OCRise en pleine résolution.

    Renvoie None si aucun mot confiant n'est localisé ou si la zone couvre
    déjà l'essentiel de l'image (rien à gagner). Les dimensions de la page
    sont lues dans le TSV lui-même (ligne level 1)."""
    page: tuple[int, int] | None = None
    gauche, haut, droite, bas = None, None, None, None
    for tsv in tsvs:
        for ligne in tsv.splitlines()[1:]:
            champs = ligne.split("\t")
            if len(champs) < 12:
                continue
            try:
                niveau = int(champs[0])
                l, t, w, h = (int(champs[i]) for i in range(6, 10))
                conf = float(champs[10])
            except ValueError:
                continue
            if niveau == 1:
                page = (w, h)
            elif niveau == 5 and conf >= _OCR_CONF_MIN and champs[11].strip():
                gauche = l if gauche is None else min(gauche, l)
                haut = t if haut is None else min(haut, t)
                droite = l + w if droite is None else max(droite, l + w)
                bas = t + h if bas is None else max(bas, t + h)
    if page is None or gauche is None:
        return None
    largeur_page, hauteur_page = page
    if not largeur_page or not hauteur_page:
        return None
    couverture = ((droite - gauche) * (bas - haut)) / (largeur_page * hauteur_page)
    if couverture >= _ZONE_COUVERTURE_MAX:
        return None  # l'étiquette occupe déjà le cadre, inutile de recadrer
    marge_l, marge_h = round(largeur_page * _ZONE_MARGE), round(hauteur_page * _ZONE_MARGE)
    return (
        max(0, gauche - marge_l),
        max(0, haut - marge_h),
        min(largeur_page, droite + marge_l),
        min(hauteur_page, bas + marge_h),
    )


def _normaliser(texte: str) -> str:
    """Chaîne normalisée pour la comparaison : minuscules, sans accents, tout
    séparateur (trait d'union compris : « Lynch-Bages » -> « lynch bages »)
    ramené à une espace."""
    # Les ligatures n'ont pas de décomposition NFKD : sans ce remplacement,
    # « Cœur » deviendrait « cur » et ne correspondrait plus à « coeur ».
    texte = (texte or "").replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE")
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return " ".join(m for m in re.split(r"[^a-z0-9]+", sans_accents.lower()) if m)


def _tokens(texte: str) -> set[str]:
    """Tokens significatifs d'un texte : minuscules, sans accents, sans
    ponctuation, sans millésimes/nombres, sans mots d'étiquette génériques."""
    return {
        m for m in _normaliser(texte).split()
        if len(m) >= 3 and not m.isdigit() and m not in _STOPWORDS
    }


def _libelle(ref) -> str:
    """Libellé présentable d'une référence LWIN (pour les suggestions)."""
    if ref.vin:
        return f"{ref.producteur} - {ref.vin}"
    if ref.sous_region:
        return f"{ref.producteur} ({ref.sous_region})"
    return ref.producteur


# Score attribué à une correspondance par préfixe (« marg » -> « margaux ») :
# sous l'exact (100) pour qu'un mot complet l'emporte, au-dessus du seuil flou.
_PREFIX_SCORE = 90.0


def _classement(texte: str, ocr: bool = False, prefixe: bool = False) -> list[tuple]:
    """Candidats du référentiel classés pour une entrée, du meilleur au moins bon.

    Chaque entrée du classement est un tuple de critères décroissants terminé
    par ``-pk`` (lire le pk via ``-entree[-1]``).

    Deux régimes :
    - **identification** (par défaut) — stricte : TOUS les tokens requis d'une
      référence doivent être retrouvés ; voir ``LwinProvider._correspondre``.
    - **``prefixe``** (autocomplétion au fil de la frappe) — permissive : le
      dernier token de l'entrée peut être un début de mot (« marg » ->
      « margaux »), et une couverture pondérée partielle (≥ 50 % du poids IDF
      des tokens requis) suffit — l'utilisateur valide visuellement la
      suggestion, contrairement à l'identification. Le classement privilégie
      alors la proximité globale à l'entrée (« chateau marg » -> Château
      Margaux avant un obscur Margalaine au token plus rare)."""
    import bisect

    tokens_entree = _tokens(texte)
    texte_norm = _normaliser(texte)
    # Une entrée sans aucun token distinctif (que des cépages/styles :
    # « riesling 2019 », « brut rosé ») ne peut identifier aucun vin —
    # et laisserait le flou accrocher des coquilles du dump (« Rieling R »).
    if not tokens_entree or tokens_entree <= _GENERIQUES:
        return []
    index = _referentiel()
    refs, idf, postings = index["refs"], index["idf"], index["postings"]

    # Score de chaque token du vocabulaire retrouvé dans l'entrée : les
    # correspondances exactes d'abord (dictionnaire), puis un passage flou
    # rapidfuzz sur le vocabulaire entier (C++, une passe par token
    # d'entrée) pour les coquilles d'OCR (« margeaux » ~ « margaux »).
    scores_vocab: dict[str, float] = {}
    for t in tokens_entree:
        if t in postings:
            scores_vocab[t] = 100.0
    for t in tokens_entree:
        if len(t) <= 3:
            # Trop court pour le flou : « vol » (mention « BY VOL ») accroche
            # « vola »/« vole » à 86 — l'exact reste permis, pas l'à-peu-près.
            continue
        generique = t in _GENERIQUES
        for trouve, score, _ in process.extract(
            t, index["vocab"], scorer=fuzz.ratio, score_cutoff=_TOKEN_RATIO, limit=None
        ):
            # Un token générique de l'entrée ne doit pas accrocher en flou un
            # token distinctif du vocabulaire : « rouge » n'est pas « rouget ».
            if generique and trouve not in _GENERIQUES:
                continue
            if score > scores_vocab.get(trouve, 0.0):
                scores_vocab[trouve] = score
    if prefixe:
        dernier = _dernier_token(texte)
        # Un préfixe générique ne s'étend pas : « rouge » en cours de frappe
        # ne doit pas devenir « rougeot »/« rouget » et polluer le classement.
        if dernier and dernier not in _GENERIQUES:
            tri = index["vocab_trie"]
            i = bisect.bisect_left(tri, dernier)
            while i < len(tri) and tri[i].startswith(dernier):
                if _PREFIX_SCORE > scores_vocab.get(tri[i], 0.0):
                    scores_vocab[tri[i]] = _PREFIX_SCORE
                i += 1

    # Candidates : seules les références partageant au moins un token
    # retrouvé sont examinées (index inversé) — quelques centaines au lieu
    # des ~200 000 du référentiel.
    candidates: set[int] = set()
    for t in scores_vocab:
        candidates.update(postings[t])

    # Les ancres (producteur d'un vin au nom générique) ne peuvent être
    # satisfaites que par un token distinctif de l'entrée : « rouge » ne doit
    # pas « ancrer » Rouget par correspondance floue avec lui-même.
    tokens_distinctifs = tokens_entree - _GENERIQUES

    # Mémos de requête : `idf`, `scores_vocab` et `tokens_distinctifs` sont fixés
    # pour toute la durée du classement, si bien que le poids d'un token et le
    # verdict d'une ancre ne dépendent que du token. Sans ces mémos, les mêmes
    # valeurs étaient recalculées une fois par candidate — sur un référentiel
    # réel, des centaines de milliers de fois par recherche (cf. profil : le
    # coût dominant était cette boucle Python, pas la correspondance floue).
    _memo_ancre: dict[str, bool] = {}
    _memo_poids: dict[str, float] = {}
    _memo_idf: dict[str, float] = {}

    def ancre_trouvee(ancre: str) -> bool:
        verdict = _memo_ancre.get(ancre)
        if verdict is None:
            if ancre in tokens_distinctifs:
                verdict = True
            else:
                verdict = max(
                    (fuzz.ratio(ancre, t) for t in tokens_distinctifs), default=0.0
                ) >= _TOKEN_RATIO
            _memo_ancre[ancre] = verdict
        return verdict

    def poids_token(token: str) -> float:
        """Poids IDF d'un token, pondéré par la qualité de sa correspondance."""
        p = _memo_poids.get(token)
        if p is None:
            p = idf.get(token, 1.0) * (scores_vocab.get(token, _TOKEN_RATIO) / 100) ** 2
            _memo_poids[token] = p
        return p

    def poids_idf(token: str) -> float:
        """Poids IDF brut d'un token (couverture attendue, sans pondération)."""
        p = _memo_idf.get(token)
        if p is None:
            p = idf.get(token, 1.0)
            _memo_idf[token] = p
        return p

    # Classement (identification) : une référence dont TOUS les tokens requis
    # sont retrouvés à l'identique prime toujours sur une correspondance floue
    # (« La Tâche » exact bat « Taches » flou) ; puis l'IDF pondéré par la
    # qualité du match départage (token rare ET bien retrouvé), les tokens
    # bonus (sous-région) et ancres (producteur) retrouvés s'y ajoutant ;
    # enfin la proximité globale à l'entrée.
    classement: list[tuple] = []
    for i in candidates:
        pk, requis, bonus, ancres, chaine, long_vin = refs[i]
        scores = [scores_vocab.get(t, 0.0) for t in requis]
        ancres_trouvees = [t for t in ancres if ancre_trouvee(t)]
        if ancres and not ancres_trouvees:
            continue  # nom générique (« Riesling ») sans son producteur
        bonus_trouves = [t for t in bonus if scores_vocab.get(t, 0.0) >= _TOKEN_RATIO]
        poids_ancres = sum(poids_token(t) for t in bonus_trouves)
        if ancres:
            # Pondéré par la couverture du producteur : un producteur reconnu
            # en entier (« Mayacamas Vineyards », 2/2) doit peser plus que
            # deux mots géographiques sur sept (« Premiere Napa Valley
            # (Hewitt…) » accroché par « napa » + « valley », 2/7).
            poids_ancres += (len(ancres_trouvees) / len(ancres)) * sum(
                poids_token(t) for t in ancres_trouvees
            )

        if prefixe:
            # Autocomplétion : couverture pondérée partielle admise, mais au
            # moins un token distinctif doit soutenir le candidat.
            # (Un token retenu ici a `s >= _TOKEN_RATIO`, donc `s` *est*
            # `scores_vocab[t]` : `poids_token(t)` calcule le même produit.)
            poids_total = sum(poids_idf(t) for t in requis)
            poids_trouve = sum(poids_token(t) for t, s in zip(requis, scores) if s >= _TOKEN_RATIO)
            if poids_trouve < poids_total * 0.5:
                continue
            distinctif = ancres_trouvees or any(
                t not in _GENERIQUES and s >= _TOKEN_RATIO for t, s in zip(requis, scores)
            )
            if not distinctif:
                continue
            classement.append((
                fuzz.token_set_ratio(chaine, texte_norm),
                poids_trouve + poids_ancres,
                poids_trouve / poids_total,
                # À égalité parfaite, le nom de cuvée le plus court (le grand
                # vin plutôt que sa déclinaison : « Riesling » avant
                # « Riesling Réserve ») est le plus proche de la saisie.
                -long_vin,
                -pk,  # départage stable
            ))
            continue

        if min(scores) < _TOKEN_RATIO:
            continue  # au moins un token requis est absent : trop risqué
        if ocr and len(requis) + len(bonus_trouves) + len(ancres_trouvees) < 2 and not (
            len(requis[0]) >= 6 and scores[0] == 100.0
        ):
            # Sortie OCR : préférer un miss à un vin douteux. L'exception du
            # token unique exige ≥ 6 caractères : sur photos réelles, un junk
            # de 5 lettres (« rossa ») suffisait à accrocher une référence.
            continue
        poids = sum(poids_token(t) for t in requis)
        classement.append((
            # « Exact avant flou » vaut pour une saisie humaine (« La Tâche »
            # exact bat « Taches » flou) mais pas pour l'OCR, où les
            # troncatures sont normales : « Lynch-Bage » lu sur l'étiquette ne
            # doit pas perdre contre un « Pauillac » exact mais moins
            # spécifique — en mode OCR, le poids IDF départage.
            not ocr and min(scores) == 100.0,
            poids + poids_ancres,
            fuzz.token_set_ratio(chaine, texte_norm),
            sum(scores) / len(scores),
            -pk,  # départage stable
        ))
    classement.sort(reverse=True)
    return classement


def _dernier_token(texte: str) -> str:
    """Dernier token significatif de l'entrée, dans l'ordre de saisie (celui
    que l'utilisateur est peut-être en train de taper)."""
    for m in reversed(_normaliser(texte).split()):
        if len(m) >= 3 and not m.isdigit() and m not in _STOPWORDS:
            return m
    return ""


def rechercher_lwin(texte: str, limite: int = 6, couleur: str | None = None) -> list[dict]:
    """Recherche dynamique (autocomplétion) dans le référentiel LWIN.

    Le dernier token est traité comme un préfixe (recherche au fil de la
    frappe). ``couleur`` filtre les résultats (les références de couleur
    inconnue sont conservées : inconnu n'est pas une contradiction). Renvoie
    des dictionnaires prêts pour l'API, du meilleur candidat au moins bon,
    chacun portant un ``score`` de similarité 0-1 (proximité globale à
    l'entrée pondérée par la couverture des tokens de la référence) qui sert
    à ``evaluer_confiance``."""
    classement = _classement(texte, prefixe=True)
    if not classement:
        return []

    from ..models import ReferenceLwin

    # Tuple préfixe : (ratio 0-100, poids, couverture 0-1, -pk).
    scores = {-c[-1]: round((c[0] / 100) * (0.6 + 0.4 * c[2]), 2) for c in classement[: limite * 4]}
    refs_db = ReferenceLwin.objects.in_bulk(scores)
    resultats: list[dict] = []
    for pk, score in scores.items():
        ref = refs_db.get(pk)
        if ref is None:
            continue
        if couleur and ref.couleur not in (couleur, "AUTRE"):
            continue
        libelle = _libelle(ref)
        # Le dump contient des doublons (même vin sous plusieurs codes LWIN) :
        # deux suggestions au libellé identique n'aideraient pas l'utilisateur.
        if any(r["libelle"] == libelle for r in resultats):
            continue
        resultats.append({
            "lwin": ref.lwin,
            "libelle": libelle,
            "producteur": ref.producteur,
            "vin": ref.vin,
            "appellation": ref.sous_region or ref.region,
            "region": ref.region,
            "pays": ref.pays,
            "couleur": ref.couleur,
            "score": score,
        })
        if len(resultats) >= limite:
            break
    return resultats


# Seuils de la décision « sûr / hésitant » : score minimal du meilleur candidat
# quand il est talonné, marge exigée sur le deuxième, et score minimal quand il
# est seul en lice (calibrés sur le dump réel : « petrus » ou « opus one »
# dominent nettement, « dom perignon » laisse P2/P3 à égalité -> hésitation).
_SEUIL_SUR = 0.85
_ECART_SUR = 0.10
_SEUIL_SUR_UNIQUE = 0.75


def evaluer_confiance(resultats: list[dict]) -> str | None:
    """Décision d'affichage pour une liste de résultats de ``rechercher_lwin``.

    - ``"sur"`` : le meilleur candidat domine nettement -> l'appli peut
      proposer directement sa fiche pré-remplie.
    - ``"hesitant"`` : plusieurs candidats plausibles -> l'appli présente les
      suggestions et sollicite une vérification manuelle.
    - ``None`` : aucun résultat."""
    if not resultats:
        return None
    meilleur = resultats[0]["score"]
    if len(resultats) == 1:
        return "sur" if meilleur >= _SEUIL_SUR_UNIQUE else "hesitant"
    if meilleur >= _SEUIL_SUR and meilleur - resultats[1]["score"] >= _ECART_SUR:
        return "sur"
    return "hesitant"


# Cache en mémoire du référentiel (rechargé quand le nombre d'entrées change,
# c.-à-d. après un import) : évite de relire ~200 000 lignes à chaque scan.
_cache: dict = {
    "version": None, "refs": [], "idf": {}, "postings": {}, "vocab": [], "vocab_trie": [],
}


# Sonde de fraîcheur du référentiel : le COUNT(*) sur ~200 000 lignes est un
# balayage complet côté SQLite, et la recherche dynamique appelle ``_referentiel``
# à chaque frappe. On n'interroge donc la base qu'une fois par ``_VERSION_TTL`` ;
# un import LWIN devient visible au plus tard après ce délai.
_VERSION_TTL = 30  # secondes
_version_memo: dict = {"valeur": None, "expire_le": 0.0}


def _version_referentiel() -> int | None:
    """Nombre d'entrées LWIN, sondé au plus une fois par ``_VERSION_TTL``."""
    import time

    from ..models import ReferenceLwin

    maintenant = time.monotonic()
    if maintenant >= _version_memo["expire_le"]:
        _version_memo["valeur"] = ReferenceLwin.objects.count()
        _version_memo["expire_le"] = maintenant + _VERSION_TTL
    return _version_memo["valeur"]


def vider_cache_referentiel() -> None:
    """Force le rechargement du référentiel indexé (après un import LWIN)."""
    _version_memo["expire_le"] = 0.0
    _cache["version"] = None


def _referentiel() -> dict:
    """Référentiel LWIN indexé, prêt pour la correspondance.

    Renvoie le cache ``{refs, idf, postings, vocab}`` :

    - ``refs`` : liste (id, tokens requis, tokens bonus, tokens ancres, chaîne).
      * **requis** : tokens du nom du vin s'il existe, sinon du producteur —
        « La Tâche » doit correspondre sans exiger « Domaine de la
        Romanée-Conti » dans l'entrée. Tous doivent être retrouvés.
      * **bonus** : tokens de la sous-région. Sur le dump réel, l'identité des
        vins de Bourgogne est portée par la sous-région (climat) avec un nom
        de vin vide — jamais requis (la commune d'un Bordeaux ne doit pas
        suffire), seulement comptés quand ils sont retrouvés.
      * **ancres** : tokens du producteur, exigés (au moins un) quand le nom
        du vin n'est fait que de mots génériques — « Riesling » seul
        n'identifie personne, « Trimbach Riesling » si.
    - ``idf`` pondère chaque token par sa rareté dans le corpus — « palmer »
      (rare) pèse plus que « margaux » (nom de commune omniprésent), ce qui
      départage les étiquettes qui mentionnent plusieurs noms.
    - ``postings`` (index inversé token -> indices de refs) et ``vocab``
      (tokens uniques) : la correspondance ne balaye plus les ~200 000
      références, elle ne considère que les candidates partageant un token
      avec l'entrée.
    """
    import math
    import sys

    from ..models import ReferenceLwin

    version = _version_referentiel()
    if _cache.get("version") != version:
        refs: list = []
        df: dict[str, int] = {}
        postings: dict[str, list[int]] = {}
        lignes = ReferenceLwin.objects.values_list("pk", "producteur", "vin", "sous_region")
        for pk, producteur, vin, sous_region in lignes:
            requis = _tokens(vin) or _tokens(producteur)
            if not requis:
                continue
            ancres: set[str] = set()
            if requis <= _GENERIQUES:
                ancres = _tokens(producteur) - requis
                if not ancres:
                    # Nom entièrement générique (« Riesling ») ET producteur sans
                    # token significatif (« te Pa ») : référence inidentifiable,
                    # elle ne doit jamais correspondre.
                    continue
            # Un token déjà compté en ancre ne compte pas aussi en bonus :
            # « Santa Barbara Winery » (Santa Barbara) pesait double.
            bonus = _tokens(sous_region) - requis - ancres
            index = len(refs)
            # Tokens internés + tuples : le dump réel fait ~200 000 lignes, on
            # partage les chaînes récurrentes (« margaux », « pinot »…) en mémoire.
            refs.append((
                pk,
                tuple(sys.intern(t) for t in requis),
                tuple(sys.intern(t) for t in bonus),
                tuple(sys.intern(t) for t in ancres),
                # Chaîne normalisée (traits d'union -> espaces) : le ratio doit
                # voir « lynch bages » dans « Château Lynch-Bages ».
                _normaliser(f"{producteur} {vin} {sous_region}"),
                # Longueur du nom de cuvée : départage les égalités parfaites
                # en faveur du grand vin (« Yquem » avant « Yquem - Y »).
                len(_normaliser(vin)),
            ))
            tous = requis | bonus | ancres
            for t in tous:
                df[t] = df.get(t, 0) + 1
                postings.setdefault(sys.intern(t), []).append(index)
        n = max(len(refs), 1)
        _cache["version"] = version
        _cache["refs"] = refs
        _cache["idf"] = {t: math.log(n / d) + 1 for t, d in df.items()}
        _cache["postings"] = postings
        _cache["vocab"] = list(postings)
        # Vocabulaire trié pour la recherche par préfixe (bisect).
        _cache["vocab_trie"] = sorted(postings)
    return _cache


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
    # Rien ne transite par le réseau ici : on lit la photo d'origine, dont la
    # pleine résolution fait tout l'intérêt de la phase de recadrage (§ _ocr).
    image_pleine_resolution = True

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        # Repli local, désactivable depuis le panneau d'admin.
        return source_activee("lwin", settings.LWIN_ENABLED)

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

        Pipeline en trois phases (inspiré de WineNot : localiser l'étiquette
        avant de la lire), chacune n'étant payée que si la précédente n'a pas
        livré assez de tokens significatifs :

        1. **Photo entière** — chaque variante (pré-traitée, et inversée si
           l'étiquette est sombre) est lue avec deux modes de segmentation :
           mise en page automatique (PSM 3) et texte épars (PSM 11).
        2. **Orientation (OSD)** — photo tournée sans EXIF (téléchargement,
           capture d'écran) : détection --psm 0, puis re-OCR sur l'image
           redressée.
        3. **Recadrage sur l'étiquette** — bouteille loin dans le cadre : les
           boîtes de mots des passes précédentes localisent la zone de texte,
           recadrée en pleine résolution puis re-OCRisée (le texte minuscule
           redevient lisible).

        Les sorties sont concaténées : la correspondance en aval n'exige que
        la présence des tokens du référentiel, le surplus ne coûte rien."""
        import time

        # TESSERACT_TIMEOUT est le budget TOTAL de l'OCR : les passes se
        # partagent le temps restant, une photo pathologique (le mode texte
        # épars peut s'enliser sur du bruit) ne bloque jamais plus longtemps.
        echeance = time.monotonic() + settings.TESSERACT_TIMEOUT
        morceaux: list[str] = []
        tsvs: list[str] = []

        def restant() -> float:
            return echeance - time.monotonic()

        def assez() -> bool:
            return len(_tokens(" ".join(morceaux))) >= _OCR_TOKENS_SUFFISANTS

        def passes(images: list[bytes]) -> bool:
            """Passes PSM sur des variantes ; True si assez de tokens ou budget épuisé."""
            for image in images:
                for psm in _PSM_PASSES:
                    if restant() <= 0:
                        return True
                    tsv = self._tesseract(image, psm, timeout=restant())
                    if tsv:
                        tsvs.append(tsv)
                        texte = _texte_tsv(tsv)
                        if texte:
                            morceaux.append(texte)
                    if assez():
                        return True  # inutile de payer les passes suivantes
            return False

        # --- Phase 1 : photo entière ---
        variantes = _variantes(data)
        if passes(variantes) or restant() <= 0:
            return "\n".join(morceaux)

        # --- Phase 2 : orientation (photos tournées sans EXIF) ---
        rotation = self._osd_rotation(variantes[0], timeout=restant())
        if rotation and restant() > 0:
            # Les boîtes des passes non redressées ne valent plus rien — mais
            # le TEXTE reste : l'OSD se trompe parfois (reflet inversé sous la
            # bouteille des photos catalogue) et jeter la lecture initiale
            # transformerait un vin identifié en miss. Le surplus est inoffensif,
            # la correspondance n'exige que la présence des tokens de référence.
            tsvs.clear()
            if passes(_variantes(data, rotation=rotation)) or restant() <= 0:
                return "\n".join(morceaux)

        # --- Phase 3 : recadrage sur la zone de texte (étiquette petite) ---
        # Même une étiquette pivotée est localisée : Tesseract lit du charabia
        # (« OVTTINVd » pour « PAUILLAC ») mais les boîtes de mots y sont.
        zone = _zone_texte(tsvs)
        if zone and restant() > 0:
            recadrees = _variantes(data, rotation=rotation, zone=zone)
            avant = len(morceaux)
            if passes(recadrees) or restant() <= 0:
                return "\n".join(morceaux)
            if not rotation and len(morceaux) == avant:
                # Le recadrage n'a rien lu de propre : étiquette probablement
                # pivotée mais trop petite/floue pour l'OSD — orientation en
                # force brute, le recadrage pivoté à 90°/270° se lit.
                for degres in (90, 270):
                    if restant() <= 0 or passes([_pivoter(recadrees[0], degres)]):
                        break
        return "\n".join(morceaux)

    def _osd_rotation(self, image: bytes, timeout: float | None = None) -> int:
        """Rotation corrective (0/90/180/270, sens horaire) détectée par
        Tesseract OSD (--psm 0). Tout échec (pack osd absent, image trop
        pauvre, timeout) vaut 0 : on ne redresse pas."""
        commande = [settings.TESSERACT_CMD, "stdin", "stdout", "--dpi", "300", "--psm", "0"]
        try:
            resultat = subprocess.run(
                commande,
                input=image,
                capture_output=True,
                timeout=timeout if timeout is not None else settings.TESSERACT_TIMEOUT,
            )
            if resultat.returncode != 0:
                return 0
            m = re.search(r"Rotate:\s*(\d+)", resultat.stdout.decode("utf-8", errors="replace"))
            return int(m.group(1)) % 360 if m else 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return 0

    def _tesseract(self, image: bytes, psm: int, timeout: float | None = None) -> str:
        """Une passe tesseract (stdin -> TSV) ; renvoie la sortie TSV brute.

        Le TSV donne une confiance 0-100 et une boîte par mot : le texte en
        est extrait par ``_texte_tsv`` (mots douteux écartés) et la zone
        d'étiquette par ``_zone_texte``. ``--dpi 300`` lève l'avertissement
        des images sans métadonnées (photos recadrées)."""
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
            return resultat.stdout.decode("utf-8", errors="replace")
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
        classement = _classement(texte, ocr=ocr)
        if not classement:
            return None
        meilleur = classement[0]

        from ..models import ReferenceLwin

        ref = ReferenceLwin.objects.get(pk=-meilleur[-1])
        # Candidats plausibles suivants, proposés en suggestions à l'utilisateur
        # (même rôle que les suggestions wineapi dans la réponse d'identification).
        autres_pks = [-c[-1] for c in classement[1:4]]
        autres = ReferenceLwin.objects.in_bulk(autres_pks)
        suggestions: list[str] = []
        for pk in autres_pks:
            libelle = _libelle(autres[pk]) if pk in autres else ""
            if libelle and libelle != _libelle(ref) and libelle not in suggestions:
                suggestions.append(libelle)
        return self._to_normalized(
            ref,
            confiance=meilleur[3] / 100,
            millesime=parse_vintage(texte),
            suggestions=suggestions,
        )

    def _to_normalized(
        self, ref, confiance: float, millesime: int | None, suggestions: list[str] | None = None
    ) -> NormalizedWine:
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
            # Confiance = score de correspondance floue : un match faible ne doit
            # pas primer sur une autre source lors de la consolidation.
            confiance=round(confiance, 2),
            raw={
                "confidence": round(confiance, 2),
                "auto_added": False,
                "pending": False,
                "suggestions": suggestions or [],
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
