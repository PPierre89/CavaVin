import json
import os
import subprocess
import tempfile
import time
import urllib.error
from datetime import date
from io import BytesIO, StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection
from django.test import (
    SimpleTestCase,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.utils import timezone
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from . import evaluation, views
from .enrichment import EnrichmentError, NormalizedWine
from .enrichment import image as image_utils
from .enrichment import normalize
from .enrichment.openfoodfacts import OpenFoodFactsProvider
from .enrichment.grapeminds import GrapeMindsProvider
from .enrichment.vinou import VinouProvider
from .enrichment.vinou import _JWT_CACHE_KEY as VINOU_JWT_KEY
from .enrichment.wineapi import WineApiProvider
from . import apogee, appariement, quotas, sommellerie, wine_profile, xwines_import
from .runtime_config import definir_source_activee, set_parametre, source_activee
from .consolidation import consolider
from .ingest import (
    enregistrer_observation,
    enrich_cuvee_from_wineapi,
    synchroniser_wineapi,
    upsert_cuvee,
)
from .models import (
    Cepage,
    Cuvee,
    Domaine,
    MillesimeReference,
    ReferenceLwin,
    SourceObservation,
)
from .serializers import ScanEtiquetteSerializer

User = get_user_model()


class NormalizeTests(SimpleTestCase):
    """Fonctions de nettoyage pures — pas de base de données."""

    def test_clean_gere_none_espaces_et_non_str(self):
        self.assertEqual(normalize.clean(None), "")
        self.assertEqual(normalize.clean("  a   b  "), "a b")
        self.assertEqual(normalize.clean(2015), "2015")
        self.assertEqual(normalize.clean({"x": 1}), "")  # type inattendu ignoré

    def test_named_accepte_str_ou_objet_name(self):
        self.assertEqual(normalize.named("Bordeaux"), "Bordeaux")
        self.assertEqual(normalize.named({"name": " Médoc "}), "Médoc")

    def test_strip_vintage_retire_le_millesime(self):
        self.assertEqual(normalize.strip_vintage("Chateau Petrus 2015"), "Chateau Petrus")
        self.assertEqual(normalize.strip_vintage("Cuvée 1998 Réserve"), "Cuvée Réserve")

    def test_couleur_from_type_mappe_et_defaut_autre(self):
        self.assertEqual(normalize.couleur_from_type("red"), "ROUGE")
        self.assertEqual(normalize.couleur_from_type("SPARKLING"), "BULLES")
        self.assertEqual(normalize.couleur_from_type("inconnu"), "AUTRE")

    def test_guess_couleur_priorite_bulles_puis_couleurs(self):
        # Les bulles priment : un champagne rosé reste des bulles.
        self.assertEqual(normalize.guess_couleur("Champagne rosé brut"), "BULLES")
        self.assertEqual(normalize.guess_couleur("Vin rosé de Provence"), "ROSE")
        self.assertEqual(normalize.guess_couleur("Red wine", "tinto"), "ROUGE")
        self.assertEqual(normalize.guess_couleur("Vin blanc sec"), "BLANC")
        self.assertEqual(normalize.guess_couleur("boisson"), "AUTRE")

    def test_parse_vintage(self):
        self.assertEqual(normalize.parse_vintage("Château X 2018"), 2018)
        self.assertEqual(normalize.parse_vintage(None, "Cuvée 1998 Réserve"), 1998)
        self.assertIsNone(normalize.parse_vintage("sans année", ""))


class ApogeeTests(SimpleTestCase):
    """Calcul pur de la fenêtre de dégustation et du statut — pas de base."""

    def test_fenetre_depuis_couleur_et_millesime(self):
        # Rouge : 3 à 12 ans de garde après la récolte.
        self.assertEqual(apogee.fenetre_apogee("ROUGE", 2015), (2018, 2027))
        # Blanc : 1 à 5 ans.
        self.assertEqual(apogee.fenetre_apogee("BLANC", 2020), (2021, 2025))

    def test_couleur_inconnue_retombe_sur_le_defaut(self):
        self.assertEqual(apogee.fenetre_apogee("INEXISTANTE", 2020), (2021, 2026))

    def test_vin_non_millesime_sans_fenetre(self):
        self.assertEqual(apogee.fenetre_apogee("ROUGE", None), (None, None))

    def test_cepage_de_garde_allonge_la_fenetre(self):
        # Base rouge 2015 : (2018, 2027). Un Cabernet Sauvignon (facteur 1.4)
        # repousse ouverture et fin de garde.
        self.assertEqual(
            apogee.fenetre_apogee("ROUGE", 2015, cepages=["Cabernet Sauvignon"]),
            (2019, 2032),
        )

    def test_cepage_de_plaisir_raccourcit_la_fenetre(self):
        # Un Gamay (facteur 0.6) se boit bien plus tôt qu'un rouge de garde.
        self.assertEqual(
            apogee.fenetre_apogee("ROUGE", 2015, cepages=["Gamay"]), (2017, 2022)
        )

    def test_assemblage_retient_le_cepage_le_plus_de_garde(self):
        # Merlot (1.0) + Cabernet Sauvignon (1.4) -> le Cabernet impose le rythme.
        self.assertEqual(
            apogee.fenetre_apogee(
                "ROUGE", 2015, cepages=["Merlot", "Cabernet Sauvignon"]
            ),
            (2019, 2032),
        )

    def test_cepage_inconnu_est_neutre(self):
        # Cépage hors table -> aucun ajustement, on garde la base couleur.
        self.assertEqual(
            apogee.fenetre_apogee("ROUGE", 2015, cepages=["Zibibbo"]), (2018, 2027)
        )
        self.assertEqual(apogee.fenetre_apogee("ROUGE", 2015, cepages=[]), (2018, 2027))

    def test_grand_millesime_allonge_la_fenetre(self):
        # Bordeaux 2016 (millésime exceptionnel) -> garde repoussée.
        self.assertEqual(
            apogee.fenetre_apogee("ROUGE", 2016, region="Bordeaux"), (2019, 2032)
        )

    def test_petit_millesime_avance_la_consommation(self):
        # Bordeaux 2013 (millésime faible) -> à boire plus tôt que la base.
        self.assertEqual(
            apogee.fenetre_apogee("ROUGE", 2013, region="Médoc"), (2016, 2021)
        )

    def test_qualite_millesime_par_sous_region_et_defauts(self):
        # Sous-région rattachée à sa grande région.
        self.assertEqual(apogee.qualite_millesime("Saint-Émilion", 2013), 1)
        self.assertEqual(apogee.qualite_millesime("Barolo", 2010), 5)
        # Région ou année inconnue -> note neutre (3), aucun ajustement.
        self.assertEqual(apogee.qualite_millesime("Californie", 2016), 3)
        self.assertEqual(apogee.qualite_millesime("Bordeaux", 1789), 3)
        self.assertEqual(apogee.qualite_millesime(None, 2016), 3)

    def test_table_millesimes_injectable(self):
        # Table injectée (ex: MillesimeReference) : prime sur la table intégrée.
        injectee = {"bordeaux": {2010: 1}}
        self.assertEqual(apogee.qualite_millesime("Médoc", 2010, injectee), 1)
        # Année absente de la table injectée -> note neutre.
        self.assertEqual(apogee.qualite_millesime("Médoc", 2016, injectee), 3)
        self.assertEqual(apogee.qualite_millesime("Bordeaux", None), 3)

    def test_cepage_et_millesime_se_combinent(self):
        # Cabernet Sauvignon (1.4) sur un grand Bordeaux 2016 (1.15 / 1.30).
        self.assertEqual(
            apogee.fenetre_apogee(
                "ROUGE", 2016, cepages=["Cabernet Sauvignon"], region="Bordeaux"
            ),
            (2021, 2038),
        )

    def test_statut_selon_l_annee(self):
        # Fenêtre 2018-2027.
        self.assertEqual(apogee.statut_pour_fenetre(2018, 2027, 2016), apogee.A_GARDER)
        self.assertEqual(apogee.statut_pour_fenetre(2018, 2027, 2020), apogee.A_BOIRE)
        self.assertEqual(apogee.statut_pour_fenetre(2018, 2027, 2030), apogee.DEPASSE)
        # Bornes incluses.
        self.assertEqual(apogee.statut_pour_fenetre(2018, 2027, 2018), apogee.A_BOIRE)
        self.assertEqual(apogee.statut_pour_fenetre(2018, 2027, 2027), apogee.A_BOIRE)

    def test_statut_fenetre_partielle_ou_absente(self):
        # Seule la borne de fin connue.
        self.assertEqual(apogee.statut_pour_fenetre(None, 2027, 2030), apogee.DEPASSE)
        self.assertEqual(apogee.statut_pour_fenetre(None, 2027, 2020), apogee.A_BOIRE)
        # Seule la borne de début connue.
        self.assertEqual(apogee.statut_pour_fenetre(2018, None, 2016), apogee.A_GARDER)
        # Aucune borne (vin non datable) -> statut neutre.
        self.assertEqual(apogee.statut_pour_fenetre(None, None, 2020), apogee.A_GARDER)

    def test_statut_utilise_l_annee_courante_par_defaut(self):
        annee = date.today().year
        self.assertEqual(
            apogee.statut_pour_fenetre(annee - 1, annee + 1), apogee.A_BOIRE
        )


def _fake_urlopen(payload_bytes):
    """Contexte manager imitant urllib.request.urlopen (.read())."""
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = payload_bytes
    return cm


class OpenFoodFactsProviderTests(SimpleTestCase):
    """Provider code-barres avec urlopen mocké — aucun appel réseau réel."""

    def setUp(self):
        self.provider = OpenFoodFactsProvider()

    @patch("apps.catalog.enrichment.openfoodfacts.urllib.request.urlopen")
    def test_hit_mappe_les_champs(self, mock_urlopen):
        mock_urlopen.return_value = _fake_urlopen(
            b'{"status": 1, "product": {"brands": "Chateau X, Second",'
            b' "product_name": "Grand Vin rouge", "categories": "Vins, Vins rouges",'
            b' "code": "3760012345678"}}'
        )
        wine = self.provider.lookup_by_barcode("3760012345678")

        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Chateau X")  # 1re marque
        self.assertEqual(wine.cuvee_nom, "Grand Vin rouge")
        self.assertEqual(wine.couleur, "ROUGE")
        self.assertEqual(wine.code_barres, "3760012345678")
        self.assertEqual(wine.source, "openfoodfacts")

    @patch("apps.catalog.enrichment.openfoodfacts.urllib.request.urlopen")
    def test_produit_absent_renvoie_none(self, mock_urlopen):
        mock_urlopen.return_value = _fake_urlopen(b'{"status": 0}')
        self.assertIsNone(self.provider.lookup_by_barcode("0000000000000"))

    @patch("apps.catalog.enrichment.openfoodfacts.urllib.request.urlopen")
    def test_reseau_indisponible_est_un_miss(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("réseau coupé")
        self.assertIsNone(self.provider.lookup_by_barcode("3760012345678"))

    @patch("apps.catalog.enrichment.openfoodfacts.urllib.request.urlopen")
    def test_millesime_parse_depuis_le_nom(self, mock_urlopen):
        mock_urlopen.return_value = _fake_urlopen(
            b'{"status": 1, "product": {"product_name": "Ch\\u00e2teau X 2018", "code": "1"}}'
        )
        wine = self.provider.lookup_by_barcode("1")
        self.assertEqual(wine.millesime, 2018)


class UpsertCuveeTests(TestCase):
    """Cache local mutualisé : déduplication et persistance des cépages."""

    def test_dedup_par_code_barres(self):
        w1 = NormalizedWine(domaine_nom="Dom", cuvee_nom="Cuvée A", code_barres="123", couleur="ROUGE")
        cuvee1, created1 = upsert_cuvee(w1)
        self.assertTrue(created1)

        # Même code-barres, autre nom -> on retrouve la cuvée existante, pas de doublon.
        w2 = NormalizedWine(domaine_nom="Dom", cuvee_nom="Autre nom", code_barres="123")
        cuvee2, created2 = upsert_cuvee(w2)
        self.assertFalse(created2)
        self.assertEqual(cuvee1.pk, cuvee2.pk)
        self.assertEqual(Cuvee.objects.count(), 1)

    def test_dedup_par_reference_externe(self):
        w = NormalizedWine(domaine_nom="Dom", cuvee_nom="C", reference_externe_id="wine-42")
        _, created1 = upsert_cuvee(w)
        _, created2 = upsert_cuvee(
            NormalizedWine(domaine_nom="Dom", cuvee_nom="C bis", reference_externe_id="wine-42")
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(Cuvee.objects.count(), 1)

    def test_cepages_enregistres_a_la_creation(self):
        w = NormalizedWine(
            domaine_nom="Dom", cuvee_nom="C", code_barres="999",
            cepages=["Merlot", "Cabernet Sauvignon"],
        )
        cuvee, _ = upsert_cuvee(w)
        self.assertEqual(cuvee.cepages.count(), 2)
        self.assertTrue(Cepage.objects.filter(nom="Merlot").exists())

    def test_couleur_invalide_repli_sur_autre(self):
        w = NormalizedWine(domaine_nom="Dom", cuvee_nom="C", code_barres="1", couleur="MAUVE")
        cuvee, _ = upsert_cuvee(w)
        self.assertEqual(cuvee.couleur, Cuvee.Couleur.AUTRE)

    def test_contrainte_unicite_code_barres(self):
        """Deux cuvées ne peuvent plus partager un même code-barres non vide."""
        domaine = Domaine.objects.create(nom="Dom")
        Cuvee.objects.create(domaine=domaine, nom="A", couleur="ROUGE", code_barres="777")
        with self.assertRaises(IntegrityError):
            Cuvee.objects.create(domaine=domaine, nom="B", couleur="ROUGE", code_barres="777")

    def test_contrainte_unicite_tolere_code_barres_vide(self):
        """La contrainte est partielle : plusieurs cuvées sans code-barres coexistent."""
        domaine = Domaine.objects.create(nom="Dom")
        Cuvee.objects.create(domaine=domaine, nom="A", couleur="ROUGE", code_barres="")
        Cuvee.objects.create(domaine=domaine, nom="B", couleur="BLANC", code_barres="")
        self.assertEqual(Cuvee.objects.filter(code_barres="").count(), 2)

    def test_scan_code_barres_retrouve_le_vin_connu_par_sa_reference_externe(self):
        """Un vin identifié par texte (référence wineapi) puis scanné par son
        code-barres ne doit pas être recréé : la référence externe est unique et
        la création échouait en IntegrityError (500 au scan)."""
        upsert_cuvee(NormalizedWine(
            domaine_nom="Château Palmer", cuvee_nom="Palmer",
            reference_externe_id="wineapi-123", couleur="ROUGE",
        ))
        cuvee, created = upsert_cuvee(NormalizedWine(
            domaine_nom="Château Palmer", cuvee_nom="Palmer",
            code_barres="3760012345678", reference_externe_id="wineapi-123",
            couleur="ROUGE",
        ))
        self.assertFalse(created)
        self.assertEqual(Cuvee.objects.count(), 1)
        # Le code-barres inédit complète l'identité : le prochain scan sera un
        # hit local, sans appel externe ni quota consommé.
        self.assertEqual(cuvee.code_barres, "3760012345678")

    def test_reference_externe_inconnue_reste_decisive(self):
        """Deux références wineapi distinctes = deux vins, même sans code-barres."""
        upsert_cuvee(NormalizedWine(
            domaine_nom="Dom", cuvee_nom="A", reference_externe_id="wine-a"
        ))
        _, created = upsert_cuvee(NormalizedWine(
            domaine_nom="Dom", cuvee_nom="B", reference_externe_id="wine-b"
        ))
        self.assertTrue(created)
        self.assertEqual(Cuvee.objects.count(), 2)

    def test_identite_deja_revendiquee_non_ecrasee(self):
        """La complétion d'identité ne vole pas une référence déjà attribuée."""
        domaine = Domaine.objects.create(nom="Dom")
        # La cuvée retrouvée par son code-barres n'a pas de référence externe…
        Cuvee.objects.create(domaine=domaine, nom="A", couleur="ROUGE", code_barres="111")
        # …mais « wine-x » appartient déjà à une autre cuvée.
        Cuvee.objects.create(
            domaine=domaine, nom="B", couleur="ROUGE", reference_externe_id="wine-x"
        )
        cuvee, created = upsert_cuvee(NormalizedWine(
            domaine_nom="Dom", cuvee_nom="A", code_barres="111",
            reference_externe_id="wine-x",
        ))
        self.assertFalse(created)
        self.assertEqual(cuvee.nom, "A")
        self.assertEqual(cuvee.reference_externe_id, "")  # laissée à son détenteur
        self.assertEqual(Cuvee.objects.filter(reference_externe_id="wine-x").count(), 1)

    def test_domaine_non_scinde_par_region_vide(self):
        """Un canal qui identifie un producteur déjà connu (avec région) ne doit
        pas créer une seconde fiche sans région (cf. correction de l'ingest)."""
        Domaine.objects.create(nom="Château Margaux", region="Bordeaux")
        upsert_cuvee(
            NormalizedWine(domaine_nom="Château Margaux", cuvee_nom="Grand Vin", code_barres="900")
        )
        self.assertEqual(Domaine.objects.filter(nom="Château Margaux").count(), 1)
        self.assertEqual(
            Cuvee.objects.get(code_barres="900").domaine.region, "Bordeaux"
        )


class SourceObservationTests(TestCase):
    """Phase 1 : chaque hit de canal dépose une observation brute (append-only)."""

    def test_upsert_enregistre_une_observation_du_canal(self):
        w = NormalizedWine(
            domaine_nom="Dom", cuvee_nom="C", couleur="ROUGE",
            code_barres="123", source="openfoodfacts",
            raw={"brands": "Dom", "categories": "Vins"},
        )
        cuvee, _ = upsert_cuvee(w)
        obs = cuvee.observations.get()
        self.assertEqual(obs.canal, "openfoodfacts")
        self.assertEqual(obs.payload_brut, {"brands": "Dom", "categories": "Vins"})
        self.assertEqual(obs.champs["code_barres"], "123")
        self.assertEqual(obs.champs["couleur"], "ROUGE")
        self.assertEqual(float(obs.confiance), 0.90)  # défaut du canal OFF

    def test_observations_s_accumulent_sans_ecraser(self):
        """Deux hits (canaux différents) sur le même vin = deux observations."""
        cle = {"reference_externe_id": "wine-7"}
        upsert_cuvee(NormalizedWine(domaine_nom="Dom", cuvee_nom="C", source="claude", **cle))
        cuvee, _ = upsert_cuvee(
            NormalizedWine(domaine_nom="Dom", cuvee_nom="C", source="wineapi", **cle)
        )
        self.assertEqual(cuvee.observations.count(), 2)
        self.assertEqual(
            set(cuvee.observations.values_list("canal", flat=True)),
            {"claude", "wineapi"},
        )

    def test_confiance_par_defaut_du_canal(self):
        domaine = Domaine.objects.create(nom="Dom")
        cuvee = Cuvee.objects.create(domaine=domaine, nom="C", couleur="ROUGE")
        obs_lwin = enregistrer_observation(cuvee, canal="lwin")
        obs_scrape = enregistrer_observation(cuvee, canal="scrape:exemple")
        obs_inconnu = enregistrer_observation(cuvee, canal="mystere")
        self.assertEqual(float(obs_lwin.confiance), 0.95)
        self.assertEqual(float(obs_scrape.confiance), 0.40)  # scraping arbitre en dernier
        self.assertEqual(float(obs_inconnu.confiance), 0.50)  # défaut

    def test_synchroniser_wineapi_observe_et_projette(self):
        domaine = Domaine.objects.create(nom="Dom")
        cuvee = Cuvee.objects.create(domaine=domaine, nom="C", couleur="ROUGE")
        detail = {"id": 7, "wine": {"name": "C", "region": "Bordeaux"}}
        synchroniser_wineapi(cuvee, detail)
        obs = cuvee.observations.get()
        self.assertEqual(obs.canal, "wineapi")
        self.assertEqual(obs.payload_brut, detail)
        cuvee.refresh_from_db()
        self.assertIsNotNone(cuvee.enrichi_le)  # projection appliquée

    def test_synchroniser_wineapi_sans_detail_n_observe_rien(self):
        domaine = Domaine.objects.create(nom="Dom")
        cuvee = Cuvee.objects.create(domaine=domaine, nom="C", couleur="ROUGE")
        synchroniser_wineapi(cuvee, None)
        self.assertEqual(cuvee.observations.count(), 0)


class ConsolidationTests(TestCase):
    """Phase 2 : arbitrage des observations en fiche de vérité + provenance."""

    def setUp(self):
        self.domaine = Domaine.objects.create(nom="Dom")
        self.cuvee = Cuvee.objects.create(domaine=self.domaine, nom="C", couleur="ROUGE")

    def test_profil_prend_la_source_la_plus_fiable(self):
        """Pour un champ de profil, la confiance prime (pas la récence)."""
        # Relevé récent mais peu fiable (scraping) vs relevé plus ancien mais fiable (lwin).
        enregistrer_observation(
            self.cuvee, canal="scrape:x", champs={"region": "Faux"}, confiance=0.40
        )
        enregistrer_observation(
            self.cuvee, canal="lwin", champs={"region": "Bordeaux"}, confiance=0.95
        )
        consolider(self.cuvee)
        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.region, "Bordeaux")
        self.assertEqual(self.cuvee.provenance["region"]["canal"], "lwin")

    def test_marche_prend_le_releve_le_plus_recent(self):
        """Pour un champ de marché, la récence prime (même si moins fiable)."""
        ancien = enregistrer_observation(
            self.cuvee, canal="wineapi", champs={"prix_min": 20}, confiance=0.70
        )
        recent = enregistrer_observation(
            self.cuvee, canal="scrape:x", champs={"prix_min": 35}, confiance=0.40
        )
        # Force un ordre temporel déterministe.
        SourceObservation.objects.filter(pk=ancien.pk).update(
            releve_le=timezone.now() - timezone.timedelta(days=30)
        )
        consolider(self.cuvee)
        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.prix_min, 35)
        self.assertEqual(self.cuvee.provenance["prix_min"]["canal"], "scrape:x")

    def test_ne_supprime_pas_une_valeur_qu_aucune_source_ne_contredit(self):
        """Une valeur héritée reste si aucune observation ne l'affirme/contredit."""
        self.cuvee.description = "Hérité"
        self.cuvee.save()
        enregistrer_observation(self.cuvee, canal="lwin", champs={"region": "Bordeaux"})
        consolider(self.cuvee)
        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.description, "Hérité")  # préservée

    def test_sans_observation_cuvee_inchangee(self):
        avant = self.cuvee.provenance
        consolider(self.cuvee)
        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.provenance, avant)

    def test_upsert_consolide_et_renseigne_la_provenance(self):
        detail = {"id": 7, "region": {"name": "Bordeaux"}, "description": "Un beau vin"}
        wine = NormalizedWine(
            domaine_nom="Dom", cuvee_nom="C2", couleur="ROUGE",
            reference_externe_id="wine-7", source="wineapi",
            raw={"wineapi_detail": detail},
        )
        cuvee, _ = upsert_cuvee(wine)
        cuvee.refresh_from_db()
        self.assertEqual(cuvee.region, "Bordeaux")
        self.assertEqual(cuvee.provenance["region"]["canal"], "wineapi")

    def test_reconsolider_rejoue_sur_tout_le_referentiel(self):
        enregistrer_observation(self.cuvee, canal="lwin", champs={"region": "Bordeaux"})
        # Pas encore consolidée : la commande doit la rattraper.
        self.assertEqual(self.cuvee.region, "")
        call_command("reconsolider")
        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.region, "Bordeaux")


