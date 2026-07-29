"""Ferme la dernière porte aux doublons du catalogue : le nom d'une cuvée.

Les identités fortes (code-barres, référence externe, code LWIN) sont contraintes
depuis les Phases 0 et 3. Mais une cuvée qui n'en porte aucune — c'est le cas de
toute identification par LLM, qui ne fournit ni code-barres ni référence
distante — retombait sur une clé ``(domaine, nom)`` **sans contrainte** et
comparée de façon exacte. « Grand Vin », « Grand vin » et « Grand Vin » (espace
final) créaient donc trois cuvées pour le même vin dans le catalogue mutualisé.

On dédoublonne l'existant sur le nom normalisé, puis on pose la contrainte —
même démarche que la migration 0008, qui avait traité les identités fortes.
"""

from collections import defaultdict

from django.db import migrations, models

from apps.catalog.enrichment.normalize import normaliser_nom

# Identités fortes : elles portent chacune une contrainte d'unicité partielle,
# on ne peut donc les recopier sur la survivante que si personne ne les détient.
_IDENTITES = ("code_barres", "reference_externe_id", "lwin_code")
# Champs de fiche que l'on récupère d'un doublon quand la survivante est vide :
# la fusion ne doit rien faire perdre.
_CHAMPS_RECUPERES = (
    "appellation", "region", "pays", "classification", "description", "elaborate",
    "corps", "acidite", "degre_alcool", "image_url", "photo_etiquette",
    "note_moyenne", "nb_notes", "prix_min", "prix_max", "devise",
)


def remplir_nom_normalise(apps, schema_editor):
    """Calcule la forme canonique du nom sur toutes les cuvées existantes."""
    Cuvee = apps.get_model("catalog", "Cuvee")
    for cuvee in Cuvee.objects.all().only("pk", "nom").iterator():
        Cuvee.objects.filter(pk=cuvee.pk).update(
            nom_normalise=normaliser_nom(cuvee.nom)[:255]
        )


def dedup_par_nom(apps, schema_editor):
    """Fusionne les cuvées d'un même producteur portant le même nom normalisé.

    La plus ancienne (plus petit pk) survit. Avant de supprimer un doublon on lui
    reprend tout ce qu'il est seul à porter : le stock privé et les notes de
    dégustation (FK en PROTECT — les perdre serait inacceptable), les
    observations de source, les cépages, ses identités fortes si elles sont
    libres, et les champs de fiche que la survivante n'a pas.
    """
    Cuvee = apps.get_model("catalog", "Cuvee")
    Bouteille = apps.get_model("inventory", "Bouteille")
    NoteDegustation = apps.get_model("inventory", "NoteDegustation")
    SourceObservation = apps.get_model("catalog", "SourceObservation")

    groupes = defaultdict(list)
    for cuvee in Cuvee.objects.exclude(nom_normalise="").order_by("pk"):
        groupes[(cuvee.domaine_id, cuvee.nom_normalise)].append(cuvee)

    for doublons in groupes.values():
        if len(doublons) < 2:
            continue
        survivante, *autres = doublons
        for doublon in autres:
            # Données privées d'abord : elles ne doivent jamais disparaître.
            Bouteille.objects.filter(cuvee=doublon).update(cuvee=survivante)
            NoteDegustation.objects.filter(cuvee=doublon).update(cuvee=survivante)
            SourceObservation.objects.filter(cuvee=doublon).update(cuvee=survivante)
            survivante.cepages.add(*doublon.cepages.all())

            # On note ce qu'il y a à reprendre, mais on ne l'écrit qu'APRÈS la
            # suppression du doublon : les identités fortes portent des contraintes
            # d'unicité, et les recopier tant que le doublon les détient encore
            # ferait échouer la migration sur « UNIQUE constraint failed ».
            a_reprendre = {}
            for champ in _IDENTITES:
                valeur = getattr(doublon, champ, "")
                if not valeur or getattr(survivante, champ, ""):
                    continue
                if Cuvee.objects.filter(**{champ: valeur}).exclude(pk=doublon.pk).exists():
                    continue  # déjà revendiquée ailleurs : on ne la vole pas
                a_reprendre[champ] = valeur
            for champ in _CHAMPS_RECUPERES:
                if not getattr(survivante, champ, None) and getattr(doublon, champ, None):
                    a_reprendre[champ] = getattr(doublon, champ)

            doublon.delete()

            if a_reprendre:
                for champ, valeur in a_reprendre.items():
                    setattr(survivante, champ, valeur)
                survivante.save()


def sans_retour(apps, schema_editor):
    """Le dédoublonnage n'est pas réversible (les doublons sont fusionnés).

    Retirer la contrainte suffit à redescendre : on ne recrée évidemment pas des
    doublons qui n'auraient plus de sens.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0015_cuvee_photo_etiquette"),
        ("inventory", "0006_remove_bouteille_statut"),
    ]

    operations = [
        migrations.AddField(
            model_name="cuvee",
            name="nom_normalise",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=255
            ),
        ),
        migrations.RunPython(remplir_nom_normalise, sans_retour),
        migrations.RunPython(dedup_par_nom, sans_retour),
        migrations.AddConstraint(
            model_name="cuvee",
            constraint=models.UniqueConstraint(
                condition=models.Q(("nom_normalise", ""), _negated=True),
                fields=("domaine", "nom_normalise"),
                name="unique_cuvee_nom_par_domaine",
            ),
        ),
    ]
