"""Mesure de la qualité de reconnaissance d'un vin.

Sans ce module, une modification de l'OCR ou de la correspondance floue ne peut
être jugée que « ça marche encore sur les tests unitaires » — or ceux-ci
travaillent sur des entrées mockées ou minuscules. Impossible d'y lire si un
réglage **améliore ou dégrade** le taux de reconnaissance réel, donc impossible
de toucher aux seuils sans avancer à l'aveugle.

Trois issues possibles pour un cas, et c'est leur distinction qui fait tout
l'intérêt de la mesure :

- **trouvé** — le vin attendu est reconnu ;
- **silence** — aucune correspondance (l'utilisateur saisit à la main : ennuyeux,
  mais sans conséquence) ;
- **erreur** — un *autre* vin est renvoyé. C'est l'issue coûteuse : elle
  contredit le parti pris « précision d'abord » du provider LWIN et pollue le
  catalogue mutualisé, que tous les utilisateurs partagent. Un réglage qui
  convertit du silence en reconnaissance est bon ; le même réglage qui convertit
  du silence en erreur est mauvais, et un simple « taux de réussite » ne les
  distingue pas.

Deux corpus complémentaires :

- **photos réelles** (``corpus`` JSON) — mesure la chaîne complète, OCR compris.
  Seule façon de juger le pré-traitement d'image, mais l'annotation est manuelle
  donc l'échantillon reste petit.
- **synthétique** — dérive des milliers de requêtes du référentiel LWIN lui-même
  en simulant le bruit d'OCR (confusions de caractères, tokens perdus, mobilier
  d'étiquette). Reproductible via sa graine, sans photo ni clé d'API, à une
  échelle qui donne du sens aux écarts de quelques points.
"""

from __future__ import annotations

import json
import random
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Confusions typiques d'un OCR sur une étiquette (police stylisée, gravure,
# faible contraste). Reproduisent les erreurs réellement observées en sortie de
# Tesseract, pas un bruit aléatoire uniforme.
_CONFUSIONS = {
    "m": "rn", "rn": "m", "l": "i", "i": "l", "o": "0", "0": "o",
    "e": "c", "c": "e", "u": "v", "v": "u", "n": "h", "h": "n",
    "a": "o", "s": "5", "t": "f", "g": "9", "b": "6",
}

# Mentions omniprésentes sur les étiquettes, sans valeur identifiante : l'OCR les
# lit et elles noient les tokens utiles. Les injecter teste la robustesse au bruit.
_MOBILIER = [
    "MIS EN BOUTEILLE AU CHATEAU", "PRODUCE OF FRANCE", "PRODUCT OF ITALY",
    "APPELLATION D ORIGINE PROTEGEE", "GRAND VIN DE BORDEAUX", "750 ML",
    "13% VOL", "CONTAINS SULFITES", "ESTATE BOTTLED", "RED WINE",
    "SULFITES ALC BY VOL", "IMPORTED BY", "VIGNOBLES ET DOMAINES",
]


@dataclass
class Cas:
    """Un cas d'évaluation : une entrée et le vin attendu."""

    identifiant: str
    # Exactement l'un des deux : une photo à lire, ou un texte déjà « lu ».
    image: Path | None = None
    texte: str = ""
    # Vérité terrain. ``lwin`` est le critère fort quand il est connu ; sinon on
    # se rabat sur la présence des tokens attendus dans le libellé renvoyé.
    lwin: str = ""
    producteur: str = ""
    vin: str = ""


@dataclass
class Resultat:
    """Issue d'un cas : ``trouve`` / ``silence`` / ``erreur``."""

    cas: Cas
    issue: str
    obtenu: str = ""


@dataclass
class Bilan:
    """Agrégat des issues, avec les taux qui pilotent une décision de réglage."""

    resultats: list[Resultat] = field(default_factory=list)

    def _compte(self, issue: str) -> int:
        return sum(1 for r in self.resultats if r.issue == issue)

    @property
    def total(self) -> int:
        return len(self.resultats)

    @property
    def trouves(self) -> int:
        return self._compte("trouve")

    @property
    def silences(self) -> int:
        return self._compte("silence")

    @property
    def erreurs(self) -> int:
        return self._compte("erreur")

    def _taux(self, n: int) -> float:
        return (100.0 * n / self.total) if self.total else 0.0

    @property
    def taux_reconnaissance(self) -> float:
        return self._taux(self.trouves)

    @property
    def taux_silence(self) -> float:
        return self._taux(self.silences)

    @property
    def taux_erreur(self) -> float:
        return self._taux(self.erreurs)

    def resume(self) -> str:
        return (
            f"{self.total} cas — "
            f"reconnus {self.trouves} ({self.taux_reconnaissance:.1f} %) · "
            f"silence {self.silences} ({self.taux_silence:.1f} %) · "
            f"erreurs {self.erreurs} ({self.taux_erreur:.1f} %)"
        )


def _normaliser(texte: str) -> str:
    """Minuscules sans accents — comparaison tolérante de la vérité terrain."""
    sans = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    return " ".join(sans.lower().split())