class LwinCanalTests(TestCase):
    """Phase 3 : le code LWIN comme identité canonique + confiance du relevé."""

    def _wine_lwin(self, lwin, *, producteur="Dom", vin="C", confiance=0.9):
        detail = {
            "name": vin, "winery": {"name": producteur},
            "region": {"name": "Bordeaux", "country": "France"},
            "lwinCode": lwin,
        }
        return NormalizedWine(
            domaine_nom=producteur, cuvee_nom=vin, couleur="ROUGE",
            source="lwin", confiance=confiance, raw={"wineapi_detail": detail},
        )

    def test_reconciliation_par_code_lwin(self):
        """Deux relevés LWIN du même vin convergent vers une seule cuvée."""
        _, created1 = upsert_cuvee(self._wine_lwin("1234567890123456"))
        cuvee, created2 = upsert_cuvee(
            self._wine_lwin("1234567890123456", vin="Nom OCR différent")
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(Cuvee.objects.filter(lwin_code="1234567890123456").count(), 1)

    def test_lwin_reconcilie_avec_une_cuvee_wineapi(self):
        """Une cuvée créée par wineapi (portant un code LWIN) est retrouvée par LWIN."""
        detail = {"id": 9, "lwinCode": "9999999999999999", "region": {"name": "Bordeaux"}}
        upsert_cuvee(NormalizedWine(
            domaine_nom="Dom", cuvee_nom="C", couleur="ROUGE",
            reference_externe_id="wine-9", source="wineapi",
            raw={"wineapi_detail": detail},
        ))
        _, created = upsert_cuvee(self._wine_lwin("9999999999999999"))
        self.assertFalse(created)  # réconciliée, pas dupliquée
        self.assertEqual(Cuvee.objects.filter(lwin_code="9999999999999999").count(), 1)

    def test_contrainte_unicite_lwin_code(self):
        domaine = Domaine.objects.create(nom="Dom")
        Cuvee.objects.create(domaine=domaine, nom="A", couleur="ROUGE", lwin_code="1111111111111111")
        with self.assertRaises(IntegrityError):
            Cuvee.objects.create(domaine=domaine, nom="B", couleur="ROUGE", lwin_code="1111111111111111")

    def test_confiance_du_releve_lwin_transmise_a_l_observation(self):
        """Un match LWIN faible pèse moins qu'un canal a priori fiable."""
        cuvee, _ = upsert_cuvee(self._wine_lwin("2222222222222222", confiance=0.55))
        obs = cuvee.observations.get(canal="lwin")
        self.assertEqual(float(obs.confiance), 0.55)  # score réel, pas le défaut 0.95

    def test_code_lwin_partage_par_deux_vins_distincts_ne_plante_pas(self):
        """Garde-fou : deux id wineapi portant le même code LWIN ne violent pas
        l'unicité — le second est créé sans code LWIN plutôt que de faire échouer
        le scan."""
        def wine(ref):
            return NormalizedWine(
                domaine_nom="Dom", cuvee_nom=f"C-{ref}", couleur="ROUGE",
                reference_externe_id=ref, source="wineapi",
                raw={"wineapi_detail": {"id": ref, "lwinCode": "5555555555555555"}},
            )
        _, c1 = upsert_cuvee(wine("wine-a"))
        _, c2 = upsert_cuvee(wine("wine-b"))
        self.assertTrue(c1)
        self.assertTrue(c2)  # créé (vin distinct), sans code LWIN
        self.assertEqual(Cuvee.objects.filter(lwin_code="5555555555555555").count(), 1)
        self.assertEqual(Cuvee.objects.get(reference_externe_id="wine-b").lwin_code, "")

    def test_match_lwin_faible_ne_prime_pas_sur_wineapi(self):
        """La consolidation profil départage par confiance : wineapi > LWIN faible."""
        domaine = Domaine.objects.create(nom="Dom")
        cuvee = Cuvee.objects.create(domaine=domaine, nom="C", couleur="ROUGE")
        enregistrer_observation(cuvee, canal="lwin", champs={"region": "Faux"}, confiance=0.50)
        enregistrer_observation(cuvee, canal="wineapi", champs={"region": "Bordeaux"}, confiance=0.70)
        consolider(cuvee)
        cuvee.refresh_from_db()
        self.assertEqual(cuvee.region, "Bordeaux")


class MillesimeReferenceTests(TestCase):
    """Phase 4 : table des millésimes sourçable, injectée dans la logique pure."""

    def setUp(self):
        cache.clear()  # la table est mise en cache : on repart propre.

    def test_seed_depuis_la_reference_integree(self):
        """La migration sème la table depuis apogee.MILLESIMES."""
        self.assertTrue(
            MillesimeReference.objects.filter(
                region_cle="bordeaux", annee=2010, note=5
            ).exists()
        )

    def test_table_reflete_la_base(self):
        MillesimeReference.objects.update_or_create(
            region_cle="bordeaux", annee=2010, defaults={"note": 2, "source": "manuel"}
        )
        self.assertEqual(MillesimeReference.table()["bordeaux"][2010], 2)

    def test_table_repli_sur_la_reference_integree_si_vide(self):
        MillesimeReference.objects.all().delete()
        MillesimeReference.vider_cache()
        self.assertEqual(MillesimeReference.table(), apogee.MILLESIMES)

    def test_correction_admin_invalide_le_cache(self):
        MillesimeReference.table()  # amorce le cache
        ref = MillesimeReference.objects.get(region_cle="bordeaux", annee=2010)
        ref.note = 1
        ref.save()  # doit vider le cache
        self.assertEqual(MillesimeReference.table()["bordeaux"][2010], 1)

    def test_bouteille_utilise_la_table_db(self):
        """La fenêtre d'apogée d'une bouteille suit la table BDD (pas le code)."""
        domaine = Domaine.objects.create(nom="Ch. Test")
        cuvee = Cuvee.objects.create(
            domaine=domaine, nom="C", couleur="ROUGE", region="Médoc"
        )
        user = User.objects.create_user("bob", password="x")
        from apps.inventory.models import Bouteille

        bouteille = Bouteille.objects.create(proprietaire=user, cuvee=cuvee, millesime=2010)
        avant = bouteille.fenetre_apogee()
        # On abaisse la qualité 2010 du Médoc : la fenêtre doit se resserrer.
        MillesimeReference.objects.filter(region_cle="bordeaux", annee=2010).update(note=1)
        MillesimeReference.vider_cache()
        apres = bouteille.fenetre_apogee()
        self.assertNotEqual(avant, apres)
        self.assertLess(apres[1], avant[1])  # grand millésime → petit : fin plus tôt


class DedupIdentiteMigrationTests(TransactionTestCase):
    """Migration 0008 : dédoublonnage des identités avant pose des contraintes.

    On rembobine jusqu'à l'état 0007, on fabrique les doublons que la migration
    doit résorber (impossibles à créer une fois les contraintes posées), puis on
    applique 0008 et on vérifie la convergence. La base de test est remise à
    l'état le plus récent en fin de test.
    """

    migrate_from = [("catalog", "0007_referencelwin")]
    migrate_to = [("catalog", "0008_dedup_identite_cuvee")]

    def test_fusionne_domaines_et_cuvees_en_double(self):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Domaine = old_apps.get_model("catalog", "Domaine")
        Cuvee = old_apps.get_model("catalog", "Cuvee")

        # Producteur scindé : une fiche régionale + une fiche vide (artefact ingest).
        d_region = Domaine.objects.create(nom="Dom", region="Bordeaux")
        d_vide = Domaine.objects.create(nom="Dom", region="")
        # Deux cuvées d'un même code-barres, rattachées à la fiche vide.
        Cuvee.objects.create(domaine=d_vide, nom="A", couleur="ROUGE", code_barres="555")
        Cuvee.objects.create(domaine=d_vide, nom="B", couleur="ROUGE", code_barres="555")

        executor.loader.build_graph()  # recharge le graphe avant d'avancer
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        Domaine = new_apps.get_model("catalog", "Domaine")
        Cuvee = new_apps.get_model("catalog", "Cuvee")

        # Le producteur n'a plus qu'une fiche (la régionale), le doublon vide est parti.
        doms = Domaine.objects.filter(nom="Dom")
        self.assertEqual(doms.count(), 1)
        self.assertEqual(doms.first().pk, d_region.pk)
        # Une seule cuvée subsiste pour le code-barres, re-rattachée au producteur gardé.
        cuvees = Cuvee.objects.filter(code_barres="555")
        self.assertEqual(cuvees.count(), 1)
        self.assertEqual(cuvees.first().domaine.pk, d_region.pk)

    def tearDown(self):
        # Laisse la base de test à jour pour les tests suivants.
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


class _FakeProvider:
    """Provider factice pour piloter la cascade des vues sans réseau."""

    def __init__(self, wine=None, error=None, name="fake"):
        self._wine = wine
        self._error = error
        self.name = name

    def lookup_by_barcode(self, ean):
        if self._error:
            raise self._error
        return self._wine

    def lookup_by_text(self, query):
        if self._error:
            raise self._error
        return self._wine

    def lookup_by_image(self, data, content_type):
        if self._error:
            raise self._error
        return self._wine


class ScanCodeBarresViewTests(APITestCase):
    """US 01 — POST /api/scan-code-barres/ : cache local, cascade externe, échec."""

    def setUp(self):
        cache.clear()  # remet à zéro le compteur de throttle entre les tests
        self.url = reverse("scan-code-barres")
        self.client.force_authenticate(User.objects.create_user("alice", password="x"))

    def test_scenario_local_sans_reseau(self):
        domaine = Domaine.objects.create(nom="Chateau Local")
        Cuvee.objects.create(
            domaine=domaine, nom="Cuvée Locale", couleur=Cuvee.Couleur.ROUGE,
            code_barres="3760012345670",
        )
        resp = self.client.post(self.url, {"code_barres": "3760012345670"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["source"], "local")
        self.assertFalse(resp.data["created"])

    @patch("apps.catalog.views.get_enabled_providers")
    def test_scenario_hit_externe_met_en_cache(self, mock_providers):
        wine = NormalizedWine(
            domaine_nom="Dom Externe", cuvee_nom="Cuvée Externe",
            couleur="ROUGE", code_barres="3760012345888", source="openfoodfacts",
            millesime=2019,
        )
        mock_providers.return_value = [_FakeProvider(wine=wine)]

        resp = self.client.post(self.url, {"code_barres": "3760012345888"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["source"], "openfoodfacts")
        self.assertEqual(resp.data["millesime"], 2019)  # préremplissage du millésime
        self.assertTrue(resp.data["created"])
        # Mise en cache locale vérifiée : la cuvée est désormais en base.
        self.assertTrue(Cuvee.objects.filter(code_barres="3760012345888").exists())

    @patch("apps.catalog.views.get_enabled_providers")
    def test_scenario_echec_total_404(self, mock_providers):
        mock_providers.return_value = [_FakeProvider(wine=None)]
        resp = self.client.post(self.url, {"code_barres": "00000000"})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class IdentifierVinViewTests(APITestCase):
    """US 04 — POST /api/identifier-vin/ : cache local, erreur remontée, échec."""

    def setUp(self):
        cache.clear()  # remet à zéro le compteur de throttle entre les tests
        self.url = reverse("identifier-vin")
        self.client.force_authenticate(User.objects.create_user("alice", password="x"))

    def test_cache_local_sur_nom_sans_millesime(self):
        domaine = Domaine.objects.create(nom="Petrus")
        Cuvee.objects.create(domaine=domaine, nom="Chateau Petrus", couleur=Cuvee.Couleur.ROUGE)
        # Le millésime est retiré côté serveur pour retomber sur la cuvée en base.
        resp = self.client.post(self.url, {"query": "Chateau Petrus 2015"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["source"], "local")

    @patch("apps.catalog.views.get_enabled_providers")
    def test_erreur_fournisseur_remontee_avec_son_statut(self, mock_providers):
        mock_providers.return_value = [
            _FakeProvider(error=EnrichmentError(429, "Quota wineapi.io atteint, réessaie plus tard."))
        ]
        resp = self.client.post(self.url, {"query": "Chateau X"})

        self.assertEqual(resp.status_code, 429)
        self.assertIn("Quota", resp.data["detail"])

    @patch("apps.catalog.views.get_enabled_providers")
    def test_echec_total_404(self, mock_providers):
        mock_providers.return_value = [_FakeProvider(wine=None)]
        resp = self.client.post(self.url, {"query": "Vin fantôme"})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class IdentifierVinLwinTests(APITestCase):
    """POST /api/identifier-vin/ avec un code `lwin` : résolution locale directe
    (sélection d'une suggestion de la recherche dynamique), sans cascade externe."""

    def setUp(self):
        from .enrichment import lwin as module_lwin

        cache.clear()
        module_lwin._cache = {"version": None, "refs": [], "idf": {}, "postings": {}, "vocab": []}
        self.url = reverse("identifier-vin")
        self.client.force_authenticate(User.objects.create_user("alice", password="x"))
        ReferenceLwin.objects.create(
            lwin="1011248", producteur="Château Palmer", pays="France",
            region="Bordeaux", sous_region="Margaux", couleur="ROUGE",
        )

    @patch("apps.catalog.views.get_enabled_providers")
    def test_selection_lwin_court_circuite_la_cascade(self, mock_providers):
        resp = self.client.post(self.url, {"query": "Château Palmer 1998", "lwin": "1011248"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["source"], "lwin")
        self.assertEqual(resp.data["millesime"], 1998)
        self.assertEqual(resp.data["cuvee"]["lwin_code"], "1011248")
        self.assertEqual(resp.data["cuvee"]["couleur"], "ROUGE")
        mock_providers.assert_not_called()  # aucun provider externe sollicité

    @patch("apps.catalog.views.get_enabled_providers")
    def test_lwin_inconnu_retombe_sur_la_cascade(self, mock_providers):
        # Code périmé (référentiel ré-importé…) : flux normal, ici échec total.
        mock_providers.return_value = [_FakeProvider(wine=None)]
        resp = self.client.post(self.url, {"query": "Vin fantôme", "lwin": "9999999"})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class RechercheVinsViewTests(APITestCase):
    """GET /api/recherche-vins/ : autocomplétion locale sur le référentiel LWIN
    (préfixe, extraction millésime/couleur, requêtes trop vagues)."""

    def setUp(self):
        from .enrichment import lwin as module_lwin

        cache.clear()
        module_lwin._cache = {"version": None, "refs": [], "idf": {}, "postings": {}, "vocab": []}
        self.url = reverse("recherche-vins")
        ReferenceLwin.objects.create(
            lwin="1011247", producteur="Château Margaux", pays="France",
            region="Bordeaux", sous_region="Margaux", couleur="ROUGE",
        )
        ReferenceLwin.objects.create(
            lwin="1011248", producteur="Château Palmer", pays="France",
            region="Bordeaux", sous_region="Margaux", couleur="ROUGE",
        )
        ReferenceLwin.objects.create(
            lwin="1055555", producteur="Bollinger", vin="Grande Année",
            pays="France", region="Champagne", couleur="BULLES",
        )

    def test_prefixe_au_fil_de_la_frappe(self):
        resp = self.client.get(self.url, {"q": "chateau marg"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        lwins = [r["lwin"] for r in resp.data["resultats"]]
        self.assertIn("1011247", lwins)  # « marg » préfixe « margaux »
        premier = resp.data["resultats"][0]
        self.assertEqual(premier["libelle"], "Château Margaux (Margaux)")
        self.assertEqual(premier["couleur"], "ROUGE")

    def test_evaluation_sure_quand_le_meilleur_domine(self):
        # « chateau margaux » : une seule référence plausible, score maximal ->
        # l'appli peut proposer directement la fiche pré-remplie.
        resp = self.client.get(self.url, {"q": "chateau margaux"})
        self.assertEqual(resp.data["evaluation"], "sur")
        premier = resp.data["resultats"][0]
        self.assertEqual(premier["score"], 1.0)
        self.assertIsNone(premier["en_base"])  # cuvée pas encore au catalogue

    def test_evaluation_hesitante_entre_plusieurs_candidats(self):
        # Deux cuvées du même producteur à égalité parfaite : vérification
        # manuelle sollicitée (liste), pas de fiche imposée. Le grand vin
        # (nom de cuvée vide) est proposé avant sa déclinaison.
        ReferenceLwin.objects.create(
            lwin="1011300", producteur="Château Margaux",
            vin="Margaux du Château Margaux", pays="France",
            region="Bordeaux", sous_region="Margaux", couleur="ROUGE",
        )
        resp = self.client.get(self.url, {"q": "chateau margaux"})
        self.assertEqual(resp.data["evaluation"], "hesitant")
        libelles = [r["libelle"] for r in resp.data["resultats"]]
        self.assertEqual(libelles[0], "Château Margaux (Margaux)")
        self.assertIn("Château Margaux - Margaux du Château Margaux", libelles)

    def test_en_base_expose_l_enrichissement_communautaire(self):
        # La cuvée existe déjà dans le catalogue partagé (nourri par les autres
        # utilisateurs) : la suggestion embarque cépages, note et accords pour
        # étoffer la fiche proposée — sans aucune donnée privée.
        domaine = Domaine.objects.create(nom="Château Margaux")
        cuvee = Cuvee.objects.create(
            domaine=domaine, nom="Château Margaux", couleur="ROUGE",
            lwin_code="1011247", note_moyenne="4.60", nb_notes=128,
            accords=[{"nom": "Bœuf", "emoji": "🥩", "confiance": 0.9}],
        )
        cuvee.cepages.add(Cepage.objects.create(nom="Cabernet Sauvignon"))
        resp = self.client.get(self.url, {"q": "chateau margaux"})
        en_base = resp.data["resultats"][0]["en_base"]
        self.assertEqual(en_base["cuvee_id"], cuvee.id)
        self.assertEqual(en_base["cepages"], ["Cabernet Sauvignon"])
        self.assertEqual(en_base["note"], 4.6)
        self.assertEqual(en_base["nb_notes"], 128)
        self.assertEqual(en_base["accords"][0]["nom"], "Bœuf")

    def test_millesime_extrait_et_couleur_filtree(self):
        resp = self.client.get(self.url, {"q": "palmer rouge 1998"})
        self.assertEqual(resp.data["resultats"][0]["millesime"], 1998)
        # « palmer blanc » : la couleur mentionnée contredit la référence (ROUGE).
        resp = self.client.get(self.url, {"q": "palmer blanc"})
        self.assertEqual(resp.data["resultats"], [])

    def test_requete_trop_courte_ou_trop_vague(self):
        self.assertEqual(self.client.get(self.url, {"q": "m"}).data["resultats"], [])
        self.assertEqual(self.client.get(self.url).data["resultats"], [])
        # Que des mots génériques : rien d'identifiant.
        self.assertEqual(self.client.get(self.url, {"q": "grande annee"}).data["resultats"], [])

    def test_accessible_sans_authentification(self):
        # Le référentiel est public en lecture, comme le reste du catalogue.
        resp = self.client.get(self.url, {"q": "bollinger grande annee"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["resultats"][0]["lwin"], "1055555")


class ScanEtiquetteViewTests(APITestCase):
    """US 02/03 — POST /api/scan-etiquette/ : upload validé, cascade image, échec."""

    def setUp(self):
        cache.clear()  # remet à zéro le compteur de throttle entre les tests
        self.url = reverse("scan-etiquette")
        self.client.force_authenticate(User.objects.create_user("alice", password="x"))

    def _photo(self, name="etiquette.jpg", content_type="image/jpeg", size=1024):
        return SimpleUploadedFile(name, b"x" * size, content_type=content_type)

    @patch("apps.catalog.views.get_enabled_providers")
    def test_hit_identifie_et_met_en_cache(self, mock_providers):
        wine = NormalizedWine(
            domaine_nom="Dom Photo", cuvee_nom="Cuvée Photo", couleur="ROUGE",
            source="wineapi", reference_externe_id="wine-77",
            raw={"confidence": 0.87, "region": "Pomerol"},
        )
        mock_providers.return_value = [_FakeProvider(wine=wine)]

        resp = self.client.post(self.url, {"image": self._photo()}, format="multipart")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["source"], "wineapi")
        self.assertEqual(resp.data["confidence"], 0.87)
        self.assertTrue(Cuvee.objects.filter(reference_externe_id="wine-77").exists())

    @patch("apps.catalog.views.get_enabled_providers")
    def test_echec_total_404(self, mock_providers):
        mock_providers.return_value = [_FakeProvider(wine=None)]
        resp = self.client.post(self.url, {"image": self._photo()}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch("apps.catalog.views.get_enabled_providers")
    def test_erreur_fournisseur_remontee(self, mock_providers):
        mock_providers.return_value = [
            _FakeProvider(error=EnrichmentError(429, "Quota wineapi.io atteint, réessaie plus tard."))
        ]
        resp = self.client.post(self.url, {"image": self._photo()}, format="multipart")
        self.assertEqual(resp.status_code, 429)

    def test_image_trop_lourde_400(self):
        gros = self._photo(size=ScanEtiquetteSerializer.MAX_SIZE + 1)
        resp = self.client.post(self.url, {"image": gros}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("image", resp.data)

    def test_mauvais_format_400(self):
        pdf = self._photo(name="doc.pdf", content_type="application/pdf")
        resp = self.client.post(self.url, {"image": pdf}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_sans_fichier_400(self):
        resp = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class EnrichmentThrottleTests(APITestCase):
    """Les endpoints d'enrichissement sont limités (protège le quota wineapi)."""

    def setUp(self):
        cache.clear()
        self.client.force_authenticate(User.objects.create_user("alice", password="x"))

    # DRF capture DEFAULT_THROTTLE_RATES à l'import (attribut de classe) :
    # override_settings est sans effet, on patche l'attribut directement.
    @patch.dict(
        "rest_framework.throttling.SimpleRateThrottle.THROTTLE_RATES",
        {"enrichment": "2/min"},
    )
    @patch("apps.catalog.views.get_enabled_providers")
    def test_429_au_dela_de_la_limite(self, mock_providers):
        mock_providers.return_value = []
        url = reverse("identifier-vin")
        for _ in range(2):
            resp = self.client.post(url, {"query": "abc"})
            self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        resp = self.client.post(url, {"query": "abc"})
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


def _http_error(code):
    return urllib.error.HTTPError("https://api.test/x", code, "err", None, None)


@override_settings(
    WINEAPI_KEY="cle-de-test",
    WINEAPI_BASE_URL="https://api.test",
    WINEAPI_TIMEOUT=5,
)
# TestCase (et non SimpleTestCase) : depuis le paramétrage à chaud, la résolution
# de la clé passe par un override en base (repli .env) — donc un accès DB.
class WineApiProviderTests(TestCase):
    """Client HTTP wineapi.io : activation, erreurs remontées, mapping (urlopen mocké)."""

    def setUp(self):
        self.provider = WineApiProvider()

    def test_enabled_suit_la_presence_de_cle(self):
        self.assertTrue(self.provider.enabled)
        with override_settings(WINEAPI_KEY=""):
            self.assertFalse(self.provider.enabled)

    @patch("apps.catalog.enrichment.wineapi.urllib.request.urlopen")
    def test_429_leve_une_erreur_remontable(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(429)
        with self.assertRaises(EnrichmentError) as ctx:
            self.provider._request("POST", "/identify/text", {"query": "x"})
        self.assertEqual(ctx.exception.status, 429)

    @patch("apps.catalog.enrichment.wineapi.urllib.request.urlopen")
    def test_401_leve_une_erreur_502(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(401)
        with self.assertRaises(EnrichmentError) as ctx:
            self.provider._request("GET", "/wines/1")
        self.assertEqual(ctx.exception.status, 502)  # clé invalide = erreur serveur

    @patch("apps.catalog.enrichment.wineapi.urllib.request.urlopen")
    def test_5xx_est_un_miss_silencieux(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(500)
        self.assertIsNone(self.provider._request("GET", "/wines/1"))

    @patch("apps.catalog.enrichment.wineapi.urllib.request.urlopen")
    def test_reseau_indisponible_est_un_miss(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("réseau coupé")
        self.assertIsNone(self.provider._request("GET", "/wines/1"))

    @override_settings(WINEAPI_ENRICH_DETAIL=False)
    @patch("apps.catalog.enrichment.wineapi.urllib.request.urlopen")
    def test_lookup_by_text_mappe_le_resultat(self, mock_urlopen):
        payload = json.dumps({
            "confidence": 0.9,
            "wine": {
                "id": 42, "name": "Chateau Petrus 2015", "type": "red",
                "vintage": 2015, "region": {"name": "Pomerol", "country": "France"},
            },
            "suggestions": [{"name": "Petrus 2016"}],
        }).encode("utf-8")
        mock_urlopen.return_value = _fake_urlopen(payload)

        wine = self.provider.lookup_by_text("Petrus")

        self.assertIsNotNone(wine)
        self.assertEqual(wine.cuvee_nom, "Chateau Petrus")  # millésime retiré
        self.assertEqual(wine.couleur, "ROUGE")
        self.assertEqual(wine.millesime, 2015)
        self.assertEqual(wine.appellation, "Pomerol")
        self.assertEqual(wine.source, "wineapi")
        self.assertIn("Petrus 2016", wine.raw["suggestions"])

    @override_settings(WINEAPI_ENRICH_DETAIL=True)
    @patch("apps.catalog.enrichment.wineapi.urllib.request.urlopen")
    def test_lookup_enrichit_via_appel_detail(self, mock_urlopen):
        identify = json.dumps({"wine": {"id": 7, "name": "Cuvée", "type": "white"}}).encode("utf-8")
        detail = json.dumps({
            "id": 7, "name": "Cuvée Prestige 2018", "type": "white",
            "winery": {"name": "Domaine Test"},
            "appellation": "Chablis",  # l'API renvoie l'appellation en chaîne
            "grapes": [{"name": "Chardonnay"}],
            "scores": [{"score": 92, "reviewer": "A"}, {"score": 88, "reviewer": "B"}],
            "region": {"name": "Bourgogne", "country": "France"},
            "vintage": 2018,
        }).encode("utf-8")
        # 1er appel = POST /identify/text, 2e appel = GET /wines/7 (enrichissement).
        mock_urlopen.side_effect = [_fake_urlopen(identify), _fake_urlopen(detail)]

        wine = self.provider.lookup_by_text("Cuvée")

        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(wine.domaine_nom, "Domaine Test")
        self.assertEqual(wine.cuvee_nom, "Cuvée Prestige")  # nom du détail, millésime retiré
        self.assertEqual(wine.couleur, "BLANC")
        self.assertEqual(wine.appellation, "Chablis")
        self.assertEqual(wine.cepages, ["Chardonnay"])
        self.assertEqual(wine.millesime, 2018)
        self.assertEqual(wine.raw["note"], 92)  # meilleur score

    @override_settings(WINEAPI_ENRICH_DETAIL=False)
    @patch("apps.catalog.enrichment.wineapi.urllib.request.urlopen")
    def test_reponse_sans_wine_renvoie_none(self, mock_urlopen):
        mock_urlopen.return_value = _fake_urlopen(b'{"confidence": 0.2}')
        self.assertIsNone(self.provider.lookup_by_text("inconnu"))

    @override_settings(WINEAPI_IMAGE_TIMEOUT=45, WINEAPI_ENRICH_DETAIL=False)
    @patch("apps.catalog.enrichment.wineapi.urllib.request.urlopen")
    def test_lookup_by_image_utilise_le_timeout_image(self, mock_urlopen):
        mock_urlopen.return_value = _fake_urlopen(b'{"wine": {"id": 1, "name": "X", "type": "red"}}')
        self.provider.lookup_by_image(b"img", "image/jpeg")
        # urlopen(req, timeout=WINEAPI_IMAGE_TIMEOUT) — la vision doit utiliser le timeout long.
        self.assertEqual(mock_urlopen.call_args.kwargs.get("timeout"), 45)

    @override_settings(WINEAPI_ENRICH_DETAIL=False)
    @patch("apps.catalog.enrichment.wineapi.urllib.request.urlopen")
    def test_lookup_by_image_envoie_du_multipart(self, mock_urlopen):
        payload = json.dumps({
            "confidence": 0.8,
            "wine": {"id": 9, "name": "Photo Wine 2020", "type": "red"},
        }).encode("utf-8")
        mock_urlopen.return_value = _fake_urlopen(payload)

        wine = self.provider.lookup_by_image(b"fausse-image-jpeg", "image/jpeg")

        self.assertIsNotNone(wine)
        self.assertEqual(wine.cuvee_nom, "Photo Wine")  # millésime retiré
        self.assertEqual(wine.couleur, "ROUGE")
        # La requête envoyée est bien un POST multipart vers /identify/image
        # contenant les octets de l'image.
        req = mock_urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/identify/image"))
        self.assertIn("multipart/form-data; boundary=", req.headers["Content-type"])
        self.assertIn(b"fausse-image-jpeg", req.data)
        self.assertIn(b'name="image"; filename="etiquette.jpg"', req.data)

    @patch("apps.catalog.enrichment.wineapi.urllib.request.urlopen")
    def test_wine_detail_with_status_lit_le_header_pending(self, mock_urlopen):
        cm = MagicMock()
        cm.__enter__.return_value.read.return_value = b'{"id": 1, "name": "X", "type": "red"}'
        cm.__enter__.return_value.headers = {"X-Update-Status": "pending", "Retry-After": "5"}
        mock_urlopen.return_value = cm

        detail, pending = self.provider.wine_detail_with_status("1")
        self.assertTrue(pending)
        self.assertEqual(detail["name"], "X")

    @override_settings(WINEAPI_ENRICH_DETAIL=True)
    @patch("apps.catalog.enrichment.wineapi.urllib.request.urlopen")
    def test_detail_pending_ecarte_du_wineapi_detail(self, mock_urlopen):
        """Un détail encore 'pending' ne doit pas être figé : on l'écarte de
        ``raw["wineapi_detail"]`` pour laisser la fiche re-fetcher plus tard."""
        identify = json.dumps(
            {"wine": {"id": 7, "name": "Cuvée", "type": "red"}, "pendingEnrichment": True}
        ).encode("utf-8")
        detail_cm = MagicMock()
        detail_cm.__enter__.return_value.read.return_value = b'{"id":7,"name":"Cuv\\u00e9e","type":"red"}'
        detail_cm.__enter__.return_value.headers = {"X-Update-Status": "pending"}
        mock_urlopen.side_effect = [_fake_urlopen(identify), detail_cm]

        wine = self.provider.lookup_by_text("Cuvée")
        self.assertTrue(wine.raw["pending"])
        self.assertIsNone(wine.raw["wineapi_detail"])


@override_settings(
    GRAPEMINDS_KEY="cle-de-test",
    GRAPEMINDS_ENABLED=True,
    GRAPEMINDS_BASE_URL="https://api.test/public/v1",
    GRAPEMINDS_TIMEOUT=5,
    GRAPEMINDS_IMAGE_TIMEOUT=9,
    GRAPEMINDS_SEARCH_LIMIT=5,
)
class GrapeMindsProviderTests(SimpleTestCase):
    """Client HTTP GrapeMinds : activation, erreurs remontées, mapping (urlopen mocké)."""

    def setUp(self):
        self.provider = GrapeMindsProvider()

    def test_enabled_exige_cle_ET_activation_explicite(self):
        # Clé + activation => actif.
        self.assertTrue(self.provider.enabled)
        # Clé seule (sans le drapeau) => inerte : garde-fou licence PSL / quota.
        with override_settings(GRAPEMINDS_ENABLED=False):
            self.assertFalse(self.provider.enabled)
        # Drapeau sans clé => inerte aussi.
        with override_settings(GRAPEMINDS_KEY=""):
            self.assertFalse(self.provider.enabled)

    @patch("apps.catalog.enrichment.grapeminds.urllib.request.urlopen")
    def test_429_leve_une_erreur_remontable(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(429)
        with self.assertRaises(EnrichmentError) as ctx:
            self.provider._request("GET", "/wines/search?q=x")
        self.assertEqual(ctx.exception.status, 429)

    @patch("apps.catalog.enrichment.grapeminds.urllib.request.urlopen")
    def test_401_leve_une_erreur_502(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(401)
        with self.assertRaises(EnrichmentError) as ctx:
            self.provider._request("GET", "/wines/1")
        self.assertEqual(ctx.exception.status, 502)  # clé invalide = erreur serveur

    @patch("apps.catalog.enrichment.grapeminds.urllib.request.urlopen")
    def test_402_403_5xx_sont_des_miss_silencieux(self, mock_urlopen):
        # 402 (abonnement), 403 (conditions/Enterprise), 500 : la cascade continue.
        for code in (402, 403, 500):
            mock_urlopen.side_effect = _http_error(code)
            self.assertIsNone(self.provider._request("GET", "/wines/1"))

    @patch("apps.catalog.enrichment.grapeminds.urllib.request.urlopen")
    def test_reseau_indisponible_est_un_miss(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("réseau coupé")
        self.assertIsNone(self.provider._request("GET", "/wines/1"))

    def test_requete_trop_courte_n_appelle_pas_l_api(self):
        # L'API exige min. 3 caractères : on court-circuite sans consommer de quota.
        with patch("apps.catalog.enrichment.grapeminds.urllib.request.urlopen") as mock_urlopen:
            self.assertIsNone(self.provider.lookup_by_text("ab"))
            mock_urlopen.assert_not_called()

    @override_settings(GRAPEMINDS_ENRICH_DETAIL=False)
    @patch("apps.catalog.enrichment.grapeminds.urllib.request.urlopen")
    def test_lookup_by_text_mappe_le_candidat(self, mock_urlopen):
        # Schéma réel : nom porté par `display_name`, enveloppe "data".
        payload = json.dumps({
            "data": [{
                "id": 9146, "display_name": "Tignanello 2019", "color": "red",
                "sub_type": "still", "producer": {"name": "Antinori"},
                "region": {"name": "Toscane", "country": "IT"}, "vintage": 2019,
            }],
        }).encode("utf-8")
        mock_urlopen.return_value = _fake_urlopen(payload)

        wine = self.provider.lookup_by_text("Tignanello")

        self.assertEqual(mock_urlopen.call_count, 1)  # pas d'appel détail
        self.assertEqual(wine.domaine_nom, "Antinori")
        self.assertEqual(wine.cuvee_nom, "Tignanello")  # millésime retiré
        self.assertEqual(wine.couleur, "ROUGE")
        self.assertEqual(wine.appellation, "Toscane")
        self.assertEqual(wine.millesime, 2019)
        self.assertEqual(wine.source, "grapeminds")
        self.assertEqual(wine.raw["pays"], "IT")
        # User-Agent applicatif explicite (GrapeMinds refuse celui d'urllib -> 403).
        req = mock_urlopen.call_args[0][0]
        self.assertIn("CavaVin", req.headers.get("User-agent", ""))

    @override_settings(GRAPEMINDS_ENRICH_DETAIL=True)
    @patch("apps.catalog.enrichment.grapeminds.urllib.request.urlopen")
    def test_lookup_enrichit_via_appel_detail(self, mock_urlopen):
        search = json.dumps({"data": [{"id": 9146, "display_name": "Tignanello"}]}).encode("utf-8")
        # Détail au schéma réel : `display_name`, `producer` objet, `description`
        # objet {text, text_long}.
        detail = json.dumps({"data": {
            "id": 9146, "display_name": "Tignanello 2019", "color": "red", "sub_type": "still",
            "producer": {"name": "Antinori"},
            "region": {"name": "Toscane", "country": "IT"},
            "grapes": [{"name": "Sangiovese"}, {"name": "Cabernet Sauvignon"}],
            "vintage": 2019, "alcohol": 13.5,
            "description": {"text": "Grand rouge toscan.", "text_long": "…", "language": "fr"},
        }}).encode("utf-8")
        # 1er appel = recherche, 2e = détail /wines/9146.
        mock_urlopen.side_effect = [_fake_urlopen(search), _fake_urlopen(detail)]

        wine = self.provider.lookup_by_text("Tignanello")

        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(wine.cuvee_nom, "Tignanello")
        self.assertEqual(wine.cepages, ["Sangiovese", "Cabernet Sauvignon"])
        self.assertEqual(wine.reference_externe_id, "9146")
        self.assertEqual(wine.raw["degre_alcool"], 13.5)
        self.assertEqual(wine.raw["description"], "Grand rouge toscan.")

    @override_settings(GRAPEMINDS_ENRICH_DETAIL=False)
    @patch("apps.catalog.enrichment.grapeminds.urllib.request.urlopen")
    def test_effervescent_prime_sur_la_couleur(self, mock_urlopen):
        payload = json.dumps({"data": [{
            "id": 1, "name": "Franciacorta", "color": "white", "sub_type": "sparkling",
        }]}).encode("utf-8")
        mock_urlopen.return_value = _fake_urlopen(payload)
        wine = self.provider.lookup_by_text("Franciacorta")
        self.assertEqual(wine.couleur, "BULLES")

    @override_settings(GRAPEMINDS_ENRICH_DETAIL=False)
    @patch("apps.catalog.enrichment.grapeminds.urllib.request.urlopen")
    def test_recherche_vide_renvoie_none(self, mock_urlopen):
        mock_urlopen.return_value = _fake_urlopen(b'{"data": []}')
        self.assertIsNone(self.provider.lookup_by_text("inconnu"))

    @patch("apps.catalog.enrichment.grapeminds.urllib.request.urlopen")
    def test_photo_desactivee_par_defaut_n_appelle_pas_l_api(self, mock_urlopen):
        # Sans l'offre Enterprise, on n'appelle pas /photo/analyze (402/403 assuré).
        with override_settings(GRAPEMINDS_PHOTO_ANALYSIS=False):
            self.assertIsNone(self.provider.lookup_by_image(b"img", "image/jpeg"))
            mock_urlopen.assert_not_called()

    @override_settings(GRAPEMINDS_PHOTO_ANALYSIS=True, GRAPEMINDS_ENRICH_DETAIL=False)
    @patch("apps.catalog.enrichment.grapeminds.urllib.request.urlopen")
    def test_photo_activee_envoie_du_json_base64(self, mock_urlopen):
        payload = json.dumps({"data": [{"id": 5, "name": "Photo Wine", "color": "red"}]}).encode("utf-8")
        mock_urlopen.return_value = _fake_urlopen(payload)

        wine = self.provider.lookup_by_image(b"fausse-image-jpeg", "image/jpeg")

        self.assertIsNotNone(wine)
        self.assertEqual(wine.couleur, "ROUGE")
        req = mock_urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/photo/analyze"))
        self.assertEqual(req.method, "POST")
        corps = json.loads(req.data.decode("utf-8"))
        self.assertTrue(corps["photo"].startswith("data:image/jpeg;base64,"))
        # La vision utilise le timeout image dédié.
        self.assertEqual(mock_urlopen.call_args.kwargs.get("timeout"), 9)


@override_settings(
    VINOU_ENABLED=True,
    VINOU_TOKEN="",
    VINOU_BASE_URL="https://api.test",
    VINOU_TIMEOUT=5,
    VINOU_SEARCH_LIMIT=5,
)
class VinouProviderTests(SimpleTestCase):
    """Client HTTP Vinou : activation, auth JWT, erreurs remontées, mapping (urlopen mocké)."""

    def setUp(self):
        cache.clear()  # évite qu'un JWT mis en cache fuite entre tests
        self.provider = VinouProvider()

    def test_enabled_suit_le_drapeau(self):
        self.assertTrue(self.provider.enabled)
        with override_settings(VINOU_ENABLED=False):
            self.assertFalse(self.provider.enabled)

    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_429_leve_une_erreur_remontable(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(429)
        with self.assertRaises(EnrichmentError) as ctx:
            self.provider.lookup_by_text("x")
        self.assertEqual(ctx.exception.status, 429)

    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_401_403_levent_une_erreur_502(self, mock_urlopen):
        for code in (401, 403):
            mock_urlopen.side_effect = _http_error(code)
            with self.assertRaises(EnrichmentError) as ctx:
                self.provider.lookup_by_text("x")
            self.assertEqual(ctx.exception.status, 502)

    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_5xx_et_reseau_sont_des_miss(self, mock_urlopen):
        mock_urlopen.side_effect = _http_error(500)
        self.assertIsNone(self.provider.lookup_by_text("x"))
        mock_urlopen.side_effect = urllib.error.URLError("réseau coupé")
        self.assertIsNone(self.provider.lookup_by_text("x"))

    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_enveloppe_non_success_renvoie_none(self, mock_urlopen):
        mock_urlopen.return_value = _fake_urlopen(b'{"info": "error", "data": null}')
        self.assertIsNone(self.provider.lookup_by_text("x"))

    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_lookup_by_text_mappe_le_candidat(self, mock_urlopen):
        payload = json.dumps({
            "info": "success",
            "data": [{
                "id": 4065, "name": "Bianca Cuvee 2019", "type": "white",
                "vintage": 2019, "alcohol": "12.0", "countrycode": "de",
                "gtin": "012345678912", "description": "Un blanc sec.",
                "winery": {"name": "Julia Heimsch", "company": "Musterweingut Vinou"},
                "prices": [{"gross": "9.52"}, {"gross": "8.33"}],
            }],
        }).encode("utf-8")
        mock_urlopen.return_value = _fake_urlopen(payload)

        wine = self.provider.lookup_by_text("Bianca")

        self.assertEqual(wine.domaine_nom, "Musterweingut Vinou")  # company prioritaire
        self.assertEqual(wine.cuvee_nom, "Bianca Cuvee")  # millésime retiré
        self.assertEqual(wine.couleur, "BLANC")
        self.assertEqual(wine.millesime, 2019)
        self.assertEqual(wine.code_barres, "012345678912")
        self.assertEqual(wine.reference_externe_id, "4065")
        self.assertEqual(wine.source, "vinou")
        self.assertEqual(wine.raw["pays"], "DE")
        self.assertEqual(wine.raw["degre_alcool"], 12.0)
        self.assertEqual(wine.raw["prix"], 8.33)  # gross le plus bas

    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_lookup_by_barcode_filtre_sur_gtin(self, mock_urlopen):
        payload = json.dumps({
            "info": "success",
            "data": [{"id": 1, "name": "Vin EAN", "type": "red"}],
        }).encode("utf-8")
        mock_urlopen.return_value = _fake_urlopen(payload)

        wine = self.provider.lookup_by_barcode("012345678912")

        self.assertIsNotNone(wine)
        self.assertEqual(wine.couleur, "ROUGE")
        # Le code-barres est conservé même si le vin renvoyé ne le répète pas.
        self.assertEqual(wine.code_barres, "012345678912")
        # La recherche par code-barres passe par `query` objet sur `gtin`
        # (Vinou rejette `filter` sur ce champ avec un 400).
        req = mock_urlopen.call_args[0][0]
        self.assertTrue(req.full_url.endswith("/wines/search"))
        corps = json.loads(req.data.decode("utf-8"))
        self.assertEqual(corps["query"]["gtin"], "012345678912")

    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_recherche_vide_renvoie_none(self, mock_urlopen):
        mock_urlopen.return_value = _fake_urlopen(b'{"info": "success", "data": []}')
        self.assertIsNone(self.provider.lookup_by_text("inconnu"))

    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_mode_public_sans_identifiants_n_envoie_pas_de_bearer(self, mock_urlopen):
        # Sans AuthID/API-Token ni jeton, les routes /wines/* sont appelées en public.
        mock_urlopen.return_value = _fake_urlopen(b'{"info": "success", "data": []}')
        self.provider.lookup_by_text("x")
        req = mock_urlopen.call_args[0][0]
        self.assertNotIn("Authorization", req.headers)

    @override_settings(VINOU_TOKEN="jwt-fourni")
    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_jwt_override_envoye_en_bearer_sans_login(self, mock_urlopen):
        mock_urlopen.return_value = _fake_urlopen(b'{"info": "success", "data": []}')
        self.provider.lookup_by_text("x")
        # Un seul appel (pas de login) et le JWT fourni est utilisé tel quel.
        self.assertEqual(mock_urlopen.call_count, 1)
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.headers["Authorization"], "Bearer jwt-fourni")

    @override_settings(VINOU_AUTH_ID="auth-42", VINOU_API_TOKEN="api-tok")
    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_login_service_puis_recherche_avec_bearer(self, mock_urlopen):
        login = b'{"info": "success", "data": {"token": "JWT-123"}}'
        search = b'{"info": "success", "data": [{"id": 7, "name": "Vin", "type": "red"}]}'
        mock_urlopen.side_effect = [_fake_urlopen(login), _fake_urlopen(search)]

        wine = self.provider.lookup_by_text("Vin")

        self.assertIsNotNone(wine)
        self.assertEqual(mock_urlopen.call_count, 2)
        # 1er appel = POST /service/login avec AuthID + API-Token.
        login_req = mock_urlopen.call_args_list[0][0][0]
        self.assertTrue(login_req.full_url.endswith("/service/login"))
        corps = json.loads(login_req.data.decode("utf-8"))
        self.assertEqual(corps, {"id": "auth-42", "token": "api-tok"})
        # 2e appel = /wines/search avec le JWT obtenu.
        search_req = mock_urlopen.call_args_list[1][0][0]
        self.assertTrue(search_req.full_url.endswith("/wines/search"))
        self.assertEqual(search_req.headers["Authorization"], "Bearer JWT-123")

    @override_settings(VINOU_AUTH_ID="auth-42", VINOU_API_TOKEN="api-tok")
    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_401_purge_le_cache_et_refait_un_login(self, mock_urlopen):
        # JWT périmé en cache : la recherche renvoie 401, on re-login et on réessaie.
        cache.set(VINOU_JWT_KEY, "JWT-PERIME", 60)
        search_ok = b'{"info": "success", "data": [{"id": 9, "name": "Vin", "type": "white"}]}'
        mock_urlopen.side_effect = [
            _http_error(401),                       # /wines/search avec JWT périmé
            _fake_urlopen(b'{"data": {"token": "JWT-NEUF"}}'),  # re-login
            _fake_urlopen(search_ok),               # /wines/search avec JWT neuf
        ]

        wine = self.provider.lookup_by_text("Vin")

        self.assertIsNotNone(wine)
        self.assertEqual(mock_urlopen.call_count, 3)
        derniere = mock_urlopen.call_args_list[2][0][0]
        self.assertEqual(derniere.headers["Authorization"], "Bearer JWT-NEUF")

    @patch("apps.catalog.enrichment.vinou.urllib.request.urlopen")
    def test_prix_repli_sur_gross_plat_en_mode_public(self, mock_urlopen):
        # En mode public, le prix n'est pas un tableau `prices` mais le champ plat
        # `gross` (le record complet est renvoyé quand même).
        payload = json.dumps({"info": "success", "data": [{
            "id": 6439, "name": "BIO Riesling trocken", "type": "white",
            "gross": "5.80", "price": "5.80", "countrycode": "de",
        }]}).encode("utf-8")
        mock_urlopen.return_value = _fake_urlopen(payload)

        wine = self.provider.lookup_by_text("Riesling")
        self.assertEqual(wine.raw["prix"], 5.80)


class StubsProviderTests(TestCase):
    """Fournisseurs stubs (Vivino, CellarTracker) : enfichés mais inertes."""

    def test_stubs_desactives_et_inertes(self):
        from .enrichment import get_all_providers, get_enabled_providers
        from .enrichment.stubs import CellarTrackerProvider, VivinoProvider

        for provider in (VivinoProvider(), CellarTrackerProvider()):
            self.assertFalse(provider.enabled)
            self.assertIsNone(provider.lookup_by_barcode("3760012345678"))
            self.assertIsNone(provider.lookup_by_text("Château X"))
            self.assertIsNone(provider.lookup_by_image(b"img", "image/jpeg"))

        # Enregistrés dans la cascade (visibles pour l'admin) mais jamais actifs.
        noms_tous = {p.name for p in get_all_providers()}
        self.assertIn("cellartracker", noms_tous)
        noms_actifs = {p.name for p in get_enabled_providers()}
        self.assertNotIn("cellartracker", noms_actifs)
        self.assertNotIn("vivino", noms_actifs)


class QuotasTests(TestCase):
    """Comptage d'usage et plafonds mensuels des sources."""

    def setUp(self):
        cache.clear()

    def test_compter_incremente_usage_du_mois(self):
        self.assertEqual(quotas.usage_mensuel("grapeminds"), 0)
        quotas.compter("grapeminds")
        quotas.compter("grapeminds")
        self.assertEqual(quotas.usage_mensuel("grapeminds"), 2)
        # Une autre source a son propre compteur.
        self.assertEqual(quotas.usage_mensuel("vinou"), 0)

    def test_plafond_defaut_et_override(self):
        self.assertEqual(quotas.plafond("grapeminds"), 250)  # défaut connu
        self.assertIsNone(quotas.plafond("vinou"))  # illimité par défaut
        set_parametre(quotas.cle_plafond("vinou"), "10")
        self.assertEqual(quotas.plafond("vinou"), 10)
        # "0" = illimité explicite, même quand un défaut existe.
        set_parametre(quotas.cle_plafond("grapeminds"), "0")
        self.assertIsNone(quotas.plafond("grapeminds"))

    def test_reste_bloque_au_plafond(self):
        set_parametre(quotas.cle_plafond("vinou"), "2")
        self.assertTrue(quotas.reste("vinou"))
        quotas.compter("vinou")
        quotas.compter("vinou")
        self.assertFalse(quotas.reste("vinou"))  # plafond atteint


class SourceToggleTests(TestCase):
    """On/off d'une source piloté depuis l'admin (override base > .env)."""

    def setUp(self):
        cache.clear()

    def test_source_activee_suit_l_override(self):
        # Sans override, on suit le défaut fourni.
        self.assertTrue(source_activee("grapeminds", True))
        self.assertFalse(source_activee("grapeminds", False))
        # L'override force l'état, quel que soit le défaut.
        definir_source_activee("grapeminds", False)
        self.assertFalse(source_activee("grapeminds", True))
        definir_source_activee("grapeminds", True)
        self.assertTrue(source_activee("grapeminds", False))


class SourcesViewTests(APITestCase):
    """API admin des sources d'identification (on/off + quota)."""

    def setUp(self):
        cache.clear()
        self.url = reverse("admin-sources")
        self.staff = User.objects.create_user("boss", password="x", is_staff=True)
        self.client.force_authenticate(self.staff)

    def test_get_liste_les_sources_avec_quota(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        par_nom = {s["source"]: s for s in resp.data["sources"]}
        self.assertIn("grapeminds", par_nom)
        self.assertIn("claude", par_nom)
        self.assertEqual(par_nom["grapeminds"]["plafond"], 250)
        self.assertEqual(par_nom["grapeminds"]["usage_mois"], 0)

    def test_put_toggle_active_une_source(self):
        resp = self.client.put(self.url, {"source": "vinou", "actif": True}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        vinou = next(s for s in resp.data["sources"] if s["source"] == "vinou")
        self.assertTrue(vinou["actif"])
        self.assertEqual(vinou["voulu"], "1")

    def test_put_regle_le_plafond(self):
        self.client.put(self.url, {"source": "grapeminds", "plafond": 100}, format="json")
        self.assertEqual(quotas.plafond("grapeminds"), 100)
        # null -> retour au défaut.
        self.client.put(self.url, {"source": "grapeminds", "plafond": None}, format="json")
        self.assertEqual(quotas.plafond("grapeminds"), 250)

    def test_source_inconnue_rejetee(self):
        resp = self.client.put(self.url, {"source": "vivino", "actif": True}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reserve_au_staff(self):
        self.client.force_authenticate(User.objects.create_user("lambda", password="x"))
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)


class CascadeParalleleTests(TestCase):
    """La cascade interroge les sources en parallèle, sans changer son contrat."""

    class _Lente:
        """Source dont le lookup dort, pour mesurer série vs parallèle."""

        def __init__(self, name, delai, wine=None, erreur=None):
            self.name, self.delai, self.wine, self.erreur = name, delai, wine, erreur

        def lookup_by_text(self, query):
            time.sleep(self.delai)
            if self.erreur:
                raise self.erreur
            return self.wine

    def setUp(self):
        cache.clear()

    def _sources(self, *sources):
        return patch("apps.catalog.views.get_enabled_providers", return_value=list(sources))

    def test_les_sources_sont_interrogees_en_parallele(self):
        delai = 0.4
        sources = [
            self._Lente(nom, delai, wine=NormalizedWine(domaine_nom="D", cuvee_nom="C", source=nom))
            for nom in ("claude", "wineapi", "lwin")
        ]
        with self._sources(*sources):
            debut = time.monotonic()
            hits, erreur = views._cascade_multi(lambda p: p.lookup_by_text("q"))
            duree = time.monotonic() - debut

        self.assertEqual(len(hits), 3)
        self.assertIsNone(erreur)
        # En série il faudrait 3 × delai ; en parallèle, un seul delai (marge
        # large pour ne pas rendre le test sensible à la charge de la CI).
        self.assertLess(duree, delai * 2, f"cascade encore sérialisée ({duree:.2f}s)")

    def test_l_ordre_de_cascade_est_preserve_malgre_l_ordre_d_arrivee(self):
        """L'ancre d'identité est la 1re source de la cascade, pas la plus rapide."""
        sources = [
            self._Lente("claude", 0.30, wine=NormalizedWine(domaine_nom="D", cuvee_nom="C", source="claude")),
            self._Lente("wineapi", 0.01, wine=NormalizedWine(domaine_nom="D", cuvee_nom="C", source="wineapi")),
            self._Lente("lwin", 0.15, wine=NormalizedWine(domaine_nom="D", cuvee_nom="C", source="lwin")),
        ]
        with self._sources(*sources):
            hits, _ = views._cascade_multi(lambda p: p.lookup_by_text("q"))
        self.assertEqual([h.source for h in hits], ["claude", "wineapi", "lwin"])

    def test_l_erreur_remontee_est_la_premiere_dans_l_ordre_de_cascade(self):
        """Déterminisme : deux sources en erreur -> toujours la même remontée."""
        sources = [
            self._Lente("claude", 0.20, erreur=EnrichmentError(429, "quota claude")),
            self._Lente("wineapi", 0.01, erreur=EnrichmentError(502, "clé wineapi")),
        ]
        with self._sources(*sources):
            hits, erreur = views._cascade_multi(lambda p: p.lookup_by_text("q"))
        self.assertEqual(hits, [])
        self.assertEqual(erreur.status, 429)  # claude est 1re dans la cascade

    def test_une_source_en_erreur_n_empeche_pas_les_autres(self):
        sources = [
            self._Lente("claude", 0.01, erreur=EnrichmentError(429, "quota")),
            self._Lente("wineapi", 0.01, wine=NormalizedWine(domaine_nom="D", cuvee_nom="C", source="wineapi")),
        ]
        with self._sources(*sources):
            hits, erreur = views._cascade_multi(lambda p: p.lookup_by_text("q"))
        self.assertEqual([h.source for h in hits], ["wineapi"])
        self.assertIsNotNone(erreur)

    def test_une_exception_inattendue_remonte_comme_avant(self):
        """Seule EnrichmentError est absorbée : le reste ne doit pas être avalé."""
        class Cassee:
            name = "cassee"

            def lookup_by_text(self, query):
                raise RuntimeError("bug provider")

        with self._sources(Cassee()):
            with self.assertRaises(RuntimeError):
                views._cascade_multi(lambda p: p.lookup_by_text("q"))

    def test_aucune_source_activee(self):
        with self._sources():
            self.assertEqual(views._cascade_multi(lambda p: p.lookup_by_text("q")), ([], None))


class ReductionImageTests(SimpleTestCase):
    """La photo envoyée aux sources distantes est réduite une fois pour toutes."""

    @staticmethod
    def _photo(largeur, hauteur, mode="RGB", fmt="JPEG", qualite=92):
        from PIL import Image

        image = Image.effect_noise((largeur, hauteur), 60).convert(mode)
        tampon = BytesIO()
        image.save(tampon, fmt, **({"quality": qualite} if fmt == "JPEG" else {}))
        return tampon.getvalue()

    def test_une_photo_de_telephone_est_fortement_allegee(self):
        from PIL import Image

        original = self._photo(4032, 3024)
        reduite, content_type = image_utils.reduire(original, "image/jpeg")

        self.assertLess(len(reduite), len(original) / 4)
        self.assertEqual(content_type, "image/jpeg")
        self.assertEqual(max(Image.open(BytesIO(reduite)).size), image_utils.TAILLE_MAX)

    def test_une_photo_deja_petite_est_laissee_telle_quelle(self):
        original = self._photo(800, 600)
        reduite, content_type = image_utils.reduire(original, "image/jpeg")
        self.assertEqual(reduite, original)
        self.assertEqual(content_type, "image/jpeg")

    def test_le_png_transparent_est_aplati_sans_fond_noir(self):
        from PIL import Image

        transparent = Image.new("RGBA", (2000, 1500), (255, 0, 0, 0))
        tampon = BytesIO()
        transparent.save(tampon, "PNG")

        reduite, content_type = image_utils.reduire(tampon.getvalue(), "image/png")
        self.assertEqual(content_type, "image/jpeg")
        # Fond blanc (et non noir) là où l'original était transparent.
        self.assertGreater(min(Image.open(BytesIO(reduite)).convert("RGB").getpixel((5, 5))), 200)

    def test_une_image_illisible_est_renvoyee_intacte(self):
        """Best-effort : jamais d'échec d'identification pour un redimensionnement."""
        self.assertEqual(image_utils.reduire(b"pas une image", "image/jpeg"),
                         (b"pas une image", "image/jpeg"))


class ScanEtiquetteChargeUtileTests(APITestCase):
    """L'OCR local lit l'original ; les sources distantes reçoivent la réduction."""

    def setUp(self):
        cache.clear()
        self.client.force_authenticate(User.objects.create_user("alice", password="x"))

    @patch("apps.catalog.views.get_enabled_providers")
    def test_chaque_source_recoit_la_charge_qui_lui_convient(self, mock_providers):
        recu = {}

        class Source:
            def __init__(self, name, pleine):
                self.name, self.image_pleine_resolution = name, pleine

            def lookup_by_image(self, data, content_type):
                recu[self.name] = len(data)
                return None

        mock_providers.return_value = [Source("claude", False), Source("lwin", True)]

        photo = ReductionImageTests._photo(3000, 2250)
        self.client.post(
            reverse("scan-etiquette"),
            {"image": SimpleUploadedFile("etiquette.jpg", photo, content_type="image/jpeg")},
            format="multipart",
        )

        self.assertEqual(recu["lwin"], len(photo))  # OCR local : pleine résolution
        self.assertLess(recu["claude"], len(photo))  # source distante : réduite


class FusionMultiSourceTests(APITestCase):
    """La cascade interroge toutes les sources activées et fusionne les relevés."""

    def setUp(self):
        cache.clear()
        self.client.force_authenticate(User.objects.create_user("alice", password="x"))

    @patch("apps.catalog.views.get_enabled_providers")
    def test_toutes_les_sources_sont_interrogees_et_observees(self, mock_providers):
        w1 = NormalizedWine(domaine_nom="Château Test", cuvee_nom="Grand Vin", couleur="ROUGE", source="claude")
        w2 = NormalizedWine(domaine_nom="Château Test", cuvee_nom="Grand Vin", appellation="Pomerol", source="wineapi")
        mock_providers.return_value = [
            _FakeProvider(wine=w1, name="claude"),
            _FakeProvider(wine=w2, name="wineapi"),
        ]
        resp = self.client.post(reverse("identifier-vin"), {"query": "Château Test Grand Vin"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["source"], "claude")  # 1re source = ancre d'identité
        cuvee = Cuvee.objects.get(nom="Grand Vin")
        canaux = set(SourceObservation.objects.filter(cuvee=cuvee).values_list("canal", flat=True))
        self.assertEqual(canaux, {"claude", "wineapi"})  # les deux sources ont laissé un relevé

    @patch("apps.catalog.views.get_enabled_providers")
    def test_source_au_plafond_est_sautee(self, mock_providers):
        set_parametre(quotas.cle_plafond("grapeminds"), "1")
        quotas.compter("grapeminds")  # usage 1 == plafond 1 -> épuisé
        appelees = []

        class Traceur(_FakeProvider):
            def lookup_by_text(self, query):
                appelees.append(self.name)
                return super().lookup_by_text(query)

        w = NormalizedWine(domaine_nom="D", cuvee_nom="C", source="claude")
        mock_providers.return_value = [
            Traceur(wine=None, name="grapeminds"),
            Traceur(wine=w, name="claude"),
        ]
        self.client.post(reverse("identifier-vin"), {"query": "abcdef"})

        self.assertNotIn("grapeminds", appelees)  # plafond atteint : non interrogée
        self.assertIn("claude", appelees)


def _reponse_claude(payload, stop_reason="end_turn"):
    """Fabrique une réponse Messages API minimale (un bloc texte JSON)."""
    bloc = MagicMock()
    bloc.type = "text"
    bloc.text = json.dumps(payload)
    reponse = MagicMock()
    reponse.stop_reason = stop_reason
    reponse.content = [bloc]
    return reponse


_VIN_CLAUDE = {
    "identifie": True,
    "confiance": 0.92,
    "vin": {
        "name": "Château Margaux 2015",
        "vintage": 2015,
        "type": "red",
        "winery": "Château Margaux",
        "region": {"name": "Bordeaux", "country": "France"},
        "appellation": "Margaux",
        "classification": "Premier Grand Cru Classé",
        "grapes": ["Cabernet Sauvignon", "Merlot"],
        "body": "Full-bodied",
        "acidity": "Medium",
        "alcoholContent": 13.5,
        "description": "Un grand vin de Margaux.",
        "pairings": [{"food": "Agneau rôti", "confidence": 0.9}],
    },
}


@override_settings(ANTHROPIC_API_KEY="cle-de-test")
# TestCase : la clé Claude est désormais résolue via un override en base (repli .env).
class ClaudeProviderTests(TestCase):
    """Provider Claude : activation, mapping structuré, erreurs (SDK mocké)."""

    def setUp(self):
        from .enrichment.claude import ClaudeProvider

        self.provider = ClaudeProvider()

    def _mock_create(self, mock_anthropic):
        return mock_anthropic.return_value.messages.create

    def test_enabled_suit_la_presence_de_cle(self):
        self.assertTrue(self.provider.enabled)
        with override_settings(ANTHROPIC_API_KEY=""):
            self.assertFalse(self.provider.enabled)

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_lookup_by_text_mappe_le_resultat(self, mock_anthropic):
        self._mock_create(mock_anthropic).return_value = _reponse_claude(_VIN_CLAUDE)

        wine = self.provider.lookup_by_text("Margaux 2015")

        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Château Margaux")
        self.assertEqual(wine.cuvee_nom, "Château Margaux")  # millésime retiré
        self.assertEqual(wine.couleur, "ROUGE")
        self.assertEqual(wine.appellation, "Margaux")
        self.assertEqual(wine.millesime, 2015)
        self.assertEqual(wine.cepages, ["Cabernet Sauvignon", "Merlot"])
        self.assertEqual(wine.source, "claude")
        # Pas de source distante : la fiche ne proposera pas de re-synchro.
        self.assertEqual(wine.reference_externe_id, "")
        self.assertEqual(wine.raw["confidence"], 0.92)
        self.assertEqual(wine.raw["pays"], "France")
        # Le détail (format wineapi) est transmis pour persistance par upsert_cuvee.
        self.assertEqual(wine.raw["wineapi_detail"]["body"], "Full-bodied")
        # Les données volatiles ne sont jamais inventées par le modèle.
        self.assertIsNone(wine.raw["prix"])
        self.assertIsNone(wine.raw["note"])

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_detail_persiste_alimente_la_cuvee(self, mock_anthropic):
        """Le détail Claude traverse normalize_detail comme un détail wineapi."""
        self._mock_create(mock_anthropic).return_value = _reponse_claude(_VIN_CLAUDE)
        wine = self.provider.lookup_by_text("Margaux")
        flat = wine_profile.normalize_detail(wine.raw["wineapi_detail"])
        self.assertEqual(flat["region"], "Bordeaux")
        self.assertEqual(flat["classification"], "Premier Grand Cru Classé")
        self.assertEqual(flat["corps"], "Full-bodied")
        self.assertEqual(flat["degre_alcool"], 13.5)
        self.assertEqual(flat["accords"][0]["nom"], "Agneau rôti")
        self.assertEqual(flat["cepages"], ["Cabernet Sauvignon", "Merlot"])

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_lookup_by_image_envoie_l_image_en_base64(self, mock_anthropic):
        create = self._mock_create(mock_anthropic)
        create.return_value = _reponse_claude(_VIN_CLAUDE)

        wine = self.provider.lookup_by_image(b"fausse-image-jpeg", "image/jpeg")

        self.assertIsNotNone(wine)
        blocs = create.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(blocs[0]["type"], "image")
        self.assertEqual(blocs[0]["source"]["media_type"], "image/jpeg")
        import base64 as b64

        self.assertEqual(b64.b64decode(blocs[0]["source"]["data"]), b"fausse-image-jpeg")

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_content_type_inconnu_retombe_sur_jpeg(self, mock_anthropic):
        create = self._mock_create(mock_anthropic)
        create.return_value = _reponse_claude(_VIN_CLAUDE)
        self.provider.lookup_by_image(b"img", "application/octet-stream")
        blocs = create.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(blocs[0]["source"]["media_type"], "image/jpeg")

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_non_identifie_renvoie_none(self, mock_anthropic):
        self._mock_create(mock_anthropic).return_value = _reponse_claude(
            {"identifie": False, "confiance": None, "vin": None}
        )
        self.assertIsNone(self.provider.lookup_by_text("blabla sans rapport"))

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_refus_du_modele_est_un_miss(self, mock_anthropic):
        reponse = MagicMock()
        reponse.stop_reason = "refusal"
        reponse.content = []
        self._mock_create(mock_anthropic).return_value = reponse
        self.assertIsNone(self.provider.lookup_by_text("x"))

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_json_invalide_est_un_miss(self, mock_anthropic):
        reponse = MagicMock()
        reponse.stop_reason = "end_turn"
        bloc = MagicMock()
        bloc.type = "text"
        bloc.text = "pas du json"
        reponse.content = [bloc]
        self._mock_create(mock_anthropic).return_value = reponse
        self.assertIsNone(self.provider.lookup_by_text("x"))

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_429_leve_une_erreur_remontable(self, mock_anthropic):
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        self._mock_create(mock_anthropic).side_effect = anthropic.RateLimitError(
            "quota", response=httpx.Response(429, request=req), body=None
        )
        with self.assertRaises(EnrichmentError) as ctx:
            self.provider.lookup_by_text("x")
        self.assertEqual(ctx.exception.status, 429)

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_401_leve_une_erreur_502(self, mock_anthropic):
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        self._mock_create(mock_anthropic).side_effect = anthropic.AuthenticationError(
            "clé invalide", response=httpx.Response(401, request=req), body=None
        )
        with self.assertRaises(EnrichmentError) as ctx:
            self.provider.lookup_by_text("x")
        self.assertEqual(ctx.exception.status, 502)  # clé invalide = erreur serveur

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_5xx_est_un_miss_silencieux(self, mock_anthropic):
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        self._mock_create(mock_anthropic).side_effect = anthropic.InternalServerError(
            "boom", response=httpx.Response(500, request=req), body=None
        )
        self.assertIsNone(self.provider.lookup_by_text("x"))

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_reseau_indisponible_est_un_miss(self, mock_anthropic):
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        self._mock_create(mock_anthropic).side_effect = anthropic.APIConnectionError(request=req)
        self.assertIsNone(self.provider.lookup_by_text("x"))

    @patch("apps.catalog.enrichment.claude.anthropic.Anthropic")
    def test_vin_sans_nom_ni_domaine_est_un_miss(self, mock_anthropic):
        payload = {
            "identifie": True,
            "confiance": 0.1,
            "vin": {**_VIN_CLAUDE["vin"], "name": "", "winery": ""},
        }
        self._mock_create(mock_anthropic).return_value = _reponse_claude(payload)
        self.assertIsNone(self.provider.lookup_by_text("x"))


class LwinProviderTests(TestCase):
    """Repli local (OCR Tesseract + référentiel LWIN) : correspondance floue,
    tolérance aux coquilles d'OCR, pondération par rareté, OCR mocké."""

    def setUp(self):
        from .enrichment import lwin as module_lwin
        from .enrichment.lwin import LwinProvider

        # Le référentiel est mis en cache au niveau module : on repart à neuf.
        module_lwin._cache = {"version": None, "refs": [], "idf": {}, "postings": {}, "vocab": []}
        module_lwin._langues_ok = True  # mémo du pack fra+eng, remis à neuf
        self.provider = LwinProvider()
        ReferenceLwin.objects.create(
            lwin="1011247", producteur="Château Margaux", pays="France",
            region="Bordeaux", sous_region="Margaux", couleur="ROUGE",
            classification="Premier Cru Classé",
        )
        ReferenceLwin.objects.create(
            lwin="1011248", producteur="Château Palmer", pays="France",
            region="Bordeaux", sous_region="Margaux", couleur="ROUGE",
        )
        # Second vin du même château : rend « margaux » plus fréquent que
        # « palmer » dans le corpus (pondération IDF).
        ReferenceLwin.objects.create(
            lwin="1011249", producteur="Château Margaux",
            vin="Pavillon Rouge du Château Margaux", pays="France",
            region="Bordeaux", sous_region="Margaux", couleur="ROUGE",
        )
        ReferenceLwin.objects.create(
            lwin="1017842", producteur="Domaine de la Romanée-Conti", vin="La Tâche",
            pays="France", region="Bourgogne", couleur="ROUGE",
        )
        # Pièges observés sur le dump réel : proches en correspondance floue
        # (« Taches » vs « Tâche », « Palmier » vs « Palmer »), un match exact
        # doit toujours l'emporter.
        ReferenceLwin.objects.create(
            lwin="1357059", producteur="Robert Denogent", vin="Taches",
            pays="France", region="Bourgogne", sous_region="Mâcon", couleur="BLANC",
        )
        ReferenceLwin.objects.create(
            lwin="2212733", producteur="Laurent Ponsot", vin="Cuvée du Palmier",
            pays="France", region="Bourgogne", couleur="ROUGE",
        )

    def test_enabled_suit_le_reglage(self):
        self.assertTrue(self.provider.enabled)
        with override_settings(LWIN_ENABLED=False):
            self.assertFalse(self.provider.enabled)

    def test_lookup_by_text_correspond(self):
        wine = self.provider.lookup_by_text("chateau margaux 2015")
        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Château Margaux")
        self.assertEqual(wine.couleur, "ROUGE")
        self.assertEqual(wine.appellation, "Margaux")  # sous-région LWIN
        self.assertEqual(wine.millesime, 2015)
        self.assertEqual(wine.source, "lwin")
        self.assertEqual(wine.reference_externe_id, "")
        self.assertEqual(wine.raw["wineapi_detail"]["lwinCode"], "1011247")
        self.assertEqual(wine.raw["pays"], "France")

    def test_le_nom_du_vin_suffit_sans_le_producteur(self):
        wine = self.provider.lookup_by_text("la tâche 1990")
        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Domaine de la Romanée-Conti")
        self.assertEqual(wine.cuvee_nom, "La Tâche")
        self.assertEqual(wine.millesime, 1990)

    def test_coquille_ocr_toleree(self):
        # « Margeaux » : faute fréquente / erreur d'OCR, plus bruit d'étiquette.
        wine = self.provider.lookup_by_text(
            "GRAND VIN DE CHATEAU MARGEAUX PREMIER GRAND CRU CLASSE 1998 MIS EN BOUTEILLE"
        )
        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Château Margaux")
        self.assertEqual(wine.millesime, 1998)

    def test_etiquette_ambigue_prefere_le_token_rare(self):
        """Une étiquette Palmer mentionne aussi sa commune (Margaux) : la
        pondération IDF doit préférer « palmer » (rare) à « margaux » (fréquent)."""
        wine = self.provider.lookup_by_text("Chateau Palmer Margaux 1998")
        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Château Palmer")

    def test_ligatures_normalisees(self):
        """« Cœur » doit se tokeniser en « coeur » (la ligature œ n'a pas de
        décomposition NFKD) pour correspondre à une saisie sans ligature."""
        ReferenceLwin.objects.create(
            lwin="3000001", producteur="Vollereaux", vin="Cœur de Cuvée",
            pays="France", region="Champagne", couleur="BULLES",
        )
        wine = self.provider.lookup_by_text("vollereaux coeur de cuvee 2014")
        self.assertIsNotNone(wine)
        self.assertEqual(wine.cuvee_nom, "Cœur de Cuvée")

    def test_nom_generique_exige_le_producteur(self):
        """Un vin nommé d'après son seul cépage (« Riesling ») ne doit pas
        correspondre sans son producteur — sinon « chardonnay » seul
        renverrait le chardonnay d'un producteur au hasard."""
        ReferenceLwin.objects.create(
            lwin="3000002", producteur="Trimbach", vin="Riesling",
            pays="France", region="Alsace", couleur="BLANC",
        )
        self.assertIsNone(self.provider.lookup_by_text("riesling 2020"))
        self.assertIsNone(self.provider.lookup_by_text("brut rosé"))
        wine = self.provider.lookup_by_text("trimbach riesling 2020")
        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Trimbach")

    def test_suggestions_renvoient_les_candidats_proches(self):
        """Quand plusieurs références restent plausibles, les suivantes sont
        renvoyées en suggestions (même contrat que wineapi)."""
        wine = self.provider.lookup_by_text("pavillon rouge du chateau margaux 2016")
        self.assertIsNotNone(wine)
        self.assertEqual(wine.cuvee_nom, "Pavillon Rouge du Château Margaux")
        # Le grand vin, candidat plausible (« margaux » retrouvé), est suggéré.
        self.assertTrue(any("Château Margaux" in s for s in wine.raw["suggestions"]))

    def test_climat_bourgogne_en_sous_region_departage(self):
        """Sur le dump réel, les vins de Bourgogne d'un même domaine partagent
        le nom du producteur (vin vide) et se distinguent par la sous-région
        (climat) : elle doit départager sans jamais être exigée."""
        ReferenceLwin.objects.create(
            lwin="2000001", producteur="Domaine Fictif de Vosne", vin="",
            sous_region="Echezeaux", region="Bourgogne", couleur="ROUGE",
        )
        ReferenceLwin.objects.create(
            lwin="2000002", producteur="Domaine Fictif de Vosne", vin="",
            sous_region="Malconsorts", region="Bourgogne", couleur="ROUGE",
        )
        wine = self.provider.lookup_by_text("domaine fictif de vosne malconsorts 2019")
        self.assertEqual(wine.raw["wineapi_detail"]["lwinCode"], "2000002")
        self.assertEqual(wine.appellation, "Malconsorts")
        # Sans mention du climat, le domaine correspond quand même (au 1er climat).
        self.assertIsNotNone(self.provider.lookup_by_text("domaine fictif de vosne"))

    def test_texte_sans_token_significatif_est_un_miss(self):
        self.assertIsNone(self.provider.lookup_by_text("grand vin de france"))

    def test_aucune_correspondance_est_un_miss(self):
        self.assertIsNone(self.provider.lookup_by_text("Screaming Eagle Napa"))

    @staticmethod
    def _tsv(*mots_conf) -> bytes:
        """Fabrique une sortie TSV tesseract minimale [(mot, confiance), ...]."""
        lignes = ["level\tpage\tblock\tpar\tline\tword\tleft\ttop\twidth\theight\tconf\ttext"]
        for mot, conf in mots_conf:
            lignes.append(f"5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t{conf}\t{mot}")
        return "\n".join(lignes).encode()

    @patch("apps.catalog.enrichment.lwin.subprocess.run")
    def test_lookup_by_image_ocr_puis_correspondance(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self._tsv(("Chateau", 91), ("Palmer", 88), ("Margaux", 90), ("1998", 95)),
        )
        wine = self.provider.lookup_by_image(b"fausse-image", "image/jpeg")
        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Château Palmer")
        self.assertEqual(wine.millesime, 1998)
        # Octets illisibles par Pillow : repli sur l'image brute, passée sur stdin.
        premier = mock_run.call_args_list[0]
        self.assertEqual(premier.kwargs["input"], b"fausse-image")
        args = premier.args[0]
        self.assertIn("tesseract", args[0])
        self.assertIn("--psm", args)
        self.assertIn("tsv", args)

    @patch("apps.catalog.enrichment.lwin.subprocess.run")
    def test_ocr_illisible_prefere_un_miss_a_un_vin_douteux(self, mock_run):
        """Une photo illisible fait halluciner à Tesseract des petits mots qui
        peuvent matcher une référence exotique (« tel » -> « Pa-Tel ») : en
        mode OCR, un unique token court ne suffit pas à identifier un vin."""
        ReferenceLwin.objects.create(lwin="2371045", producteur="Maturana", vin="Pa-Tel")
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self._tsv(("cae", 70), ("tel", 78), ("pit", 68), ("fed", 72)),
        )
        self.assertIsNone(self.provider.lookup_by_image(b"img", "image/jpeg"))
        # La même référence reste trouvable par une saisie humaine.
        wine = self.provider.lookup_by_text("maturana pa-tel")
        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Maturana")

    @patch("apps.catalog.enrichment.lwin.subprocess.run")
    def test_passe_ocr_a_confiance_moyenne_faible_ecartee_en_bloc(self, mock_run):
        """Sur une photo illisible, le mode texte épars hallucine des mots à
        confiance moyenne ~50 : la passe entière doit être écartée, même si
        un vrai nom s'y glisse."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self._tsv(("Margaux", 55), ("bers", 48), ("tear", 52), ("fits", 41)),
        )
        self.assertIsNone(self.provider.lookup_by_image(b"img", "image/jpeg"))

    @patch("apps.catalog.enrichment.lwin.subprocess.run")
    def test_ocr_ecarte_les_mots_de_faible_confiance(self, mock_run):
        # « Palmer » douteux (conf < 40) : il ne doit pas atteindre la
        # correspondance floue, sinon n'importe quel bruit d'OCR matcherait.
        mock_run.return_value = MagicMock(
            returncode=0, stdout=self._tsv(("Palmer", 12), ("Margaux", 91))
        )
        wine = self.provider.lookup_by_image(b"img", "image/jpeg")
        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Château Margaux")  # pas Palmer

    def test_variantes_pretraite_et_inverse_les_etiquettes_sombres(self):
        from PIL import Image
        from io import BytesIO

        from .enrichment.lwin import _variantes

        def png(couleur) -> bytes:
            tampon = BytesIO()
            Image.new("RGB", (200, 100), couleur).save(tampon, "PNG")
            return tampon.getvalue()

        claire = _variantes(png("ivory"))
        sombre = _variantes(png("black"))
        self.assertEqual(len(claire), 1)  # étiquette claire : pas d'inversion
        self.assertEqual(len(sombre), 2)  # étiquette sombre : + variante inversée
        # Les variantes sont des PNG pré-traités, pas les octets d'origine.
        self.assertTrue(all(v.startswith(b"\x89PNG") for v in claire + sombre))
        # Octets illisibles : repli sur l'image brute.
        self.assertEqual(_variantes(b"pas-une-image"), [b"pas-une-image"])

    @patch("apps.catalog.enrichment.lwin.subprocess.run")
    def test_tesseract_absent_est_un_miss(self, mock_run):
        mock_run.side_effect = FileNotFoundError("tesseract")
        self.assertIsNone(self.provider.lookup_by_image(b"img", "image/jpeg"))

    @patch("apps.catalog.enrichment.lwin.subprocess.run")
    def test_ocr_timeout_est_un_miss(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tesseract", timeout=20)
        self.assertIsNone(self.provider.lookup_by_image(b"img", "image/jpeg"))

    @patch("apps.catalog.enrichment.lwin.subprocess.run")
    def test_ocr_replie_sur_la_langue_par_defaut(self, mock_run):
        # Pack fra absent : 1er essai en échec, puis toutes les passes suivantes
        # repartent sans -l (mémo module), sans nouvel essai voué à l'échec.
        ok = MagicMock(returncode=0, stdout=self._tsv(("Chateau", 90), ("Palmer", 90), ("1998", 95)))
        osd = MagicMock(returncode=0, stdout=b"Rotate: 0\n")
        mock_run.side_effect = [
            MagicMock(returncode=1, stderr=b"Error opening data file fra"),
            ok, ok, osd,
        ]
        wine = self.provider.lookup_by_image(b"img", "image/jpeg")
        self.assertIsNotNone(wine)
        for appel in mock_run.call_args_list[1:]:
            self.assertNotIn("-l", appel.args[0])

    def test_zone_texte_localise_l_etiquette(self):
        from .enrichment.lwin import _zone_texte

        tsv = "\n".join([
            "level\tpage\tblock\tpar\tline\tword\tleft\ttop\twidth\theight\tconf\ttext",
            "1\t1\t0\t0\t0\t0\t0\t0\t1000\t1000\t-1\t",
            "5\t1\t1\t1\t1\t1\t400\t450\t80\t30\t90\tPalmer",
            "5\t1\t1\t1\t1\t2\t500\t450\t90\t30\t88\tMargaux",
            "5\t1\t1\t1\t1\t3\t100\t100\t60\t25\t20\tjunk",  # conf < 40 : ignoré
        ])
        # Union des mots confiants + marge de 8 % de la page.
        self.assertEqual(_zone_texte([tsv]), (320, 370, 670, 560))
        # Texte couvrant déjà le cadre : rien à gagner au recadrage.
        plein = tsv.replace("400\t450\t80\t30", "0\t0\t900\t900")
        self.assertIsNone(_zone_texte([plein]))
        # Aucun TSV, ou aucun mot confiant : pas de zone.
        self.assertIsNone(_zone_texte([]))
        self.assertIsNone(_zone_texte([tsv.splitlines()[0]]))

    @patch("apps.catalog.enrichment.lwin.subprocess.run")
    def test_osd_detecte_la_rotation(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=b"Page number: 0\nOrientation in degrees: 270\nRotate: 90\n"
        )
        self.assertEqual(self.provider._osd_rotation(b"img"), 90)
        appel = mock_run.call_args.args[0]
        self.assertIn("0", appel)  # --psm 0
        self.assertNotIn("-l", appel)  # l'OSD utilise osd.traineddata, pas fra+eng
        # Échec OSD (image trop pauvre, pack absent) : on ne redresse pas.
        mock_run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"Too few characters")
        self.assertEqual(self.provider._osd_rotation(b"img"), 0)

    @patch("apps.catalog.enrichment.lwin.subprocess.run")
    def test_photo_tournee_sans_exif_redressee_par_osd(self, mock_run):
        """Photo pivotée sans EXIF : la phase 1 ne lit rien, l'OSD détecte la
        rotation et les passes sur l'image redressée identifient le vin."""
        vide = MagicMock(returncode=0, stdout=self._tsv())
        osd = MagicMock(returncode=0, stdout=b"Rotate: 90\n")
        ok = MagicMock(
            returncode=0,
            stdout=self._tsv(("Chateau", 91), ("Palmer", 90), ("Margaux", 89), ("1998", 95)),
        )
        mock_run.side_effect = [vide, vide, osd, ok, ok]
        wine = self.provider.lookup_by_image(b"img", "image/jpeg")
        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Château Palmer")

    @patch("apps.catalog.enrichment.lwin.subprocess.run")
    def test_etiquette_petite_recadree_puis_relue(self, mock_run):
        """Bouteille loin dans le cadre : la phase 1 ne lit qu'un mot minuscule,
        mais sa boîte localise l'étiquette — le recadrage en pleine résolution
        la relit et l'identification aboutit (l'esprit WineNot)."""
        loin = MagicMock(returncode=0, stdout="\n".join([
            "level\tpage\tblock\tpar\tline\tword\tleft\ttop\twidth\theight\tconf\ttext",
            "1\t1\t0\t0\t0\t0\t0\t0\t1000\t1000\t-1\t",
            "5\t1\t1\t1\t1\t1\t450\t480\t70\t22\t85\tPalmer",
        ]).encode())
        osd = MagicMock(returncode=0, stdout=b"Rotate: 0\n")
        ok = MagicMock(
            returncode=0,
            stdout=self._tsv(("Chateau", 91), ("Palmer", 90), ("Margaux", 89), ("1998", 95)),
        )
        mock_run.side_effect = [loin, loin, osd, ok, ok]
        wine = self.provider.lookup_by_image(b"img", "image/jpeg")
        self.assertIsNotNone(wine)
        self.assertEqual(wine.domaine_nom, "Château Palmer")
        self.assertEqual(wine.millesime, 1998)


class ImportLwinCommandTests(TestCase):
    """Commande import_lwin : import du dump XLSX/CSV, filtrage, idempotence."""

    # Colonnes du dump Liv-ex réel (sous-ensemble utile) : l'effervescence est
    # portée par SUB_TYPE, TYPE valant toujours « Wine ».
    _COLONNES = [
        "LWIN", "STATUS", "DISPLAY_NAME", "PRODUCER_TITLE", "PRODUCER_NAME",
        "WINE", "COUNTRY", "REGION", "SUB_REGION", "COLOUR", "TYPE",
        "SUB_TYPE", "CLASSIFICATION",
    ]

    def _importer_csv(self, lignes: list[str]) -> str:
        contenu = ",".join(self._COLONNES) + "\n" + "".join(lignes)
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write(contenu)
            chemin = f.name
        try:
            sortie = StringIO()
            call_command("import_lwin", chemin, stdout=sortie)
            return sortie.getvalue()
        finally:
            os.unlink(chemin)

    def test_import_filtre_et_mappe(self):
        self._importer_csv([
            "1011247,Live,Chateau Margaux,Chateau,Margaux,,France,Bordeaux,Margaux,Red,Wine,Still,1er Cru\n",
            "1099999,Deleted,Vin Supprimé,Chateau,Disparu,,France,,,Red,Wine,Still,\n",
            ",Live,Sans Code,X,Y,,,,,,,,\n",
            "1055555,Live,Bollinger,,Bollinger,Grande Année,France,Champagne,,White,Wine,Sparkling,\n",
        ])
        self.assertEqual(ReferenceLwin.objects.count(), 2)  # Deleted et sans LWIN écartés
        margaux = ReferenceLwin.objects.get(lwin="1011247")
        self.assertEqual(margaux.producteur, "Chateau Margaux")
        self.assertEqual(margaux.sous_region, "Margaux")
        self.assertEqual(margaux.couleur, "ROUGE")
        self.assertEqual(margaux.classification, "1er Cru")
        bollinger = ReferenceLwin.objects.get(lwin="1055555")
        self.assertEqual(bollinger.vin, "Grande Année")
        self.assertEqual(bollinger.couleur, "BULLES")  # sparkling (SUB_TYPE) prime sur white

    def test_reimport_met_a_jour_sans_doublonner(self):
        self._importer_csv([
            "1011247,Live,Chateau Margaux,Chateau,Margaux,,France,Bordeaux,Margaux,Red,Wine,Still,\n"
        ])
        self._importer_csv([
            "1011247,Live,Chateau Margaux,Chateau,Margaux,,France,Bordeaux,Margaux AOC,Red,Wine,Still,\n"
        ])
        self.assertEqual(ReferenceLwin.objects.count(), 1)
        self.assertEqual(ReferenceLwin.objects.get(lwin="1011247").sous_region, "Margaux AOC")

    def test_import_xlsx_normalise_na_et_codes_numeriques(self):
        """Le dump Liv-ex d'origine (XLSX) est accepté tel quel : codes LWIN en
        nombres flottants et absences encodées « NA » sont normalisés."""
        import openpyxl

        classeur = openpyxl.Workbook()
        feuille = classeur.active
        feuille.append(self._COLONNES)
        feuille.append([
            1011247.0, "Live", "Chateau Margaux", "NA", "Chateau Margaux", "NA",
            "France", "Bordeaux", "Margaux", "Red", "Wine", "Still", "NA",
        ])
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            chemin = f.name
        classeur.save(chemin)
        try:
            call_command("import_lwin", chemin, stdout=StringIO())
        finally:
            os.unlink(chemin)

        ref = ReferenceLwin.objects.get()
        self.assertEqual(ref.lwin, "1011247")  # 1011247.0 -> "1011247"
        self.assertEqual(ref.producteur, "Chateau Margaux")  # « NA » ignoré, pas préfixé
        self.assertEqual(ref.vin, "")
        self.assertEqual(ref.classification, "")
        self.assertEqual(ref.couleur, "ROUGE")

    def test_fichier_absent_leve_une_erreur(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("import_lwin", "/chemin/inexistant.csv")
        with self.assertRaises(CommandError):
            call_command("import_lwin", "/chemin/inexistant.xlsx")


class EnsureSuperuserCommandTests(TestCase):
    """Commande ensure_superuser : création depuis l'environnement, idempotente."""

    @patch.dict(os.environ, {
        "DJANGO_SUPERUSER_USERNAME": "boss",
        "DJANGO_SUPERUSER_PASSWORD": "un-mot-de-passe",
        "DJANGO_SUPERUSER_EMAIL": "boss@example.com",
    })
    def test_cree_le_superuser_puis_idempotent(self):
        User = get_user_model()
        call_command("ensure_superuser", stdout=StringIO())

        u = User.objects.get(username="boss")
        self.assertTrue(u.is_superuser)
        self.assertTrue(u.is_staff)
        self.assertEqual(u.email, "boss@example.com")

        # Second appel : ni doublon, ni erreur.
        call_command("ensure_superuser", stdout=StringIO())
        self.assertEqual(User.objects.filter(username="boss").count(), 1)

    @patch.dict(os.environ, {"DJANGO_SUPERUSER_USERNAME": "", "DJANGO_SUPERUSER_PASSWORD": ""})
    def test_sans_variables_ne_cree_rien(self):
        call_command("ensure_superuser", stdout=StringIO())
        self.assertFalse(get_user_model().objects.filter(is_superuser=True).exists())


class SommellerieTests(SimpleTestCase):
    """Conseils de dégustation dérivés de la couleur — logique pure, sans BDD."""

    def test_conseil_par_couleur(self):
        rouge = sommellerie.conseil_pour_couleur("ROUGE")
        self.assertEqual(rouge.temperature, "16-18")
        self.assertEqual(rouge.carafage, "1h-2h")
        self.assertEqual(len(rouge.gustatif), 3)
        self.assertTrue(rouge.accords)

        blanc = sommellerie.conseil_pour_couleur("BLANC")
        self.assertEqual(blanc.carafage, "Non requis")

    def test_couleur_inconnue_retombe_sur_autre(self):
        inconnu = sommellerie.conseil_pour_couleur("MAUVE")
        autre = sommellerie.conseil_pour_couleur("AUTRE")
        self.assertEqual(inconnu, autre)

    def test_axes_gustatifs_bornes(self):
        for couleur in ("ROUGE", "BLANC", "ROSE", "BULLES", "AUTRE"):
            for axe in sommellerie.conseil_pour_couleur(couleur).gustatif:
                self.assertGreaterEqual(axe.valeur, 0)
                self.assertLessEqual(axe.valeur, 1)


class FicheCuveeTests(APITestCase):
    """Endpoint /api/cuvees/{id}/fiche/ : conseil public + stock par utilisateur."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.domaine = Domaine.objects.create(nom="Château Cantemerle")
        self.cuvee = Cuvee.objects.create(
            domaine=self.domaine,
            nom="Grand Cru Classé",
            appellation="Haut-Médoc",
            couleur=Cuvee.Couleur.ROUGE,
        )
        self.cuvee.cepages.add(
            Cepage.objects.create(nom="Cabernet Sauvignon"),
            Cepage.objects.create(nom="Merlot"),
        )
        self.url = reverse("cuvee-fiche", args=[self.cuvee.pk])

    def _ajoute_bouteille(self, **kwargs):
        from apps.inventory.models import Bouteille

        defaults = dict(proprietaire=self.user, cuvee=self.cuvee, quantite=1)
        defaults.update(kwargs)
        return Bouteille.objects.create(**defaults)

    def test_conseil_public_sans_authentification(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["conseil_degustation"]["temperature"], "16-18")
        self.assertEqual(resp.data["cuvee"]["cepages"], ["Cabernet Sauvignon", "Merlot"])
        # Aucune donnée privée pour un visiteur anonyme.
        self.assertIsNone(resp.data["prix_achat_moyen"])
        self.assertEqual(resp.data["millesimes"], [])

    def test_prix_moyen_pondere_et_millesimes(self):
        self._ajoute_bouteille(millesime=2019, quantite=10, prix_achat="20.00")
        self._ajoute_bouteille(millesime=2019, quantite=2, prix_achat="50.00")
        self._ajoute_bouteille(millesime=2016, quantite=3, prix_achat=None)
        self.client.force_authenticate(self.user)

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # (10*20 + 2*50) / 12 = 25.00 ; la ligne sans prix est ignorée.
        self.assertEqual(str(resp.data["prix_achat_moyen"]), "25.00")
        self.assertEqual(resp.data["stock_total"], 15)
        # Millésimes regroupés et triés du plus récent au plus ancien.
        millesimes = resp.data["millesimes"]
        self.assertEqual([m["millesime"] for m in millesimes], [2019, 2016])
        self.assertEqual(millesimes[0]["quantite"], 12)

    def test_stock_cloisonne_par_utilisateur(self):
        bob = User.objects.create_user(username="bob", password="x")
        self._ajoute_bouteille(millesime=2019, quantite=5, prix_achat="30.00")
        self.client.force_authenticate(bob)

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Bob ne voit pas le stock d'Alice.
        self.assertEqual(resp.data["millesimes"], [])
        self.assertIsNone(resp.data["prix_achat_moyen"])


# Détail wineapi.io d'exemple (sous-ensemble de GET /wines/{id}).
_WINEAPI_DETAIL = {
    "body": "Full-bodied",
    "acidity": "Medium",
    "averageRating": 3.9,
    "ratingsCount": 26,
    "priceRange": {"min": 38, "max": 65, "currency": "EUR"},
    "pairings": [
        {"food": "Beef", "confidence": 0.95, "notes": "Grillé"},
        {"food": "Lamb", "confidence": 0.9, "notes": None},
    ],
    "scores": [
        {"score": 92, "scoreText": "Excellent", "reviewer": "Wine Critic", "reviewDate": "2020-01-01"},
        {"score": None, "scoreText": None, "reviewer": None},  # ignoré (pas de reviewer)
    ],
}


class WineProfileTests(SimpleTestCase):
    """Mapping wineapi.io -> champs de fiche — fonctions pures, sans réseau."""

    def test_food_emoji(self):
        self.assertEqual(wine_profile.food_emoji("Grilled Beef"), "🥩")
        self.assertEqual(wine_profile.food_emoji("Poisson grillé"), "🐟")
        self.assertEqual(wine_profile.food_emoji("Ovni"), "🍽️")

    def test_profil_gustatif_utilise_corps_et_acidite(self):
        defaut = [
            {"gauche": "Léger", "droite": "Puissant", "valeur": 0.1},
            {"gauche": "Souple", "droite": "Tannique", "valeur": 0.2},
            {"gauche": "Doux", "droite": "Acide", "valeur": 0.3},
        ]
        axes = wine_profile.profil_gustatif(_WINEAPI_DETAIL, defaut)
        self.assertEqual(axes[0]["valeur"], 0.85)  # Full-bodied
        self.assertEqual(axes[1]["valeur"], 0.2)  # tanin : conserve le défaut
        self.assertEqual(axes[2]["valeur"], 0.5)  # acidité Medium

    def test_profil_gustatif_repli_si_rien(self):
        defaut = [{"gauche": "Léger", "droite": "Puissant", "valeur": 0.1}]
        self.assertIs(wine_profile.profil_gustatif({}, defaut), defaut)

    def test_accords_tries_par_confiance(self):
        accords = wine_profile.accords_mets(_WINEAPI_DETAIL)
        self.assertEqual([a["nom"] for a in accords], ["Beef", "Lamb"])
        self.assertEqual(accords[0]["emoji"], "🥩")
        self.assertEqual(accords[0]["confiance"], 0.95)
        self.assertIsNone(wine_profile.accords_mets({}))

    def test_note_communaute_ramenee_sur_5(self):
        self.assertEqual(wine_profile.note_communaute(_WINEAPI_DETAIL), {"note": 3.9, "nb": 26})
        self.assertEqual(wine_profile.note_communaute({"averageRating": 92})["note"], 4.6)
        self.assertIsNone(wine_profile.note_communaute({}))

    def test_avis_ignore_sans_reviewer(self):
        avis = wine_profile.avis_critiques(_WINEAPI_DETAIL)
        self.assertEqual(len(avis), 1)
        self.assertEqual(avis[0]["reviewer"], "Wine Critic")
        self.assertEqual(avis[0]["score"], 92)

    def test_prix_marche(self):
        self.assertEqual(
            wine_profile.prix_marche(_WINEAPI_DETAIL), {"min": 38, "max": 65, "devise": "EUR"}
        )
        self.assertIsNone(wine_profile.prix_marche({"priceRange": {"min": 10}}))

    def test_prix_marchands_tries_et_filtres(self):
        offres = wine_profile.prix_marchands(_WINEAPI_FULL)
        # Triés par prix croissant ; l'offre sans prix est ignorée.
        self.assertEqual([o["marchand"] for o in offres], ["Cave A", "Cave B"])
        self.assertEqual(offres[0]["prix"], 42.5)
        self.assertEqual(offres[0]["devise"], "EUR")
        self.assertEqual(offres[0]["url"], "")
        self.assertEqual(wine_profile.prix_marchands({}), [])


class FicheEnrichmentTests(APITestCase):
    """L'endpoint fiche fusionne le détail wineapi quand le vin est identifié."""

    def setUp(self):
        self.domaine = Domaine.objects.create(nom="Château Cantemerle")
        self.cuvee = Cuvee.objects.create(
            domaine=self.domaine,
            nom="Grand Cru Classé",
            couleur=Cuvee.Couleur.ROUGE,
            reference_externe_id="abc-123",
        )
        self.url = reverse("cuvee-fiche", args=[self.cuvee.pk])

    @patch("apps.catalog.views.wineapi_detail")
    def test_fiche_enrichie_par_wineapi(self, mock_detail):
        mock_detail.return_value = _WINEAPI_DETAIL
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_detail.assert_called_once_with("abc-123")
        self.assertEqual(resp.data["note_communaute"], {"note": 3.9, "nb": 26})
        self.assertEqual(resp.data["prix_marche"]["max"], 65)
        self.assertEqual(resp.data["accords_mets"][0]["nom"], "Beef")
        self.assertEqual(len(resp.data["avis"]), 1)

    @patch("apps.catalog.views.wineapi_detail")
    def test_fiche_repli_sans_wineapi(self, mock_detail):
        mock_detail.return_value = None
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Repli sur le conseil dérivé de la couleur (ROUGE).
        self.assertIsNone(resp.data["note_communaute"])
        self.assertEqual(resp.data["avis"], [])
        self.assertIsNone(resp.data["prix_marche"])
        self.assertTrue(resp.data["accords_mets"])  # accords par défaut présents
        self.assertIsNone(resp.data["accords_mets"][0]["confiance"])


class FicheRafraichirTests(APITestCase):
    """Bouton de synchro : POST /api/cuvees/{id}/rafraichir/ + garde-fou cooldown."""

    def setUp(self):
        cache.clear()  # réinitialise cooldown et compteurs de throttle
        self.user = User.objects.create_user(username="alice", password="x")
        self.domaine = Domaine.objects.create(nom="Château Cantemerle")
        self.cuvee = Cuvee.objects.create(
            domaine=self.domaine,
            nom="Grand Cru Classé",
            couleur=Cuvee.Couleur.ROUGE,
            reference_externe_id="abc-123",
        )
        self.url = reverse("cuvee-rafraichir", args=[self.cuvee.pk])

    @patch("apps.catalog.views.refresh_wineapi_detail")
    def test_force_le_refetch_puis_bloque_par_cooldown(self, mock_refresh):
        mock_refresh.return_value = _WINEAPI_DETAIL
        self.client.force_authenticate(self.user)

        r1 = self.client.post(self.url)
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r1.data["note_communaute"], {"note": 3.9, "nb": 26})
        mock_refresh.assert_called_once_with("abc-123")

        # 2e synchro immédiate : bloquée par le cooldown, aucun nouvel appel wineapi.
        r2 = self.client.post(self.url)
        self.assertEqual(r2.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        mock_refresh.assert_called_once()

    def test_sans_source_externe_400(self):
        cuvee = Cuvee.objects.create(
            domaine=self.domaine, nom="Sans réf", couleur=Cuvee.Couleur.ROUGE
        )
        url = reverse("cuvee-rafraichir", args=[cuvee.pk])
        self.client.force_authenticate(self.user)
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# Détail wineapi complet (sous-ensemble représentatif de GET /wines/{id}).
_WINEAPI_FULL = {
    "body": "Full-bodied",
    "acidity": "Medium",
    "averageRating": 3.9,
    "ratingsCount": 26,
    "classification": "Grand Cru Classé",
    "description": "Un Médoc élégant et structuré.",
    "elaborate": "Élevage 18 mois en fûts de chêne.",
    "alcoholContent": 13.5,
    "imageUrl": "https://img.example/wine.png",
    "lwinCode": "1234567",
    "region": {"id": "r1", "name": "Haut-Médoc", "country": "France"},
    "priceRange": {"min": 38, "max": 65, "currency": "EUR"},
    "grapes": [{"id": "g1", "name": "Merlot", "color": "red"}],
    "pairings": [{"food": "Beef", "confidence": 0.95, "notes": "Grillé"}],
    "scores": [{"score": 92, "scoreText": "Excellent", "reviewer": "Critic", "reviewDate": "2020-01-01"}],
    "prices": [
        {"merchantName": "Cave B", "price": 59.9, "currency": "EUR", "url": "https://b.example/w"},
        {"merchantName": "Cave A", "price": 42.5, "currency": "EUR", "url": None},
        {"merchantName": "Sans prix", "price": None, "currency": "EUR"},  # ignoré
    ],
}


# Détail wineapi avec offres datées (fetchedAt) pour l'historique de prix.
_WINEAPI_PRICES_DATED = {
    "priceRange": {"min": 40, "max": 70, "currency": "EUR"},
    "prices": [
        {"merchantName": "Cave A", "price": 42.5, "currency": "EUR", "fetchedAt": "2026-06-01T10:00:00Z"},
        {"merchantName": "Cave B", "price": 59.9, "currency": "EUR", "fetchedAt": "2026-06-01"},
        {"merchantName": "Cave C", "price": 55.0, "currency": "EUR", "fetchedAt": "2026-06-15T08:00:00Z"},
        {"merchantName": "Sans date", "price": 50.0, "currency": "EUR"},  # ignoré (pas de fetchedAt)
    ],
}


class HistoriquePrixTests(SimpleTestCase):
    """wine_profile.points_historique_prix : offres datées -> points par jour (pur)."""

    def test_groupe_par_jour_min_et_max(self):
        points = wine_profile.points_historique_prix(_WINEAPI_PRICES_DATED)
        # Deux jours de relevé, triés ; le 01/06 agrège deux offres (min/max).
        self.assertEqual([p["date"] for p in points], ["2026-06-01", "2026-06-15"])
        self.assertEqual(points[0]["prix_min"], 42.5)
        self.assertEqual(points[0]["prix_max"], 59.9)
        self.assertEqual(points[1]["prix_min"], 55.0)
        self.assertEqual(points[1]["devise"], "EUR")

    def test_offres_sans_date_ou_prix_ignorees(self):
        self.assertEqual(wine_profile.points_historique_prix({}), [])
        self.assertEqual(
            wine_profile.points_historique_prix(
                {"prices": [{"merchantName": "X", "price": 10}]}  # pas de fetchedAt
            ),
            [],
        )

    def test_prix_marchands_expose_la_date_de_releve(self):
        offres = wine_profile.prix_marchands(_WINEAPI_PRICES_DATED)
        par_marchand = {o["marchand"]: o for o in offres}
        self.assertEqual(par_marchand["Cave A"]["releve_le"], "2026-06-01")
        self.assertEqual(par_marchand["Cave C"]["releve_le"], "2026-06-15")
        self.assertEqual(par_marchand["Sans date"]["releve_le"], "")


class NormalizeDetailTests(SimpleTestCase):
    """wine_profile.normalize_detail : aplatissement wineapi -> champs cuvée (pur)."""

    def test_aplati_les_champs(self):
        d = wine_profile.normalize_detail(_WINEAPI_FULL)
        self.assertEqual(d["region"], "Haut-Médoc")
        self.assertEqual(d["pays"], "France")
        self.assertEqual(d["classification"], "Grand Cru Classé")
        self.assertEqual(d["corps"], "Full-bodied")
        self.assertEqual(d["degre_alcool"], 13.5)
        self.assertEqual(d["image_url"], "https://img.example/wine.png")
        self.assertEqual(d["note_moyenne"], 3.9)
        self.assertEqual(d["nb_notes"], 26)
        self.assertEqual(d["prix_min"], 38)
        self.assertEqual(d["cepages"], ["Merlot"])
        self.assertEqual(d["accords"][0]["nom"], "Beef")
        self.assertEqual([o["marchand"] for o in d["prix_marchands"]], ["Cave A", "Cave B"])

    def test_detail_vide_donne_des_valeurs_neutres(self):
        d = wine_profile.normalize_detail({})
        self.assertEqual(d["region"], "")
        self.assertIsNone(d["note_moyenne"])
        self.assertEqual(d["accords"], [])
        self.assertEqual(d["cepages"], [])
        self.assertEqual(d["prix_marchands"], [])


class EnrichCuveeTests(TestCase):
    """ingest.enrich_cuvee_from_wineapi : persistance sur la cuvée."""

    def setUp(self):
        self.domaine = Domaine.objects.create(nom="Château X")
        self.cuvee = Cuvee.objects.create(
            domaine=self.domaine, nom="C", couleur="ROUGE", reference_externe_id="w1"
        )

    def test_persiste_lenrichissement(self):
        enrich_cuvee_from_wineapi(self.cuvee, _WINEAPI_FULL)
        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.region, "Haut-Médoc")
        self.assertEqual(str(self.cuvee.note_moyenne), "3.9")
        self.assertEqual(self.cuvee.description, "Un Médoc élégant et structuré.")
        self.assertEqual(self.cuvee.image_url, "https://img.example/wine.png")
        self.assertEqual(self.cuvee.accords[0]["nom"], "Beef")
        self.assertEqual(self.cuvee.scores[0]["reviewer"], "Critic")
        self.assertEqual(self.cuvee.prix_marchands[0]["marchand"], "Cave A")  # moins cher d'abord
        self.assertIsNotNone(self.cuvee.enrichi_le)
        self.assertTrue(self.cuvee.cepages.filter(nom="Merlot").exists())

    def test_ne_remplace_pas_une_valeur_par_du_vide(self):
        self.cuvee.description = "Déjà là"
        self.cuvee.save()
        enrich_cuvee_from_wineapi(self.cuvee, {"body": "Full-bodied"})  # pas de description
        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.description, "Déjà là")  # conservé
        self.assertEqual(self.cuvee.corps, "Full-bodied")  # ajouté

    def test_conserve_le_payload_brut_complet(self):
        """Toutes les infos remontées sont stockées : le détail brut est persisté
        tel quel, y compris les champs non mappés en colonnes."""
        enrich_cuvee_from_wineapi(self.cuvee, _WINEAPI_FULL)
        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.wineapi_detail, _WINEAPI_FULL)
        # Un champ non mappé en colonne (ex: la couleur des cépages) reste accessible.
        self.assertEqual(self.cuvee.wineapi_detail["grapes"][0]["color"], "red")

    def test_le_dernier_appel_rafraichit_le_snapshot(self):
        """« Actualiser » remplace le snapshot par les données fraîches (prix...)."""
        enrich_cuvee_from_wineapi(self.cuvee, _WINEAPI_FULL)
        frais = {**_WINEAPI_FULL, "priceRange": {"min": 40, "max": 70, "currency": "EUR"}}
        enrich_cuvee_from_wineapi(self.cuvee, frais)
        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.wineapi_detail["priceRange"]["max"], 70)
        self.assertEqual(str(self.cuvee.prix_max), "70.00")

    def test_accumule_lhistorique_de_prix_par_date(self):
        """Les offres datées alimentent la série ; un nouvel appel enrichit sans
        dupliquer, et un relevé pour une date existante met le point à jour."""
        enrich_cuvee_from_wineapi(self.cuvee, _WINEAPI_PRICES_DATED)
        self.cuvee.refresh_from_db()
        self.assertEqual([p["date"] for p in self.cuvee.historique_prix], ["2026-06-01", "2026-06-15"])

        # Second appel : nouvelle date + mise à jour d'une date déjà présente.
        suivant = {"prices": [
            {"merchantName": "Cave D", "price": 48.0, "currency": "EUR", "fetchedAt": "2026-06-20"},
            {"merchantName": "Cave A", "price": 40.0, "currency": "EUR", "fetchedAt": "2026-06-01"},
        ]}
        enrich_cuvee_from_wineapi(self.cuvee, suivant)
        self.cuvee.refresh_from_db()
        hist = {p["date"]: p for p in self.cuvee.historique_prix}
        self.assertEqual(sorted(hist), ["2026-06-01", "2026-06-15", "2026-06-20"])
        self.assertEqual(hist["2026-06-01"]["prix_min"], 40.0)  # point mis à jour
        self.assertEqual(hist["2026-06-20"]["prix_min"], 48.0)  # point ajouté

    def test_repli_sur_la_fourchette_marche_si_aucune_offre_datee(self):
        """Sans offre datée mais avec une fourchette marché, on ancre un point au
        jour de la synchro pour que la série se construise quand même."""
        from django.utils import timezone

        enrich_cuvee_from_wineapi(self.cuvee, {"priceRange": {"min": 38, "max": 65, "currency": "EUR"}})
        self.cuvee.refresh_from_db()
        self.assertEqual(len(self.cuvee.historique_prix), 1)
        point = self.cuvee.historique_prix[0]
        self.assertEqual(point["date"], timezone.now().date().isoformat())
        self.assertEqual(point["prix_min"], 38)
        self.assertEqual(point["prix_max"], 65)

    def test_upsert_enrichit_via_raw(self):
        wine = NormalizedWine(
            domaine_nom="Dom", cuvee_nom="Cuv", reference_externe_id="w2",
            raw={"wineapi_detail": _WINEAPI_FULL},
        )
        cuvee, created = upsert_cuvee(wine)
        self.assertTrue(created)
        self.assertEqual(cuvee.classification, "Grand Cru Classé")
        self.assertIsNotNone(cuvee.enrichi_le)


class FicheDepuisBaseTests(APITestCase):
    """La fiche lit l'enrichissement persisté ; enrichissement paresseux au 1er accès."""

    def setUp(self):
        cache.clear()
        self.domaine = Domaine.objects.create(nom="Château X")
        self.cuvee = Cuvee.objects.create(
            domaine=self.domaine, nom="C", couleur="ROUGE", reference_externe_id="w1"
        )
        enrich_cuvee_from_wineapi(self.cuvee, _WINEAPI_FULL)

    @patch("apps.catalog.views.wineapi_detail")
    def test_fiche_lit_la_base_sans_reseau(self, mock_detail):
        resp = self.client.get(reverse("cuvee-fiche", args=[self.cuvee.pk]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_detail.assert_not_called()  # déjà enrichi -> aucun appel wineapi
        self.assertEqual(resp.data["note_communaute"], {"note": 3.9, "nb": 26})
        self.assertEqual(resp.data["cuvee"]["description"], "Un Médoc élégant et structuré.")
        self.assertEqual(resp.data["cuvee"]["image_url"], "https://img.example/wine.png")
        self.assertEqual(resp.data["cuvee"]["classification"], "Grand Cru Classé")

    @patch("apps.catalog.views.wineapi_detail")
    def test_enrichissement_paresseux_au_premier_acces(self, mock_detail):
        neuf = Cuvee.objects.create(
            domaine=self.domaine, nom="Neuf", couleur="ROUGE", reference_externe_id="w9"
        )
        mock_detail.return_value = _WINEAPI_FULL
        resp = self.client.get(reverse("cuvee-fiche", args=[neuf.pk]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_detail.assert_called_once_with("w9")
        neuf.refresh_from_db()
        self.assertIsNotNone(neuf.enrichi_le)  # persisté au premier accès


class CatalogueSuppressionPermissionTests(APITestCase):
    """Le catalogue mutualisé est écrivable par tout utilisateur authentifié, mais
    sa *suppression* est réservée au staff : effacer une référence partagée peut
    détruire en cascade des données privées d'autres utilisateurs (RGPD)."""

    def setUp(self):
        self.domaine = Domaine.objects.create(nom="Château Partagé")
        self.cuvee = Cuvee.objects.create(
            domaine=self.domaine, nom="Cuvée Partagée", couleur="ROUGE"
        )

    def test_utilisateur_lambda_ne_peut_pas_supprimer_une_cuvee(self):
        self.client.force_authenticate(User.objects.create_user("alice", password="x"))
        resp = self.client.delete(reverse("cuvee-detail", args=[self.cuvee.pk]))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Cuvee.objects.filter(pk=self.cuvee.pk).exists())

    def test_utilisateur_lambda_peut_toujours_editer_le_catalogue(self):
        # L'écriture (catalogue communautaire) reste ouverte aux authentifiés.
        self.client.force_authenticate(User.objects.create_user("alice", password="x"))
        resp = self.client.patch(
            reverse("cuvee-detail", args=[self.cuvee.pk]), {"appellation": "Médoc"}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_staff_peut_supprimer_une_cuvee_libre(self):
        staff = User.objects.create_user("admin", password="x", is_staff=True)
        self.client.force_authenticate(staff)
        resp = self.client.delete(reverse("cuvee-detail", args=[self.cuvee.pk]))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Cuvee.objects.filter(pk=self.cuvee.pk).exists())

    def test_anonyme_ne_peut_pas_supprimer(self):
        resp = self.client.delete(reverse("domaine-detail", args=[self.domaine.pk]))
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )


class SuppressionProtegeeTests(APITestCase):
    """La suppression d'une référence encore utilisée renvoie un 409 propre (et non
    un 500), et n'efface jamais les données privées liées."""

    def setUp(self):
        from apps.inventory.models import Bouteille, NoteDegustation

        self.Bouteille = Bouteille
        self.NoteDegustation = NoteDegustation
        self.staff = User.objects.create_user("admin", password="x", is_staff=True)
        self.autre = User.objects.create_user("bob", password="x")
        self.domaine = Domaine.objects.create(nom="Château Y")
        self.cuvee = Cuvee.objects.create(domaine=self.domaine, nom="C", couleur="ROUGE")

    def test_supprimer_une_cuvee_avec_stock_renvoie_409(self):
        self.Bouteille.objects.create(proprietaire=self.autre, cuvee=self.cuvee, quantite=1)
        self.client.force_authenticate(self.staff)
        resp = self.client.delete(reverse("cuvee-detail", args=[self.cuvee.pk]))
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(Cuvee.objects.filter(pk=self.cuvee.pk).exists())

    def test_supprimer_une_cuvee_notee_par_autrui_renvoie_409_sans_perte(self):
        # Une note de dégustation est privée : sa cuvée ne doit pas disparaître
        # (PROTECT) et surtout la note ne doit pas être détruite en cascade.
        self.NoteDegustation.objects.create(proprietaire=self.autre, cuvee=self.cuvee, note="4.5")
        self.client.force_authenticate(self.staff)
        resp = self.client.delete(reverse("cuvee-detail", args=[self.cuvee.pk]))
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            self.NoteDegustation.objects.filter(proprietaire=self.autre).count(), 1
        )

    def test_supprimer_un_domaine_dont_une_cuvee_a_du_stock_renvoie_409(self):
        # Domaine -> cuvée est en CASCADE, mais cuvée -> bouteille est en PROTECT :
        # la cascade bute sur le stock et remonte un 409 au lieu d'un 500.
        self.Bouteille.objects.create(proprietaire=self.autre, cuvee=self.cuvee, quantite=1)
        self.client.force_authenticate(self.staff)
        resp = self.client.delete(reverse("domaine-detail", args=[self.domaine.pk]))
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(Domaine.objects.filter(pk=self.domaine.pk).exists())
        self.assertTrue(Cuvee.objects.filter(pk=self.cuvee.pk).exists())


class EvaluationReconnaissanceTests(TestCase):
    """Le harnais de mesure : c'est lui qui rend un réglage d'OCR défendable."""

    @staticmethod
    def _wine(domaine, cuvee, lwin=""):
        return NormalizedWine(
            domaine_nom=domaine, cuvee_nom=cuvee, source="lwin",
            raw={"wineapi_detail": {"lwinCode": lwin}} if lwin else {},
        )

    def test_aucun_relevé_vaut_silence(self):
        cas = evaluation.Cas(identifiant="x", producteur="Ridge", vin="Geyserville")
        self.assertEqual(evaluation.juger(cas, None).issue, "silence")

    def test_code_lwin_identique_vaut_trouve(self):
        cas = evaluation.Cas(identifiant="x", lwin="1234567", producteur="Ridge", vin="G")
        resultat = evaluation.juger(cas, self._wine("Ridge", "Geyserville", lwin="1234567"))
        self.assertEqual(resultat.issue, "trouve")

    def test_doublon_du_referentiel_n_est_pas_une_erreur(self):
        """Le dump LWIN porte le même vin sous plusieurs codes : tomber sur un
        autre code du MÊME vin est une identification réussie, pas une erreur.
        Juger sur le seul code rendrait le taux d'erreur absurdement pessimiste."""
        cas = evaluation.Cas(identifiant="x", lwin="1111111", producteur="Ridge", vin="Geyserville")
        resultat = evaluation.juger(cas, self._wine("Ridge", "Geyserville", lwin="2222222"))
        self.assertEqual(resultat.issue, "trouve")

    def test_autre_vin_vaut_erreur(self):
        cas = evaluation.Cas(identifiant="x", producteur="Ridge", vin="Geyserville")
        resultat = evaluation.juger(cas, self._wine("Ravenswood", "Old Hill"))
        self.assertEqual(resultat.issue, "erreur")

    def test_un_producteur_proche_ne_valide_pas(self):
        """« Ridge » attendu ne doit pas être validé par « Ridgeview » : la
        comparaison porte sur des tokens entiers, pas sur des sous-chaînes."""
        cas = evaluation.Cas(identifiant="x", producteur="Ridge", vin="Geyserville")
        self.assertEqual(evaluation.juger(cas, self._wine("Ridgeview", "Estate")).issue, "erreur")

    def test_les_taux_du_bilan(self):
        cas = evaluation.Cas(identifiant="x")
        bilan = evaluation.Bilan(resultats=[
            evaluation.Resultat(cas=cas, issue="trouve"),
            evaluation.Resultat(cas=cas, issue="trouve"),
            evaluation.Resultat(cas=cas, issue="silence"),
            evaluation.Resultat(cas=cas, issue="erreur"),
        ])
        self.assertEqual(bilan.total, 4)
        self.assertEqual(bilan.taux_reconnaissance, 50.0)
        self.assertEqual(bilan.taux_silence, 25.0)
        self.assertEqual(bilan.taux_erreur, 25.0)

    def test_bilan_vide_ne_divise_pas_par_zero(self):
        self.assertEqual(evaluation.Bilan().taux_reconnaissance, 0.0)

    def test_generation_synthetique_reproductible(self):
        ReferenceLwin.objects.create(lwin="1", producteur="Château Margaux", vin="Grand Vin")
        ReferenceLwin.objects.create(lwin="2", producteur="Domaine Leflaive", vin="Puligny")
        refs = list(ReferenceLwin.objects.all())
        a = evaluation.generer_cas_synthetiques(refs, 20, graine=7)
        b = evaluation.generer_cas_synthetiques(refs, 20, graine=7)
        self.assertEqual([c.texte for c in a], [c.texte for c in b])  # même graine
        c = evaluation.generer_cas_synthetiques(refs, 20, graine=8)
        self.assertNotEqual([x.texte for x in a], [x.texte for x in c])

    def test_sans_bruit_le_texte_genere_reste_fidele(self):
        ReferenceLwin.objects.create(lwin="1", producteur="Château Margaux", vin="Grand Vin")
        refs = list(ReferenceLwin.objects.all())
        cas = evaluation.generer_cas_synthetiques(refs, 5, graine=1, intensite=0.0)
        for c in cas:
            self.assertEqual(c.texte, "Château Margaux Grand Vin")
            self.assertEqual(c.lwin, "1")

    def test_referentiel_vide_ne_genere_rien(self):
        self.assertEqual(evaluation.generer_cas_synthetiques([], 10), [])

    def test_le_corpus_livre_est_lisible_et_annote(self):
        from apps.catalog.management.commands.evaluer_reconnaissance import CORPUS_DEFAUT

        cas, meta = evaluation.charger_corpus(CORPUS_DEFAUT)
        self.assertGreaterEqual(len(cas), 10)
        self.assertTrue(meta["licence"])       # licence du jeu amont documentée
        self.assertTrue(meta["attribution"])   # CC BY 4.0 => attribution obligatoire
        for c in cas:
            self.assertTrue(c.producteur, f"cas {c.identifiant} sans producteur attendu")


class EvaluerReconnaissanceCommandeTests(TestCase):
    """La commande : orchestration, sans réseau ni image."""

    def setUp(self):
        for i in range(30):
            ReferenceLwin.objects.create(
                lwin=str(2000000 + i), producteur=f"Producteur {i}", vin=f"Cuvée {i}"
            )

    def _lancer(self, **kwargs):
        sortie, erreurs = StringIO(), StringIO()
        call_command("evaluer_reconnaissance", stdout=sortie, stderr=erreurs, **kwargs)
        return sortie.getvalue(), erreurs.getvalue()

    def test_mode_synthetique_seul(self):
        sortie, _ = self._lancer(synthetique=15, sources="lwin")
        self.assertIn("Synthétique", sortie)
        self.assertIn("15 cas", sortie)
        self.assertNotIn("Photos réelles", sortie)  # pas de corpus demandé

    def test_source_inconnue_est_refusee(self):
        with self.assertRaises(CommandError):
            self._lancer(synthetique=1, sources="nexistepas")

    def test_referentiel_vide_est_signale_et_n_echoue_pas(self):
        ReferenceLwin.objects.all().delete()
        _, erreurs = self._lancer(synthetique=5, sources="lwin")
        self.assertIn("Référentiel LWIN vide", erreurs)

    def test_corpus_introuvable_leve_une_erreur_explicite(self):
        with self.assertRaises(CommandError):
            self._lancer(corpus="/tmp/corpus-qui-nexiste-pas.json", sources="lwin")

    def test_le_bruit_maximal_ne_plante_pas(self):
        """Garde-fou : la génération doit tenir sur des entrées très dégradées."""
        sortie, _ = self._lancer(synthetique=10, intensite=1.0, sources="lwin")
        self.assertIn("10 cas", sortie)


class CorpusOpenFoodFactsTests(SimpleTestCase):
    """Filtrage du corpus : c'est lui qui décide de la qualité de la mesure."""

    def setUp(self):
        from apps.catalog.management.commands.corpus_openfoodfacts import Command

        self.commande = Command()
        self.vus = set()

    def _produit(self, **surcharges):
        base = {
            "code": "3211203433220",
            "brands": "Baron de Lestac",
            "product_name": "Bordeaux 2013",
            "image_front_url": "https://images.openfoodfacts.org/x.jpg",
            "categories_tags": ["en:alcoholic-beverages", "en:wines", "en:red-wines"],
        }
        base.update(surcharges)
        return base

    def test_un_vin_complet_est_retenu(self):
        entree = self.commande._retenir(self._produit(), self.vus)
        self.assertIsNotNone(entree)
        self.assertEqual(entree["code_barres"], "3211203433220")
        self.assertEqual(entree["producteur"], "Baron de Lestac")

    def test_produit_sans_le_tag_vin_est_ecarte(self):
        """Le paramètre de recherche d'OFF fait une correspondance textuelle et
        ramène des produits sans rapport : on revalide le tag sur la fiche."""
        produit = self._produit(categories_tags=["en:jams", "en:marmalades"])
        self.assertIsNone(self.commande._retenir(produit, self.vus))

    def test_produit_mal_categorise_est_ecarte(self):
        """Cas réel : une confiture de clémentines porte `en:wines` dans OFF, à
        côté de `en:jams`. Exiger le tag ne suffit pas, il faut refuser les
        familles qui le contredisent — sinon le corpus contient des cas
        ingagnables qui font passer le moteur pour mauvais."""
        produit = self._produit(
            product_name="Confiture clémentines et oranges de Corse",
            categories_tags=["en:wines", "en:wines-from-france", "en:jams", "en:marmalades"],
        )
        self.assertIsNone(self.commande._retenir(produit, self.vus))

    def test_vinaigre_de_vin_est_ecarte(self):
        produit = self._produit(
            product_name="Vinaigre de vin blanc",
            categories_tags=["en:wines", "en:vinegars", "en:wine-vinegars"],
        )
        self.assertIsNone(self.commande._retenir(produit, self.vus))

    def test_fiche_incomplete_est_ecartee(self):
        for manquant in ("code", "brands", "product_name", "image_front_url"):
            self.assertIsNone(
                self.commande._retenir(self._produit(**{manquant: ""}), self.vus),
                f"une fiche sans {manquant} ne devrait pas être retenue",
            )

    def test_marque_ou_nom_non_discriminant_est_ecarte(self):
        self.assertIsNone(self.commande._retenir(self._produit(brands="Bio"), self.vus))
        self.assertIsNone(self.commande._retenir(self._produit(brands="AB"), self.vus))
        self.assertIsNone(self.commande._retenir(self._produit(product_name="75 cl"), self.vus))
        self.assertIsNone(self.commande._retenir(self._produit(product_name="Rouge"), self.vus))

    def test_doublon_de_code_barres_est_ecarte(self):
        self.assertIsNotNone(self.commande._retenir(self._produit(), self.vus))
        self.assertIsNone(self.commande._retenir(self._produit(), self.vus))


# Les tests écrivent de vraies vignettes : on les isole dans un dossier
# temporaire plutôt que de semer des fichiers dans l'arborescence du projet.
_MEDIA_TEST = tempfile.mkdtemp(prefix="cavavin-media-")


@override_settings(MEDIA_ROOT=_MEDIA_TEST)
class PhotoEtiquetteTests(APITestCase):
    """La vignette d'étiquette conservée au catalogue lors d'un scan."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("alice", password="x")
        self.client.force_authenticate(self.user)

    @staticmethod
    def _photo(largeur=1200, hauteur=900):
        from PIL import Image

        tampon = BytesIO()
        Image.effect_noise((largeur, hauteur), 40).convert("RGB").save(tampon, "JPEG")
        return tampon.getvalue()

    def _scanner(self, wine):
        with patch("apps.catalog.views.get_enabled_providers",
                   return_value=[_FakeProvider(wine=wine, name="claude")]):
            return self.client.post(
                reverse("scan-etiquette"),
                {"image": SimpleUploadedFile("e.jpg", self._photo(), content_type="image/jpeg")},
                format="multipart",
            )

    def test_le_scan_conserve_une_vignette_sur_la_cuvee(self):
        wine = NormalizedWine(domaine_nom="Ch. Test", cuvee_nom="Grand Vin", source="claude")
        resp = self._scanner(wine)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        cuvee = Cuvee.objects.get(nom="Grand Vin")
        self.assertTrue(cuvee.photo_etiquette, "aucune vignette conservée")
        self.assertIsNotNone(resp.data["cuvee"]["photo_etiquette_url"])

    def test_une_vignette_existante_n_est_pas_ecrasee(self):
        """Le catalogue est mutualisé : un second scan, plus flou ou plus lointain,
        ne doit pas remplacer pour tout le monde une photo déjà correcte."""
        wine = NormalizedWine(domaine_nom="Ch. Test", cuvee_nom="Grand Vin", source="claude")
        self._scanner(wine)
        cuvee = Cuvee.objects.get(nom="Grand Vin")
        premiere = cuvee.photo_etiquette.name

        self._scanner(wine)
        cuvee.refresh_from_db()
        self.assertEqual(cuvee.photo_etiquette.name, premiere)

    def test_la_vignette_est_servie_et_lisible_publiquement(self):
        """Le catalogue est en lecture publique : sa vignette doit l'être aussi."""
        wine = NormalizedWine(domaine_nom="Ch. Test", cuvee_nom="Grand Vin", source="claude")
        self._scanner(wine)
        cuvee = Cuvee.objects.get(nom="Grand Vin")

        self.client.force_authenticate(None)  # anonyme
        resp = self.client.get(reverse("cuvee-photo", args=[cuvee.pk]))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "image/jpeg")
        self.assertTrue(b"".join(resp.streaming_content))

    def test_cuvee_sans_vignette_repond_404(self):
        domaine = Domaine.objects.create(nom="Dom")
        cuvee = Cuvee.objects.create(domaine=domaine, nom="Sans photo", couleur="ROUGE")
        resp = self.client.get(reverse("cuvee-photo", args=[cuvee.pk]))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_fichier_disparu_repond_404_et_non_500(self):
        """Volume remonté, sauvegarde partielle : la fiche doit survivre."""
        domaine = Domaine.objects.create(nom="Dom")
        cuvee = Cuvee.objects.create(domaine=domaine, nom="Fantome", couleur="ROUGE")
        cuvee.photo_etiquette.name = "etiquettes/2026/07/inexistant.jpg"
        cuvee.save(update_fields=["photo_etiquette"])
        resp = self.client.get(reverse("cuvee-photo", args=[cuvee.pk]))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_un_echec_de_vignette_ne_fait_pas_echouer_le_scan(self):
        """Enrichir le catalogue d'une photo est un bonus, jamais une raison
        d'échouer une identification."""
        wine = NormalizedWine(domaine_nom="Ch. Test", cuvee_nom="Grand Vin", source="claude")
        with patch("apps.catalog.views.recadrer_etiquette", side_effect=OSError("disque plein")):
            resp = self._scanner(wine)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(Cuvee.objects.get(nom="Grand Vin").photo_etiquette)


class RecadrageEtiquetteTests(SimpleTestCase):
    """Le recadrage : best-effort, jamais bloquant."""

    def test_la_vignette_est_bornee_et_en_jpeg(self):
        from PIL import Image

        tampon = BytesIO()
        Image.effect_noise((3000, 2000), 40).convert("RGB").save(tampon, "JPEG")
        vignette, content_type = image_utils.recadrer_etiquette(tampon.getvalue())

        self.assertEqual(content_type, "image/jpeg")
        self.assertLessEqual(
            max(Image.open(BytesIO(vignette)).size), image_utils.TAILLE_VIGNETTE
        )

    def test_une_image_illisible_ne_leve_pas(self):
        vignette, _ = image_utils.recadrer_etiquette(b"pas une image")
        self.assertEqual(vignette, b"pas une image")

    def test_sans_tesseract_on_garde_la_photo_entiere(self):
        from PIL import Image

        tampon = BytesIO()
        Image.effect_noise((1000, 800), 40).convert("RGB").save(tampon, "JPEG")
        with override_settings(TESSERACT_CMD="binaire-inexistant"):
            vignette, content_type = image_utils.recadrer_etiquette(tampon.getvalue())
        self.assertEqual(content_type, "image/jpeg")
        self.assertTrue(vignette)


class DedupNomCuveeTests(TestCase):
    """Le nom normalisé ferme la dernière porte aux doublons du catalogue."""

    def _wine(self, nom, domaine="Ch. Test"):
        # Ni code-barres ni référence externe : c'est exactement ce que produit
        # une identification par LLM, donc le chemin qui retombe sur le nom.
        return NormalizedWine(
            domaine_nom=domaine, cuvee_nom=nom, couleur="ROUGE", source="claude"
        )

    def test_les_variantes_de_casse_et_d_espaces_convergent(self):
        for nom in ("Grand Vin", "Grand vin", "GRAND VIN", " Grand  Vin ", "Grand-Vin"):
            upsert_cuvee(self._wine(nom))
        self.assertEqual(Cuvee.objects.count(), 1, "variantes non dédoublonnées")

    def test_les_accents_convergent(self):
        upsert_cuvee(self._wine("Château Margaux"))
        _, created = upsert_cuvee(self._wine("Chateau Margaux"))
        self.assertFalse(created)
        self.assertEqual(Cuvee.objects.count(), 1)

    def test_deux_vins_reellement_differents_restent_distincts(self):
        upsert_cuvee(self._wine("Grand Vin"))
        upsert_cuvee(self._wine("Second Vin"))
        self.assertEqual(Cuvee.objects.count(), 2)

    def test_meme_nom_chez_deux_producteurs_reste_distinct(self):
        """La contrainte porte sur (domaine, nom) : « Grand Vin » existe partout."""
        upsert_cuvee(self._wine("Grand Vin", domaine="Ch. A"))
        upsert_cuvee(self._wine("Grand Vin", domaine="Ch. B"))
        self.assertEqual(Cuvee.objects.count(), 2)

    def test_la_contrainte_interdit_le_doublon_en_base(self):
        domaine = Domaine.objects.create(nom="Dom")
        Cuvee.objects.create(domaine=domaine, nom="Grand Vin", couleur="ROUGE")
        with self.assertRaises(IntegrityError):
            Cuvee.objects.create(domaine=domaine, nom="grand vin", couleur="BLANC")

    def test_le_nom_normalise_suit_le_nom(self):
        domaine = Domaine.objects.create(nom="Dom")
        cuvee = Cuvee.objects.create(domaine=domaine, nom="Château Test", couleur="ROUGE")
        self.assertEqual(cuvee.nom_normalise, "chateau test")
        cuvee.nom = "Clos du Roi"
        cuvee.save()
        self.assertEqual(cuvee.nom_normalise, "clos du roi")

    def test_le_nom_normalise_suit_meme_avec_update_fields(self):
        """update_fields=['nom'] ne doit pas laisser la forme canonique périmée :
        un champ dérivé qui diverge rouvrirait la porte aux doublons."""
        domaine = Domaine.objects.create(nom="Dom")
        cuvee = Cuvee.objects.create(domaine=domaine, nom="Avant", couleur="ROUGE")
        cuvee.nom = "Après"
        cuvee.save(update_fields=["nom"])
        cuvee.refresh_from_db()
        self.assertEqual(cuvee.nom_normalise, "apres")

    def test_api_refuse_de_creer_un_doublon(self):
        """Le catalogue est partagé : le créer en double par l'API n'a pas de sens."""
        user = User.objects.create_user("bob", password="x")
        self.client.force_login(user)
        domaine = Domaine.objects.create(nom="Dom")
        payload = {"domaine": domaine.id, "nom": "Grand Vin", "couleur": "ROUGE"}
        self.assertEqual(
            self.client.post(reverse("cuvee-list"), payload).status_code,
            status.HTTP_201_CREATED,
        )
        seconde = self.client.post(reverse("cuvee-list"), {**payload, "nom": "grand vin"})
        self.assertEqual(seconde.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Cuvee.objects.count(), 1)


class DedupNomMigrationTests(TransactionTestCase):
    """Migration 0016 : fusion des doublons de nom avant pose de la contrainte.

    On rembobine à l'état 0015 (où le doublon est encore possible), on fabrique
    les cas que la migration doit résorber, puis on applique 0016 et on vérifie
    qu'aucune donnée privée n'a été perdue au passage.
    """

    # `inventory` est épinglé dans les deux états : sans cela, l'état historique
    # calculé pour catalog-0015 rembobinerait Bouteille avant la suppression de
    # `statut`, alors que la table, elle, ne l'a plus.
    migrate_from = [
        ("catalog", "0015_cuvee_photo_etiquette"),
        ("inventory", "0006_remove_bouteille_statut"),
    ]
    migrate_to = [
        ("catalog", "0016_dedup_nom_cuvee"),
        ("inventory", "0006_remove_bouteille_statut"),
    ]

    def test_fusionne_les_doublons_et_preserve_le_prive(self):
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Domaine = old_apps.get_model("catalog", "Domaine")
        Cuvee = old_apps.get_model("catalog", "Cuvee")
        Bouteille = old_apps.get_model("inventory", "Bouteille")
        NoteDegustation = old_apps.get_model("inventory", "NoteDegustation")
        User = old_apps.get_model("auth", "User")

        user = User.objects.create(username="alice")
        domaine = Domaine.objects.create(nom="Ch. Test", region="")
        # Trois variantes du même vin, comme un LLM peut les produire.
        garde = Cuvee.objects.create(domaine=domaine, nom="Grand Vin", couleur="ROUGE")
        doublon = Cuvee.objects.create(domaine=domaine, nom="grand vin", couleur="ROUGE",
                                       code_barres="777", region="Bordeaux")
        autre = Cuvee.objects.create(domaine=domaine, nom="Grand  Vin ", couleur="ROUGE")
        # Données privées réparties sur les doublons : rien ne doit disparaître.
        Bouteille.objects.create(proprietaire=user, cuvee=doublon, quantite=3)
        Bouteille.objects.create(proprietaire=user, cuvee=autre, quantite=2)
        NoteDegustation.objects.create(proprietaire=user, cuvee=doublon, note="4.0")

        executor.loader.build_graph()
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        Cuvee = new_apps.get_model("catalog", "Cuvee")
        Bouteille = new_apps.get_model("inventory", "Bouteille")
        NoteDegustation = new_apps.get_model("inventory", "NoteDegustation")

        self.assertEqual(Cuvee.objects.filter(domaine=domaine.pk).count(), 1)
        survivante = Cuvee.objects.get(pk=garde.pk)  # la plus ancienne survit
        self.assertEqual(survivante.nom_normalise, "grand vin")
        # Ce que seuls les doublons portaient a été récupéré.
        self.assertEqual(survivante.code_barres, "777")
        self.assertEqual(survivante.region, "Bordeaux")
        # Aucune donnée privée perdue.
        self.assertEqual(
            sum(Bouteille.objects.filter(cuvee=survivante).values_list("quantite", flat=True)), 5
        )
        self.assertEqual(NoteDegustation.objects.filter(cuvee=survivante).count(), 1)


class XWinesMappingTests(SimpleTestCase):
    """Mapping pur d'une ligne X-Wines — aucune base de données."""

    # Ligne réelle du dump (XWines_Test_100_wines.csv), en-têtes en majuscules
    # comme les rend `tabular.lignes`.
    _LIGNE = {
        "WINEID": "101847",
        "WINENAME": "Dona Antonia Porto Reserva Tawny",
        "TYPE": "Dessert/Port",
        "ELABORATE": "Assemblage/Blend",
        "GRAPES": "['Touriga Nacional', 'Touriga Franca', 'Tinta Barroca']",
        "HARMONIZE": "['Appetizer', 'Sweet Dessert', 'Blue Cheese']",
        "ABV": "20.0",
        "BODY": "Very full-bodied",
        "ACIDITY": "High",
        "CODE": "PT",
        "COUNTRY": "Portugal",
        "REGIONID": "1031",
        "REGIONNAME": "Porto",
        "WINERYID": "10674",
        "WINERYNAME": "Porto Ferreira",
        "WEBSITE": "https://sogrape.com/pt/brand/porto-ferreira",
        "VINTAGES": "[2021, 2020, 'N.V.']",
    }

    def test_detail_au_format_wineapi(self):
        """Le détail produit passe tel quel dans `wine_profile.normalize_detail`,
        qui est le seul mapping vers les colonnes de la cuvée."""
        detail = xwines_import.detail_depuis_ligne(self._LIGNE)
        champs = wine_profile.normalize_detail(detail)

        self.assertEqual(champs["region"], "Porto")
        self.assertEqual(champs["pays"], "Portugal")
        self.assertEqual(champs["corps"], "Very full-bodied")
        self.assertEqual(champs["acidite"], "High")
        self.assertEqual(champs["degre_alcool"], 20.0)
        self.assertEqual(champs["elaborate"], "Assemblage/Blend")
        self.assertEqual(
            champs["cepages"], ["Touriga Nacional", "Touriga Franca", "Tinta Barroca"]
        )
        self.assertEqual(
            [a["nom"] for a in champs["accords"]],
            ["Appetizer", "Sweet Dessert", "Blue Cheese"],
        )
        # X-Wines ne pondère pas ses accords : pas de confiance inventée.
        self.assertTrue(all(a["confiance"] is None for a in champs["accords"]))
        # Aucune donnée de marché : le canal ne doit rien affirmer sur les prix.
        self.assertIsNone(champs["prix_min"])
        self.assertIsNone(champs["note_moyenne"])

    def test_millesimes_conserves_dans_le_brut(self):
        """La liste des millésimes n'a pas d'équivalent au modèle (une cuvée est
        indépendante du millésime) : elle survit dans le payload brut."""
        detail = xwines_import.detail_depuis_ligne(self._LIGNE)
        self.assertEqual(detail["vintages"], ["2021", "2020", "N.V."])

    def test_couleurs(self):
        for type_xwines, attendu in [
            ("Red", "ROUGE"),
            ("White", "BLANC"),
            ("Rosé", "ROSE"),
            ("Sparkling", "BULLES"),
            # Un vin de dessert / porto n'a pas de couleur dans notre
            # nomenclature : AUTRE plutôt qu'un rouge affirmé à tort.
            ("Dessert", "AUTRE"),
            ("Dessert/Port", "AUTRE"),
            ("", "AUTRE"),
        ]:
            with self.subTest(type_xwines):
                self.assertEqual(xwines_import.couleur_depuis_type(type_xwines), attendu)

    def test_listes_malformees_ne_cassent_pas_la_ligne(self):
        """Le dump est communautaire : une cellule illisible vaut liste vide, la
        ligne reste importable."""
        ligne = dict(self._LIGNE, GRAPES="['Merlot'", HARMONIZE="", ABV="n/c")
        detail = xwines_import.detail_depuis_ligne(ligne)
        self.assertEqual(detail["grapes"], [])
        self.assertEqual(detail["pairings"], [])
        self.assertNotIn("alcoholContent", detail)

    def test_ligne_sans_identite_est_rejetee(self):
        """Sans producteur ni nom il n'y a aucune clé de déduplication : la ligne
        est écartée plutôt que de polluer le catalogue mutualisé."""
        self.assertIsNone(
            xwines_import.detail_depuis_ligne(dict(self._LIGNE, WINERYNAME=""))
        )
        self.assertIsNone(xwines_import.wine_depuis_ligne(dict(self._LIGNE, WINENAME="")))

    def test_reference_externe_prefixee_par_le_canal(self):
        """`reference_externe_id` est mono-source : sans préfixe, l'id X-Wines
        « 101847 » se confondrait avec le wineapi.io « 101847 »."""
        wine = xwines_import.wine_depuis_ligne(self._LIGNE)
        self.assertEqual(wine.reference_externe_id, "xwines:101847")
        self.assertEqual(wine.source, "xwines")


class ImportXWinesCommandTests(TestCase):
    """Commande import_xwines : remplissage du référentiel, idempotence, reprise."""

    _COLONNES = [
        "WineID", "WineName", "Type", "Elaborate", "Grapes", "Harmonize", "ABV",
        "Body", "Acidity", "Code", "Country", "RegionID", "RegionName",
        "WineryID", "WineryName", "Website", "Vintages",
    ]
    _MARGAUX = (
        '1,Grand Vin,Red,Assemblage/Blend,"[\'Merlot\', \'Cabernet Sauvignon\']",'
        '"[\'Beef\', \'Lamb\']",13.5,Full-bodied,Medium,FR,France,10,Bordeaux,'
        '20,Chateau Margaux,https://chateau-margaux.com,"[2015, 2016]"\n'
    )

    def _fichier(self, lignes: list[str]) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(",".join(self._COLONNES) + "\n" + "".join(lignes))
            return f.name

    def _importer(self, lignes: list[str], **options) -> str:
        chemin = self._fichier(lignes)
        try:
            sortie = StringIO()
            call_command("import_xwines", chemin, stdout=sortie, **options)
            return sortie.getvalue()
        finally:
            os.unlink(chemin)

    def test_import_remplit_le_referentiel(self):
        self._importer([self._MARGAUX])

        cuvee = Cuvee.objects.get()
        self.assertEqual(cuvee.nom, "Grand Vin")
        self.assertEqual(cuvee.couleur, "ROUGE")
        self.assertEqual(cuvee.region, "Bordeaux")
        self.assertEqual(cuvee.pays, "France")
        self.assertEqual(cuvee.corps, "Full-bodied")
        self.assertEqual(float(cuvee.degre_alcool), 13.5)
        self.assertEqual(
            sorted(c.nom for c in cuvee.cepages.all()), ["Cabernet Sauvignon", "Merlot"]
        )
        self.assertEqual([a["nom"] for a in cuvee.accords], ["Beef", "Lamb"])
        # Le producteur est renseigné, là où les canaux d'identification le
        # créent avec une région vide.
        self.assertEqual(cuvee.domaine.nom, "Chateau Margaux")
        self.assertEqual(cuvee.domaine.region, "Bordeaux")
        self.assertEqual(cuvee.domaine.pays, "France")
        self.assertEqual(cuvee.domaine.site_web, "https://chateau-margaux.com")

    def test_observation_et_provenance_portent_le_canal(self):
        """Le relevé traverse le chemin commun : observation horodatée puis
        consolidation, comme n'importe quel canal."""
        self._importer([self._MARGAUX])

        observation = SourceObservation.objects.get()
        self.assertEqual(observation.canal, "xwines")
        self.assertEqual(float(observation.confiance), 0.60)
        # Rien du dump n'est perdu : les millésimes, sans équivalent au modèle,
        # restent lisibles dans le brut de l'observation.
        self.assertEqual(
            observation.payload_brut["wineapi_detail"]["vintages"], ["2015", "2016"]
        )

        cuvee = Cuvee.objects.get()
        self.assertEqual(cuvee.provenance["region"]["canal"], "xwines")

    def test_reimport_est_gratuit_et_ne_doublonne_pas(self):
        """Une seconde passe saute les vins déjà connus (import reprenable)."""
        self._importer([self._MARGAUX])
        sortie = self._importer([self._MARGAUX])

        self.assertEqual(Cuvee.objects.count(), 1)
        self.assertEqual(SourceObservation.objects.count(), 1)  # aucun relevé re-déposé
        self.assertIn("1 déjà présentes", sortie)

    def test_rafraichir_redepose_une_observation(self):
        self._importer([self._MARGAUX])
        self._importer([self._MARGAUX], rafraichir=True)

        self.assertEqual(Cuvee.objects.count(), 1)
        self.assertEqual(SourceObservation.objects.count(), 2)

    def test_cuvee_deja_connue_est_completee_pas_dupliquee(self):
        """Un vin déjà au catalogue (identifié par un autre canal, donc sans
        référence X-Wines) est retrouvé par (domaine, nom) : X-Wines lui greffe
        sa référence et son profil au lieu de créer un doublon."""
        domaine = Domaine.objects.create(nom="Chateau Margaux", region="Bordeaux")
        existante = Cuvee.objects.create(
            domaine=domaine, nom="grand vin", couleur=Cuvee.Couleur.ROUGE
        )

        self._importer([self._MARGAUX])

        self.assertEqual(Cuvee.objects.count(), 1)
        existante.refresh_from_db()
        self.assertEqual(existante.reference_externe_id, "xwines:1")
        self.assertEqual(existante.corps, "Full-bodied")

    def test_limite_et_lignes_inexploitables(self):
        sortie = self._importer(
            [
                self._MARGAUX,
                ',Sans Producteur,Red,,[],[],,,,FR,France,10,Bordeaux,20,,,[]\n',
                '3,Autre Vin,White,,[],[],,,,FR,France,10,Loire,21,Domaine X,,[]\n',
            ],
            limite=2,
        )
        self.assertIn("2 lignes lues", sortie)
        self.assertEqual(Cuvee.objects.count(), 1)  # la ligne sans producteur est écartée

    def test_schema_inattendu_leve_une_erreur(self):
        chemin = self._fichier([])
        with open(chemin, "w", encoding="utf-8") as f:
            f.write("a,b,c\n1,2,3\n")
        try:
            with self.assertRaises(CommandError) as ctx:
                call_command("import_xwines", chemin, stdout=StringIO())
            self.assertIn("WineName", str(ctx.exception))
        finally:
            os.unlink(chemin)

    def test_fichier_absent_leve_une_erreur(self):
        with self.assertRaises(CommandError):
            call_command("import_xwines", "/chemin/inexistant.csv")

    def test_conflit_de_region_du_domaine_n_interrompt_pas_l_import(self):
        """Renseigner la région d'un producteur peut heurter la contrainte
        unique (nom, région) si une fiche régionale existe déjà : le vin est
        importé quand même, une région manquante n'ayant rien de bloquant."""
        sans_region = Domaine.objects.create(nom="Chateau Margaux", region="")
        Domaine.objects.create(nom="Chateau Margaux", region="Bordeaux")
        Cuvee.objects.create(
            domaine=sans_region, nom="Grand Vin", couleur=Cuvee.Couleur.ROUGE,
            reference_externe_id="xwines:1",
        )

        self._importer([self._MARGAUX], rafraichir=True)

        sans_region.refresh_from_db()
        self.assertEqual(sans_region.region, "")  # non renseignée, mais pas d'échec
        self.assertEqual(Cuvee.objects.count(), 1)
        self.assertEqual(Cuvee.objects.get().corps, "Full-bodied")  # profil bien posé

    def test_une_ligne_en_conflit_ne_fait_pas_echouer_l_import(self):
        """Un import de 100 000 lignes ne doit pas mourir sur une ligne : un
        conflit d'identité résiduel se compte, il n'interrompt pas."""
        autre = self._MARGAUX.replace("1,Grand Vin", "2,Second Vin")
        reel = xwines_import.upsert_cuvee
        appels = {"n": 0}

        def _upsert(wine):
            appels["n"] += 1
            if appels["n"] == 1:
                raise IntegrityError("conflit simulé")
            return reel(wine)

        with patch.object(xwines_import, "upsert_cuvee", _upsert):
            sortie = self._importer([self._MARGAUX, autre])

        self.assertIn("1 ignorées", sortie)
        self.assertEqual(Cuvee.objects.count(), 1)
        self.assertEqual(Cuvee.objects.get().nom, "Second Vin")

    def test_ecriture_par_lots_et_progression(self):
        """L'import commit par lots (un fsync SQLite par vin domine sinon le
        temps d'import) et rend compte de l'avancement à chaque lot."""
        lignes = [
            self._MARGAUX.replace("1,Grand Vin", f"{i},Vin {i}") for i in range(1, 6)
        ]
        etapes = []
        chemin = self._fichier(lignes)
        try:
            xwines_import.importer_xwines(
                chemin, lot=2, progression=lambda r: etapes.append(r.crees)
            )
        finally:
            os.unlink(chemin)

        self.assertEqual(Cuvee.objects.count(), 5)
        self.assertEqual(etapes, [2, 4, 5])


class RepliNomAvantCreationTests(TestCase):
    """`upsert_cuvee` : une identité forte inédite ne doit pas faire créer une
    cuvée qui violerait `unique_cuvee_nom_par_domaine`."""

    def test_reference_externe_inedite_rejoint_la_cuvee_de_meme_nom(self):
        domaine = Domaine.objects.create(nom="Chateau Margaux", region="Bordeaux")
        existante = Cuvee.objects.create(
            domaine=domaine, nom="Grand Vin", couleur=Cuvee.Couleur.ROUGE
        )

        cuvee, created = upsert_cuvee(
            NormalizedWine(
                domaine_nom="Chateau Margaux",
                cuvee_nom="grand  vin",  # même nom normalisé
                couleur="ROUGE",
                reference_externe_id="w-42",
                source="wineapi",
            )
        )

        self.assertFalse(created)
        self.assertEqual(cuvee.pk, existante.pk)
        # L'identité neuve est greffée sur la cuvée existante.
        self.assertEqual(cuvee.reference_externe_id, "w-42")
        self.assertEqual(Cuvee.objects.count(), 1)

    def test_nom_vide_ne_rejoint_rien(self):
        """La contrainte de nom est partielle : un nom vide n'identifie rien et
        ne doit pas agréger des vins distincts."""
        domaine = Domaine.objects.create(nom="Domaine X", region="")
        Cuvee.objects.create(domaine=domaine, nom="", couleur=Cuvee.Couleur.ROUGE)

        cuvee, created = upsert_cuvee(
            NormalizedWine(
                domaine_nom="Domaine X", cuvee_nom="", couleur="ROUGE",
                reference_externe_id="w-99", source="wineapi",
            )
        )

        self.assertTrue(created)
        self.assertEqual(Cuvee.objects.count(), 2)


class ChoisirReferenceTests(SimpleTestCase):
    """Choix d'une référence LWIN pour une cuvée — fonction pure, orientée précision."""

    def _ref(self, lwin, vin, **extra):
        base = {
            "lwin": lwin, "producteur": "Chateau Margaux", "vin": vin,
            "region": "Bordeaux", "sous_region": "Margaux",
            "classification": "1er Cru", "couleur": "ROUGE",
        }
        base.update(extra)
        return base

    def test_apparie_le_bon_vin_du_domaine(self):
        ref, score, motif = appariement.choisir_reference(
            "Grand Vin", "ROUGE",
            [self._ref("1", "Pavillon Rouge"), self._ref("2", "Grand Vin")],
        )
        self.assertEqual(motif, "apparie")
        self.assertEqual(ref["lwin"], "2")
        self.assertEqual(score, 1.0)

    def test_un_vin_plus_long_ne_s_apparie_pas_au_plus_court(self):
        """Le piège de l'appariement : « Origem Merlot » ne doit pas atterrir sur
        « Origem », l'autre vin du même domaine, sous prétexte que tous les
        tokens de celui-ci sont retrouvés (défaut de `token_set_ratio`)."""
        ref, _, motif = appariement.choisir_reference(
            "Origem Merlot", "ROUGE", [self._ref("1", "Origem")]
        )
        self.assertEqual(motif, "silence")
        self.assertIsNone(ref)

    def test_deux_vins_homonymes_sont_une_ambiguite(self):
        """Même nom de vin sous deux appellations différentes : le score ne peut
        pas les départager, on s'abstient plutôt que de polluer le catalogue."""
        ref, _, motif = appariement.choisir_reference(
            "Reserve", "ROUGE",
            [self._ref("1", "Reserve", sous_region="Margaux"),
             self._ref("2", "Reserve", sous_region="Pauillac")],
        )
        self.assertEqual(motif, "ambigu")
        self.assertIsNone(ref)

    def test_meme_vin_sous_deux_codes_se_confirme(self):
        """Le dump contient le même vin sous plusieurs codes LWIN : ce n'est pas
        une ambiguïté, les deux références se confirment."""
        ref, _, motif = appariement.choisir_reference(
            "Grand Vin", "ROUGE", [self._ref("1", "Grand Vin"), self._ref("2", "Grand Vin")]
        )
        self.assertEqual(motif, "apparie")
        self.assertEqual(ref["lwin"], "1")  # départage stable sur le code

    def test_couleur_contradictoire_ecarte_la_reference(self):
        ref, _, motif = appariement.choisir_reference(
            "Grand Vin", "BLANC", [self._ref("1", "Grand Vin", couleur="ROUGE")]
        )
        self.assertEqual(motif, "silence")
        self.assertIsNone(ref)

    def test_couleur_inconnue_ne_contredit_rien(self):
        """`AUTRE` est une absence d'information (tous les portos importés de
        X-Wines), pas une couleur : elle ne doit pas bloquer l'appariement."""
        ref, _, motif = appariement.choisir_reference(
            "Grand Vin", "AUTRE", [self._ref("1", "Grand Vin", couleur="ROUGE")]
        )
        self.assertEqual(motif, "apparie")
        self.assertEqual(ref["lwin"], "1")

    def test_reference_sans_nom_de_vin(self):
        """Une référence au niveau du producteur n'identifie une cuvée que si
        celle-ci porte le nom du domaine."""
        refs = [self._ref("1", "", producteur="Chateau Margaux")]
        _, _, motif = appariement.choisir_reference("Grand Vin", "ROUGE", refs)
        self.assertEqual(motif, "silence")
        ref, _, motif = appariement.choisir_reference("Chateau Margaux", "ROUGE", refs)
        self.assertEqual(motif, "apparie")
        self.assertEqual(ref["lwin"], "1")

    def test_nom_vide_ou_aucune_reference(self):
        self.assertEqual(appariement.choisir_reference("", "ROUGE", [])[2], "silence")
        self.assertEqual(appariement.choisir_reference("Grand Vin", "ROUGE", [])[2], "silence")


class ApparierLwinCommandTests(TestCase):
    """Commande apparier_lwin : réconciliation catalogue <-> référentiel LWIN."""

    def setUp(self):
        self.domaine = Domaine.objects.create(nom="Chateau Margaux", region="Bordeaux")
        self.cuvee = Cuvee.objects.create(
            domaine=self.domaine, nom="Grand Vin", couleur=Cuvee.Couleur.ROUGE
        )
        ReferenceLwin.objects.create(
            lwin="1011247", producteur="Chateau Margaux", vin="Grand Vin",
            pays="France", region="Bordeaux", sous_region="Margaux",
            couleur="ROUGE", classification="1er Cru Classe",
        )

    def _lancer(self, **options) -> str:
        sortie = StringIO()
        call_command("apparier_lwin", stdout=sortie, **options)
        return sortie.getvalue()

    def test_appariement_pose_identite_et_appellation(self):
        self._lancer()

        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.lwin_code, "1011247")
        # L'apport décisif de LWIN : l'AOC, que l'import en masse ne connaît pas.
        self.assertEqual(self.cuvee.appellation, "Margaux")
        # La classification passe par la consolidation, comme tout champ de profil.
        self.assertEqual(self.cuvee.classification, "1er Cru Classe")
        self.assertEqual(self.cuvee.provenance["classification"]["canal"], "lwin")

    def test_observation_deposee_avec_le_score(self):
        self._lancer()

        observation = SourceObservation.objects.get(canal="lwin")
        self.assertEqual(float(observation.confiance), 1.0)
        self.assertEqual(observation.payload_brut["lwin"]["lwin"], "1011247")

    def test_simuler_n_ecrit_rien(self):
        sortie = self._lancer(simuler=True)

        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.lwin_code, "")
        self.assertEqual(SourceObservation.objects.count(), 0)
        self.assertIn("[simulation]", sortie)
        self.assertIn("1 appariées", sortie)

    def test_appellation_passe_par_la_consolidation(self):
        """L'appellation n'est pas écrite en direct : elle est *affirmée* par le
        relevé LWIN, puis arbitrée comme tout champ consolidé. Une valeur que
        plus aucune source n'affirme est donc ré-arbitrée — c'est la règle
        commune à `region`, `description` ou `classification`, pas une exception
        de l'appariement."""
        self.cuvee.appellation = "Saisie antérieure"
        self.cuvee.save(update_fields=["appellation"])

        self._lancer()

        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.appellation, "Margaux")
        self.assertEqual(self.cuvee.provenance["appellation"]["canal"], "lwin")

    def test_code_deja_revendique_reste_au_premier_arrive(self):
        """Le code LWIN porte une contrainte d'unicité : le premier arrivé le
        garde (même règle que `ingest._completer_identites`)."""
        autre = Cuvee.objects.create(
            domaine=self.domaine, nom="Autre Vin", couleur=Cuvee.Couleur.ROUGE,
            lwin_code="1011247",
        )

        sortie = self._lancer()

        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.lwin_code, "")
        self.assertEqual(Cuvee.objects.get(pk=autre.pk).lwin_code, "1011247")
        self.assertIn("1 codes déjà pris", sortie)

    def test_seconde_passe_n_examine_que_le_reste(self):
        self._lancer()
        sortie = self._lancer()

        self.assertIn("0 cuvées examinées", sortie)
        self.assertEqual(SourceObservation.objects.filter(canal="lwin").count(), 1)

    def test_producteur_ecrit_autrement_est_rapproche(self):
        """Le repli flou rattrape une graphie différente du producteur, à
        condition qu'un token au moins soit commun."""
        domaine = Domaine.objects.create(nom="Chateau  Margaux SA", region="")
        cuvee = Cuvee.objects.create(
            domaine=domaine, nom="Pavillon Rouge", couleur=Cuvee.Couleur.ROUGE
        )
        ReferenceLwin.objects.create(
            lwin="1011248", producteur="Chateau Margaux", vin="Pavillon Rouge",
            region="Bordeaux", sous_region="Margaux", couleur="ROUGE",
        )

        self._lancer()

        cuvee.refresh_from_db()
        self.assertEqual(cuvee.lwin_code, "1011248")

    def test_producteur_inconnu_du_dump_reste_silencieux(self):
        domaine = Domaine.objects.create(nom="Bodega Inconnue", region="")
        cuvee = Cuvee.objects.create(
            domaine=domaine, nom="Tinto Joven", couleur=Cuvee.Couleur.ROUGE
        )

        self._lancer()

        cuvee.refresh_from_db()
        self.assertEqual(cuvee.lwin_code, "")

    def test_referentiel_vide_et_seuils_invalides(self):
        ReferenceLwin.objects.all().delete()
        with self.assertRaises(CommandError):
            self._lancer()
        ReferenceLwin.objects.create(lwin="1", producteur="X", vin="Y")
        with self.assertRaises(CommandError):
            self._lancer(seuil=1.5)
        with self.assertRaises(CommandError):
            self._lancer(seuil_producteur=0)

    def test_limite(self):
        Cuvee.objects.create(
            domaine=self.domaine, nom="Pavillon Rouge", couleur=Cuvee.Couleur.ROUGE
        )
        sortie = self._lancer(limite=1)
        self.assertIn("1 cuvées examinées", sortie)


class ConsolidationIdentiteTests(TestCase):
    """`couleur` et `appellation` sont désormais arbitrés, pas figés à la création."""

    def setUp(self):
        self.domaine = Domaine.objects.create(nom="Dom", region="")
        self.cuvee = Cuvee.objects.create(
            domaine=self.domaine, nom="C", couleur=Cuvee.Couleur.AUTRE
        )

    def _observer(self, canal, confiance, **champs):
        enregistrer_observation(
            self.cuvee, canal=canal, champs=champs, confiance=confiance
        )

    def test_appellation_corrigee_par_le_canal_le_plus_sur(self):
        """Le cas qui motive le changement : une cuvée créée sans appellation par
        un canal qui l'ignore la gardait vide à jamais."""
        self._observer("xwines", 0.60, appellation="")
        self._observer("lwin", 0.95, appellation="Margaux")

        consolider(self.cuvee)

        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.appellation, "Margaux")
        self.assertEqual(self.cuvee.provenance["appellation"]["canal"], "lwin")

    def test_couleur_autre_ne_prime_jamais_sur_une_couleur_connue(self):
        """`AUTRE` est une absence d'information : même affirmée par le canal le
        plus fiable, elle ne doit pas effacer le rouge lu sur l'étiquette."""
        self._observer("claude", 0.75, couleur="ROUGE")
        self._observer("lwin", 0.95, couleur=Cuvee.Couleur.AUTRE)

        consolider(self.cuvee)

        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.couleur, "ROUGE")
        self.assertEqual(self.cuvee.provenance["couleur"]["canal"], "claude")

    def test_couleur_autre_est_relevee_par_un_canal_qui_sait(self):
        self._observer("lwin", 0.90, couleur="BLANC")

        consolider(self.cuvee)

        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.couleur, "BLANC")

    def test_couleur_hors_nomenclature_est_ignoree(self):
        """Une observation garde la couleur telle que le canal l'a affirmée, sans
        le garde-fou de `upsert_cuvee` : la projeter sans revalider remettrait en
        base une couleur inexistante (Django ne vérifie pas `choices` au save)."""
        self.cuvee.couleur = Cuvee.Couleur.ROUGE
        self.cuvee.save(update_fields=["couleur"])
        self._observer("claude", 0.99, couleur="MAUVE")

        consolider(self.cuvee)

        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.couleur, Cuvee.Couleur.ROUGE)

    def test_aucune_source_ne_laisse_la_valeur_en_place(self):
        """La consolidation ne supprime jamais une valeur qu'aucune source ne
        contredit (règle générale, vérifiée sur les deux nouveaux champs)."""
        self.cuvee.couleur = Cuvee.Couleur.ROSE
        self.cuvee.appellation = "Bandol"
        self.cuvee.save(update_fields=["couleur", "appellation"])
        self._observer("wineapi", 0.70, description="Un vin.")

        consolider(self.cuvee)

        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.couleur, Cuvee.Couleur.ROSE)
        self.assertEqual(self.cuvee.appellation, "Bandol")
