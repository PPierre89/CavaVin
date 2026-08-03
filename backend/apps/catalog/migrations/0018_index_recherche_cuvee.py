"""Index de recherche plein texte (FTS5) sur le catalogue.

L'autocomplétion de l'écran d'ajout interroge ``/api/cuvees/?search=`` à chaque
frappe. Le filtre de DRF traduit cela en ``LIKE '%terme%'`` sur cinq colonnes :
le joker initial interdit tout index, si bien que **chaque frappe balayait la
table entière**. Sur un catalogue garni par import (127 952 cuvées, 551 Mo),
c'est 180 ms sur un disque rapide au cache chaud, et plusieurs secondes sur un
NAS — l'autocomplétion paraît morte.

FTS5 ramène la même recherche à 0,1–2,7 ms, pour ~7 Mo d'index.

La synchronisation passe par des **triggers SQLite** plutôt que par des signaux
Django : les imports en masse écrivent par ``bulk_create`` et ``update()``, que
les signaux ne voient pas. Un trigger, lui, ne peut pas être contourné.
"""

from django.db import migrations

# `remove_diacritics 2` : « Château » et « chateau » doivent se rejoindre, ce qui
# est la règle du reste du catalogue (cf. Cuvee.nom_normalise).
CREER = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS catalog_cuvee_fts USING fts5(
        nom, domaine, appellation, region,
        tokenize='unicode61 remove_diacritics 2'
    );
    """,
    # Peuplement initial : le catalogue peut déjà contenir des dizaines de
    # milliers de cuvées au moment où la migration s'applique.
    """
    INSERT INTO catalog_cuvee_fts(rowid, nom, domaine, appellation, region)
    SELECT c.id, c.nom, d.nom, c.appellation, c.region
      FROM catalog_cuvee c JOIN catalog_domaine d ON d.id = c.domaine_id;
    """,
    """
    CREATE TRIGGER catalog_cuvee_fts_ai AFTER INSERT ON catalog_cuvee BEGIN
        INSERT INTO catalog_cuvee_fts(rowid, nom, domaine, appellation, region)
        SELECT new.id, new.nom, d.nom, new.appellation, new.region
          FROM catalog_domaine d WHERE d.id = new.domaine_id;
    END;
    """,
    """
    CREATE TRIGGER catalog_cuvee_fts_ad AFTER DELETE ON catalog_cuvee BEGIN
        DELETE FROM catalog_cuvee_fts WHERE rowid = old.id;
    END;
    """,
    """
    CREATE TRIGGER catalog_cuvee_fts_au AFTER UPDATE ON catalog_cuvee BEGIN
        DELETE FROM catalog_cuvee_fts WHERE rowid = old.id;
        INSERT INTO catalog_cuvee_fts(rowid, nom, domaine, appellation, region)
        SELECT new.id, new.nom, d.nom, new.appellation, new.region
          FROM catalog_domaine d WHERE d.id = new.domaine_id;
    END;
    """,
    # Le nom du producteur est dénormalisé dans l'index : le renommer doit
    # rafraîchir toutes ses cuvées, sinon la recherche par domaine se périme.
    """
    CREATE TRIGGER catalog_domaine_fts_au AFTER UPDATE OF nom ON catalog_domaine BEGIN
        DELETE FROM catalog_cuvee_fts
         WHERE rowid IN (SELECT id FROM catalog_cuvee WHERE domaine_id = new.id);
        INSERT INTO catalog_cuvee_fts(rowid, nom, domaine, appellation, region)
        SELECT c.id, c.nom, new.nom, c.appellation, c.region
          FROM catalog_cuvee c WHERE c.domaine_id = new.id;
    END;
    """,
]

SUPPRIMER = [
    "DROP TRIGGER IF EXISTS catalog_domaine_fts_au;",
    "DROP TRIGGER IF EXISTS catalog_cuvee_fts_au;",
    "DROP TRIGGER IF EXISTS catalog_cuvee_fts_ad;",
    "DROP TRIGGER IF EXISTS catalog_cuvee_fts_ai;",
    "DROP TABLE IF EXISTS catalog_cuvee_fts;",
]


class Migration(migrations.Migration):

    dependencies = [("catalog", "0017_tacheimport")]

    operations = [
        migrations.RunSQL(sql=CREER, reverse_sql=SUPPRIMER),
    ]