def juger(cas: Cas, wine) -> Resultat:
    """Confronte le relevé d'une source à la vérité terrain du cas.

    ``wine`` est un ``NormalizedWine`` ou ``None``.

    Ce qu'on mesure est « le bon **vin** a-t-il été identifié », pas « la bonne
    *ligne* a-t-elle été renvoyée ». La nuance est décisive : le dump LWIN
    contient le même vin sous plusieurs codes (``rechercher_lwin`` déduplique
    d'ailleurs les libellés pour cette raison). Juger sur la seule égalité des
    codes compterait donc en erreur une identification parfaite tombée sur un
    doublon — et donnerait un taux d'erreur massivement pessimiste, exactement
    l'inverse de ce qu'on cherche à outiller.

    On accepte donc dans l'ordre : code LWIN identique, ou identité équivalente
    (tous les tokens significatifs du producteur attendu présents dans le relevé).
    Un « Ridge » attendu n'est pas validé par un « Ridgeview » : la comparaison
    porte sur des tokens entiers, pas sur des sous-chaînes.
    """
    if wine is None:
        return Resultat(cas=cas, issue="silence")

    obtenu = f"{wine.domaine_nom} — {wine.cuvee_nom}".strip(" —")

    # 1) Code LWIN — critère fort quand les deux côtés le portent.
    attendu_lwin = (cas.lwin or "").strip()
    if attendu_lwin:
        detail = (wine.raw or {}).get("wineapi_detail") or {}
        if detail.get("lwinCode") == attendu_lwin:
            return Resultat(cas=cas, issue="trouve", obtenu=obtenu)

    # 2) Identité équivalente — rattrape les doublons du référentiel.
    tokens_obtenus = set(_normaliser(obtenu).split())
    attendus = [t for t in _normaliser(cas.producteur).split() if len(t) >= 3]
    if attendus and all(t in tokens_obtenus for t in attendus):
        return Resultat(cas=cas, issue="trouve", obtenu=obtenu)
    # Producteur sans token discriminant : on exige alors le libellé complet.
    if not attendus:
        cible = set(_normaliser(f"{cas.producteur} {cas.vin}").split())
        if cible and cible <= tokens_obtenus:
            return Resultat(cas=cas, issue="trouve", obtenu=obtenu)

    return Resultat(cas=cas, issue="erreur", obtenu=obtenu)


def charger_corpus(chemin: Path, racine_images: Path | None = None) -> tuple[list[Cas], dict]:
    """Charge un corpus de photos annotées -> ``(cas, métadonnées)``.

    Le manifeste ne porte que des URL et *nos* annotations : les images ne sont
    pas versionnées (poids, et licence propre au jeu de données amont). Elles
    sont téléchargées à la demande dans un cache local.
    """
    donnees = json.loads(Path(chemin).read_text(encoding="utf-8"))
    racine = Path(racine_images) if racine_images else Path(chemin).parent / "images"
    cas = []
    for entree in donnees.get("cas", []):
        cas.append(
            Cas(
                identifiant=str(entree.get("id") or entree.get("producteur", "?")),
                image=racine / f"{entree['id']}.jpg" if entree.get("id") else None,
                lwin=entree.get("lwin", ""),
                producteur=entree.get("producteur", ""),
                vin=entree.get("vin", ""),
            )
        )
    meta = {c: donnees.get(c, "") for c in ("nom", "licence", "attribution", "source")}
    return cas, meta


def _bruiter(mot: str, alea: random.Random, intensite: float) -> str:
    """Applique des confusions de caractères typiques d'un OCR à un mot."""
    if len(mot) < 4 or alea.random() > intensite:
        return mot
    positions = [i for i, c in enumerate(mot) if c in _CONFUSIONS]
    if not positions:
        return mot
    i = alea.choice(positions)
    return mot[:i] + _CONFUSIONS[mot[i]] + mot[i + 1:]


def generer_cas_synthetiques(
    references, nombre: int, graine: int = 0, intensite: float = 0.3
) -> list[Cas]:
    """Dérive des requêtes « comme lues par l'OCR » depuis le référentiel LWIN.

    Chaque cas part d'une référence réelle et lui applique le traitement que la
    chaîne subit sur une vraie étiquette : mobilier d'étiquette ajouté, ordre des
    mots bousculé, confusions de caractères, et parfois un token perdu (mot
    illisible). La vérité terrain est le code LWIN de départ — donc sans
    ambiguïté, contrairement à une annotation manuelle.

    ``intensite`` (0 à 1) dose le bruit : 0 = texte propre (mesure le plafond de
    la correspondance), 1 = photo très dégradée.
    """
    alea = random.Random(graine)
    references = list(references)
    if not references:
        return []
    cas: list[Cas] = []
    for i in range(nombre):
        ref = references[alea.randrange(len(references))]
        mots = f"{ref.producteur} {ref.vin}".split()
        if not mots:
            continue
        # Un mot illisible sur l'étiquette : c'est le cas qui sépare une
        # correspondance robuste d'une correspondance chanceuse.
        if len(mots) > 2 and alea.random() < intensite:
            mots.pop(alea.randrange(len(mots)))
        mots = [_bruiter(m, alea, intensite) for m in mots]
        if alea.random() < intensite:
            mots.append(alea.choice(_MOBILIER))
        if alea.random() < intensite:
            alea.shuffle(mots)
        cas.append(
            Cas(
                identifiant=f"synth-{i}-{ref.lwin}",
                texte=" ".join(mots),
                lwin=ref.lwin,
                producteur=ref.producteur,
                vin=ref.vin,
            )
        )
    return cas
