"""Appariement du catalogue avec le référentiel LWIN (réconciliation hors ligne).

Deux grandes tables coexistaient sans jamais se parler : `ReferenceLwin`
(~200 000 identités Liv-ex, qui portent la **sous-région** — donc l'appellation —
et la classification) et `Cuvee` (le catalogue, garni en masse par
`import_xwines`, qui porte cépages, accords et profil œnologique). Chacune a ce
qui manque à l'autre. Ce module les relie, hors ligne et sans quota : il pose le
`lwin_code` sur les cuvées qui n'en ont pas et en tire l'appellation et la
classification (cf. `docs/architecture-referentiel.md`, D4 / Phase 3).

Le gain n'est pas que documentaire : `RechercheVinsView` (l'autocomplétion de
l'écran d'ajout) cherche dans **LWIN** et enrichit ses suggestions en joignant
`Cuvee.lwin_code`. Sans appariement, les vins importés en masse restent
invisibles de ce flux ; avec, chaque suggestion remonte cépages, note et accords.

**Pourquoi ne pas réutiliser `enrichment.lwin._classement`.** Ce moteur-là est
taillé pour une entrée *non structurée* — une sortie d'OCR ou une saisie libre —
qu'il faut rapprocher d'un référentiel entier. Ici les deux côtés sont
**structurés** : un producteur d'un côté, un producteur de l'autre. Comparer
producteur à producteur puis vin à vin est à la fois plus précis (on ne risque
pas d'accrocher un autre vin du même domaine sur la seule foi du nom du domaine)
et bien plus rapide (l'espace de recherche tombe à la poignée de références du
producteur).

Orientation **précision d'abord**, comme le provider LWIN : le catalogue est
mutualisé, un mauvais appariement se propage à tous les utilisateurs. Un doute
est un simple silence — la cuvée reste telle quelle, ce qui est sans gravité.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz, process

from .enrichment.normalize import normaliser_nom
from .ingest import enregistrer_observation
from .consolidation import consolider
from .models import Cuvee, ReferenceLwin

# Canal des observations déposées : la donnée vient bien de LWIN, seul le chemin
# d'entrée diffère (réconciliation en masse plutôt qu'identification unitaire).
CANAL = "lwin"

# Similarité minimale entre le nom de la cuvée et le nom du vin LWIN pour
# accepter un appariement (0 à 1). Calibré haut : au-delà du seuil on écrit dans
# le catalogue partagé, en deçà on ne fait rien.
SEUIL_DEFAUT = 0.86

# Similarité minimale (0-100) pour rapprocher deux noms de producteurs quand la
# forme normalisée ne coïncide pas exactement (« Chateau Margaux SA » / « Chateau
# Margaux »). Se tromper de producteur, c'est se tromper sur toute la grappe de
# vins qu'il porte — mais la garde est double : le nom du *vin* doit ensuite
# franchir `SEUIL_DEFAUT` de son côté.
#
# Mesuré sur des paires réalistes : les variantes légitimes d'un même producteur
# (suffixe juridique, « & Fils ») marquent 86,5 à 90,9, tandis que deux
# producteurs distincts marquent 66,7 à 86,7 — les deux plages **se recouvrent**
# (« Domaine Leflaive » / « … Fils » à 86,5 contre « Chateau Montrose » /
# « Chateau Montus » à 86,7). Aucun seuil ne les sépare proprement : 90 est le
# meilleur compromis disponible (il écarte tous les faux positifs mesurés, au
# prix de quelques silences), fidèle au principe « précision d'abord ». C'est
# aussi pourquoi il est réglable : la graphie des domaines dépend du dump, et
# `--simuler` permet de le calibrer avant d'écrire quoi que ce soit.
SEUIL_PRODUCTEUR = 90

# Écart minimal entre le meilleur candidat et le suivant *décrivant un autre vin*.
# En deçà, on préfère le silence : le dump contient des cuvées voisines d'un même
# domaine (« Riesling » / « Riesling Réserve ») qu'un score seul ne départage pas.
ECART_AMBIGUITE = 0.05


@dataclass
class ResultatAppariement:
    """Compte rendu d'une passe d'appariement."""

    examinees: int = 0
    appariees: int = 0
    silences: int = 0  # aucun candidat au-dessus du seuil (sans gravité)
    ambigus: int = 0  # plusieurs vins plausibles : on s'abstient
    revendiques: int = 0  # code LWIN déjà porté par une autre cuvée

    def __str__(self) -> str:  # pragma: no cover - confort d'affichage
        return (
            f"{self.examinees} cuvées examinées — {self.appariees} appariées, "
            f"{self.silences} sans correspondance, {self.ambigus} ambiguës, "
            f"{self.revendiques} codes déjà pris"
        )


def _couleurs_incompatibles(couleur_cuvee: str, couleur_ref: str) -> bool:
    """Deux couleurs *connues* et différentes contredisent l'appariement.

    `AUTRE` n'est pas une couleur mais une absence d'information (c'est le cas de
    tous les vins de dessert et portos importés depuis X-Wines) : elle ne
    contredit rien."""
    if not couleur_cuvee or not couleur_ref:
        return False
    if "AUTRE" in (couleur_cuvee, couleur_ref):
        return False
    return couleur_cuvee != couleur_ref


def choisir_reference(
    nom_cuvee: str,
    couleur_cuvee: str,
    references: list[dict],
    seuil: float = SEUIL_DEFAUT,
) -> tuple[dict | None, float, str]:
    """Meilleure référence LWIN pour une cuvée, parmi celles d'un producteur.

    Fonction *pure* : ``references`` est une liste de dicts
    ``{lwin, vin, sous_region, region, classification, couleur}``. Retourne
    ``(reference | None, score, motif)`` où ``motif`` vaut ``"apparie"``,
    ``"silence"`` ou ``"ambigu"``. Le score (0 à 1) devient la confiance du
    relevé déposé : un appariement tout juste au-dessus du seuil ne doit pas
    primer, à la consolidation, sur un canal mieux assuré.

    Le score compare les noms de vin par ``token_sort_ratio`` — sensible aux
    tokens **non expliqués des deux côtés**, contrairement à
    ``token_set_ratio`` : « Origem Merlot » ne doit pas s'apparier à la référence
    « Origem » du même domaine sous prétexte que tous les tokens de celle-ci sont
    retrouvés.
    """
    cible = normaliser_nom(nom_cuvee)
    if not cible:
        return None, 0.0, "silence"

    notes: list[tuple[float, dict]] = []
    for ref in references:
        if _couleurs_incompatibles(couleur_cuvee, ref.get("couleur", "")):
            continue
        vin = normaliser_nom(ref.get("vin", ""))
        if not vin:
            # Référence au niveau du producteur (pas de nom de vin) : elle
            # n'identifie une cuvée que si celle-ci porte le nom du domaine.
            vin = normaliser_nom(ref.get("producteur", ""))
            if vin != cible:
                continue
        notes.append((fuzz.token_sort_ratio(cible, vin) / 100, ref))

    if not notes:
        return None, 0.0, "silence"
    notes.sort(key=lambda n: (-n[0], n[1]["lwin"]))  # départage stable
    meilleur_score, meilleure = notes[0]
    if meilleur_score < seuil:
        return None, meilleur_score, "silence"

    # Le dump contient le même vin sous plusieurs codes LWIN : deux références
    # qui décrivent le même vin ne sont pas une ambiguïté, elles se confirment.
    def identite(ref: dict) -> tuple[str, str]:
        return (normaliser_nom(ref.get("vin", "")), ref.get("sous_region", ""))

    reference_identite = identite(meilleure)
    for score, ref in notes[1:]:
        if identite(ref) == reference_identite:
            continue
        if meilleur_score - score < ECART_AMBIGUITE:
            return None, meilleur_score, "ambigu"
        break  # la liste est triée : les suivants sont encore plus loin.
    return meilleure, meilleur_score, "apparie"


# Garde-fou de l'appariement flou de producteur : au-delà, on cesse d'élargir la
# liste de candidats (un token trop courant n'apporte aucune discrimination).
_MAX_CANDIDATS = 400


def _index_producteurs() -> tuple[dict[str, list[dict]], dict[str, list[str]]]:
    """Référentiel LWIN indexé par forme normalisée du nom de producteur.

    Chargé en une passe, il rend deux structures : l'index producteur -> ses
    références, et un **index inversé** token -> producteurs. Le second est ce
    qui rend l'appariement flou praticable : comparer chaque producteur du
    catalogue aux ~50 000 producteurs du dump coûte ~16 ms pièce (≈ 27 min pour
    100 000 cuvées, mesuré), alors que ne comparer qu'aux producteurs partageant
    un token ramène la liste à quelques dizaines — et permet surtout de conclure
    au silence *sans aucune comparaison* quand aucun token n'est connu du dump,
    ce qui est le cas le plus fréquent.
    """
    index: dict[str, list[dict]] = {}
    postings: dict[str, list[str]] = {}
    champs = ("lwin", "producteur", "vin", "region", "sous_region", "classification", "couleur")
    for ligne in ReferenceLwin.objects.values(*champs).iterator(chunk_size=5000):
        cle = normaliser_nom(ligne["producteur"])
        if not cle:
            continue
        if cle not in index:
            index[cle] = []
            for token in set(cle.split()):
                postings.setdefault(token, []).append(cle)
        index[cle].append(ligne)
    return index, postings


def _producteur_proche(
    cle: str, index: dict, postings: dict, seuil: int = SEUIL_PRODUCTEUR
) -> str | None:
    """Clé de producteur du référentiel la plus proche, ou None sous le seuil.

    Le repli flou ne considère que les producteurs partageant au moins un token
    avec l'entrée. Un producteur dont *aucun* mot n'existe dans le dump n'est
    donc jamais rapproché : c'est voulu, l'appariement serait de toute façon trop
    incertain pour écrire dans un catalogue mutualisé.
    """
    if cle in index:
        return cle
    presents = [t for t in set(cle.split()) if t in postings]
    if not presents:
        return None
    # Les tokens les plus rares cernent le mieux les candidats ; deux suffisent.
    presents.sort(key=lambda t: len(postings[t]))
    candidats: set[str] = set()
    for token in presents[:2]:
        candidats.update(postings[token])
        if len(candidats) >= _MAX_CANDIDATS:
            break
    trouve = process.extractOne(
        cle, list(candidats), scorer=fuzz.token_sort_ratio, score_cutoff=seuil
    )
    return trouve[0] if trouve else None


def _par_lots(pks: list[int], taille: int = 500):
    """Itère les cuvées par paquets d'identifiants, avec leur domaine."""
    for debut in range(0, len(pks), taille):
        yield from (
            Cuvee.objects.filter(pk__in=pks[debut : debut + taille])
            .select_related("domaine")
            .order_by("pk")
        )


