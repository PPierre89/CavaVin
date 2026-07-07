from __future__ import annotations

from django.db import transaction

from .enrichment import NormalizedWine
from .models import Cepage, Cuvee, Domaine


@transaction.atomic
def upsert_cuvee(wine: NormalizedWine) -> tuple[Cuvee, bool]:
    """
    Enregistre (ou retrouve) une cuvée à partir d'un vin normalisé — c'est le
    cache local partagé par le scan code-barres (US 01) et l'identification texte
    (US 04). Retourne (cuvee, created).

    Clé de déduplication, par ordre de priorité :
      1. code-barres  (chemin US 01)
      2. référence externe wineapi  (chemin US 04)
      3. (domaine, nom)  (dernier recours)
    """
    couleurs_valides = dict(Cuvee.Couleur.choices)
    couleur = wine.couleur if wine.couleur in couleurs_valides else Cuvee.Couleur.AUTRE

    domaine, _ = Domaine.objects.get_or_create(nom=wine.domaine_nom, region="")

    if wine.code_barres:
        lookup = {"code_barres": wine.code_barres}
    elif wine.reference_externe_id:
        lookup = {"reference_externe_id": wine.reference_externe_id}
    else:
        lookup = {"domaine": domaine, "nom": wine.cuvee_nom}

    cuvee, created = Cuvee.objects.get_or_create(
        defaults={
            "domaine": domaine,
            "nom": wine.cuvee_nom,
            "couleur": couleur,
            "appellation": wine.appellation,
            "code_barres": wine.code_barres,
            "reference_externe_id": wine.reference_externe_id,
        },
        **lookup,
    )

    if created and wine.cepages:
        cepages = [Cepage.objects.get_or_create(nom=nom)[0] for nom in wine.cepages]
        cuvee.cepages.set(cepages)

    return cuvee, created
