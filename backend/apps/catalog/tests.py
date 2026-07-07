import json
import urllib.error
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .enrichment import EnrichmentError, NormalizedWine
from .enrichment import normalize
from .enrichment.openfoodfacts import OpenFoodFactsProvider
from .enrichment.wineapi import WineApiProvider
from .ingest import upsert_cuvee
from .models import Cepage, Cuvee, Domaine

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


class _FakeProvider:
    """Provider factice pour piloter la cascade des vues sans réseau."""

    def __init__(self, wine=None, error=None):
        self._wine = wine
        self._error = error

    def lookup_by_barcode(self, ean):
        return self._wine

    def lookup_by_text(self, query):
        if self._error:
            raise self._error
        return self._wine


class ScanCodeBarresViewTests(APITestCase):
    """US 01 — POST /api/scan-code-barres/ : cache local, cascade externe, échec."""

    def setUp(self):
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
        )
        mock_providers.return_value = [_FakeProvider(wine=wine)]

        resp = self.client.post(self.url, {"code_barres": "3760012345888"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["source"], "openfoodfacts")
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


def _http_error(code):
    return urllib.error.HTTPError("https://api.test/x", code, "err", None, None)


@override_settings(
    WINEAPI_KEY="cle-de-test",
    WINEAPI_BASE_URL="https://api.test",
    WINEAPI_TIMEOUT=5,
)
class WineApiProviderTests(SimpleTestCase):
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
            "appellation": {"name": "Chablis"},
            "grapes": [{"name": "Chardonnay"}],
            "scores": [{"score": 92}, {"score": 88}],
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