def _observer(cuvee: Cuvee, ref: dict, score: float) -> None:
    """Dépose le relevé LWIN de l'appariement, puis re-consolide la fiche.

    On n'affirme que ce que LWIN sait **mieux** que le canal qui a garni la
    cuvée : l'appellation (sa `sous_region`), la classification et la couleur.
    En particulier on n'affirme **pas** `region` — LWIN la donne large
    (« Bordeaux ») là où l'import en masse la donne souvent plus fine, et une
    confiance LWIN supérieure écraserait la meilleure valeur par la moins bonne.

    Ces champs passent par l'observation plutôt que par une écriture directe :
    `appellation` et `couleur` sont désormais consolidés
    (`consolidation._CHAMPS_PROFIL`), donc `consolider` ci-dessous ré-arbitrerait
    de toute façon toute valeur posée à la main juste avant — et la provenance
    resterait muette sur son origine.
    """
    enregistrer_observation(
        cuvee,
        canal=CANAL,
        payload_brut={"lwin": ref, "appariement": {"score": score}},
        champs={
            "classification": ref.get("classification", ""),
            # `sous_region` porte l'AOC (« Margaux ») ; à défaut, la région reste
            # la meilleure approximation disponible.
            "appellation": ref.get("sous_region") or ref.get("region") or "",
            # `AUTRE` sera ignoré par la consolidation (absence, pas affirmation).
            "couleur": ref.get("couleur", ""),
        },
        confiance=score,
    )
    consolider(cuvee)


