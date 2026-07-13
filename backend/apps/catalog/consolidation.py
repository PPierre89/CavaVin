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
_CHAMPS_PROFIL = (
    "region", "pays", "classification", "description", "elaborate", "corps",
    "acidite", "degre_alcool", "image_url",
)
# Champs de marché (volatils) : arbitrés à la récence, puis à la confiance.
_CHAMPS_MARCHE = (
    "note_moyenne", "nb_notes", "prix_min", "prix_max", "devise",
    "accords", "scores", "prix_marchands",
)


def _valeur_affirmee(obs: SourceObservation, champ: str):
    """Valeur du champ affirmée par l'observation, ou ``None`` si vide/absente."""
    valeur = (obs.champs or {}).get(champ)
    return valeur if valeur not in (None, "", []) else None


def _clef_profil(obs: SourceObservation):
    """Priorité profil : confiance d'abord, récence puis pk pour départager."""
    return (obs.confiance, obs.releve_le, obs.pk)


def _clef_marche(obs: SourceObservation):
    """Priorité marché : récence d'abord, confiance puis pk pour départager."""
    return (obs.releve_le, obs.confiance, obs.pk)


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
    observations = list(cuvee.observations.all())
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
