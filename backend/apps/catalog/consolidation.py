"""Consolidation du référentiel : arbitrage des observations en fiche de vérité.

Phase 2 de la revue d'architecture (cf. docs/architecture-referentiel.md). Les
canaux déposent des ``SourceObservation`` brutes (Phase 1) ; ce module en dérive
la **projection consolidée** de la cuvée — la valeur qui fait autorité pour chaque
champ — accompagnée d'une **carte de provenance** (quel canal, quand, avec quelle
confiance). L'arbitrage est explicite et testable, là où il était auparavant
implicite (dernier canal de la cascade à écrire).

Politiques d'arbitrage, par nature de champ :

- **profil** (région, corps, description…) : la source la plus **fiable** prime
  (confiance a priori du canal), la plus récente en cas d'égalité. Un profil de
  vin bouge peu ; on préfère une source d'identité solide (LWIN, code-barres) à
  un relevé récent mais faible.
- **marché** (prix, notes communautaires, avis…) : le relevé le plus **récent**
  prime, quel que soit le canal — un prix d'aujourd'hui vaut mieux qu'un prix de
  l'an dernier —, la confiance ne départageant que les ex æquo.

Un champ n'est écrit que si au moins une observation l'affirme (valeur non vide) :
la consolidation ne *supprime* jamais une valeur héritée qu'aucune source ne
contredit.
"""

from __future__ import annotations

from .models import Cuvee, SourceObservation

# Champs de profil (stables) : arbitrés à la confiance, puis à la récence.
# `lwin_code` est volontairement absent : c'est une identité canonique gérée par
# ``ingest`` (clé de déduplication, contrainte unique), pas un champ arbitré.
#
# `couleur` et `appellation` en font partie bien qu'ils tiennent de l'identité :
# la revue (§5) range l'identité sous la même politique « confiance d'abord ».
# Ils étaient auparavant écrits **une seule fois, à la création** par
# ``ingest.upsert_cuvee``, donc jamais corrigés : un vin créé sans appellation
# par un canal qui l'ignore la gardait vide même après le relevé d'un canal qui
# la connaît, et une couleur posée à `AUTRE` faute de mieux restait `AUTRE`.
_CHAMPS_PROFIL = (
    "region", "pays", "classification", "description", "elaborate", "corps",
    "acidite", "degre_alcool", "image_url", "appellation", "couleur",
)
# Champs de marché (volatils) : arbitrés à la récence, puis à la confiance.
_CHAMPS_MARCHE = (
    "note_moyenne", "nb_notes", "prix_min", "prix_max", "devise",
    "accords", "scores", "prix_marchands",
)


# Seules ces valeurs comptent comme une couleur *affirmée* (cf. _valeur_affirmee).
_COULEURS_AFFIRMABLES = frozenset(Cuvee.Couleur.values) - {Cuvee.Couleur.AUTRE}


def _valeur_affirmee(obs: SourceObservation, champ: str):
    """Valeur du champ affirmée par l'observation, ou ``None`` si vide/absente.

    ``couleur`` obéit à une règle propre, à deux motifs :

    1. ``AUTRE`` est une **absence**, pas une affirmation. C'est le fourre-tout
       de ``Cuvee.Couleur``, attribué dès qu'on ne sait pas : type inconnu de
       wineapi (``couleur_from_type``), aucun mot-clé reconnu (``guess_couleur``),
       vin de dessert ou porto de X-Wines, défaut d'une ``ReferenceLwin``. Le
       traiter comme une valeur laisserait un canal très sûr *sur l'identité* —
       LWIN en tête — effacer le rouge affirmé par un canal qui, lui, a lu
       l'étiquette : l'écrasement d'une bonne valeur par une moins bonne que la
       revue reproche à l'arbitrage implicite (D2).
    2. Une observation conserve la couleur **telle que le canal l'a affirmée**,
       sans passer par le garde-fou de ``ingest.upsert_cuvee`` (qui, lui, replie
       une valeur hors nomenclature sur ``AUTRE``). Projeter l'observation sans
       revalider réintroduirait donc en base une couleur inexistante : Django ne
       vérifie pas ``choices`` à l'enregistrement.
    """
    valeur = (obs.champs or {}).get(champ)
    if champ == "couleur":
        return valeur if valeur in _COULEURS_AFFIRMABLES else None
    return valeur if valeur not in (None, "", []) else None


def _clef_profil(obs: SourceObservation):
    """Priorité profil : confiance d'abord, récence puis pk pour départager."""
    return (obs.confiance, obs.releve_le, obs.pk)


def _est_scraping(obs: SourceObservation) -> bool:
    """Le relevé vient-il d'un canal de scraping (cf. ingest._confiance_pour) ?"""
    return (obs.canal or "").startswith("scrape")


def _clef_marche(obs: SourceObservation):
    """Priorité marché : sources légitimes d'abord, puis récence, puis confiance.

    La règle « le plus récent gagne » suppose des provenances **comparables** :
    entre deux canaux interrogés en direct, un prix d'aujourd'hui vaut mieux qu'un
    prix de l'an dernier. Elle ne tient plus face à un import de scraping, qui
    daterait tous ses relevés du jour de l'import et raflerait donc prix et notes
    à un canal légitime — la confiance basse du canal n'y changerait rien,
    puisqu'elle n'arbitre ici qu'à égalité de date.

    Le scraping forme donc un **étage inférieur** : il n'alimente prix et notes
    que là où aucune source légitime ne s'est exprimée. À l'intérieur de chaque
    étage, la récence reprend ses droits."""
    return (not _est_scraping(obs), obs.releve_le, obs.confiance, obs.pk)


def _arbitrer(cuvee: Cuvee, provenance: dict, observations, champ: str, clef) -> None:
    """Retient la meilleure observation pour ``champ`` et l'applique à la cuvée."""
    candidats = [o for o in observations if _valeur_affirmee(o, champ) is not None]
    if not candidats:
        return
    gagnant = max(candidats, key=clef)
    setattr(cuvee, champ, _valeur_affirmee(gagnant, champ))
    provenance[champ] = {
        "canal": gagnant.canal,
        "date": gagnant.releve_le.isoformat() if gagnant.releve_le else None,
        "confiance": float(gagnant.confiance),
    }


def consolider(cuvee: Cuvee) -> Cuvee:
    """(Re)calcule la fiche consolidée de la cuvée depuis ses observations.

    Idempotent et rejouable : relit toutes les ``SourceObservation`` de la cuvée,
    arbitre chaque champ selon sa politique et met à jour la projection + la carte
    de provenance. Sans observation, la cuvée est renvoyée inchangée.
    """
    # ``payload_brut`` (réponse complète du canal) n'entre pas dans l'arbitrage :
    # on l'écarte du SELECT. Les observations sont append-only et chaque
    # identification les relit toutes — inutile de charger des payloads wineapi
    # entiers pour ne lire que ``champs``.
    observations = list(cuvee.observations.defer("payload_brut"))
    if not observations:
        return cuvee

    provenance = dict(cuvee.provenance or {})
    for champ in _CHAMPS_PROFIL:
        _arbitrer(cuvee, provenance, observations, champ, _clef_profil)
    for champ in _CHAMPS_MARCHE:
        _arbitrer(cuvee, provenance, observations, champ, _clef_marche)

    cuvee.provenance = provenance
    cuvee.save()
    return cuvee