def apparier_lwin(
    *,
    seuil: float = SEUIL_DEFAUT,
    seuil_producteur: int = SEUIL_PRODUCTEUR,
    limite: int | None = None,
    simuler: bool = False,
    progression=None,
) -> ResultatAppariement:
    """Apparie les cuvées dépourvues de `lwin_code` au référentiel LWIN.

    Idempotent et reprenable : une cuvée qui porte déjà un `lwin_code` n'est pas
    ré-examinée, donc une seconde passe ne coûte que la requête de sélection.
    ``simuler=True`` n'écrit rien (permet d'inspecter le taux d'appariement avant
    de toucher au catalogue partagé).
    """
    resultat = ResultatAppariement()
    index, postings = _index_producteurs()
    if not index:
        return resultat  # référentiel LWIN non importé : rien à apparier.

    # Les identifiants sont matérialisés **avant** la boucle : celle-ci écrit
    # `lwin_code`, c'est-à-dire le champ même que filtre la requête. Itérer le
    # queryset en le modifiant ferait sortir des lignes du jeu de résultats en
    # cours de route, et une partie du catalogue ne serait jamais examinée.
    pks = list(
        Cuvee.objects.filter(lwin_code="").order_by("pk").values_list("pk", flat=True)
    )
    if limite is not None:
        pks = pks[:limite]

    for cuvee in _par_lots(pks):
        resultat.examinees += 1
        cle = _producteur_proche(
            normaliser_nom(cuvee.domaine.nom), index, postings, seuil_producteur
        )
        if cle is None:
            resultat.silences += 1
        else:
            ref, score, motif = choisir_reference(
                cuvee.nom, cuvee.couleur, index[cle], seuil=seuil
            )
            if motif == "ambigu":
                resultat.ambigus += 1
            elif ref is None:
                resultat.silences += 1
            elif Cuvee.objects.filter(lwin_code=ref["lwin"]).exclude(pk=cuvee.pk).exists():
                # Le code LWIN est une identité contrainte unique : le premier
                # arrivé la garde (même règle que `ingest._completer_identites`).
                resultat.revendiques += 1
            else:
                resultat.appariees += 1
                if not simuler:
                    _appliquer(cuvee, ref, score)
        if progression is not None and resultat.examinees % 500 == 0:
            progression(resultat)

    if progression is not None:
        progression(resultat)
    return resultat


def _appliquer(cuvee: Cuvee, ref: dict, score: float) -> None:
    """Pose l'identité LWIN sur la cuvée, puis dépose le relevé.

    Seul `lwin_code` est écrit ici : c'est une **identité** canonique (contrainte
    unique, clé de déduplication), pas un champ arbitrable — même traitement que
    dans `ingest.upsert_cuvee`. Tout le reste de l'apport LWIN (appellation,
    classification, couleur) passe par l'observation et la consolidation."""
    cuvee.lwin_code = ref["lwin"]
    cuvee.save(update_fields=["lwin_code"])
    _observer(cuvee, ref, score)
