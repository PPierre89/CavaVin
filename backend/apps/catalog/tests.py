import json
import os
import urllib.error
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .enrichment import EnrichmentError, NormalizedWine
from .enrichment import normalize
from .enrichment.openfoodfacts import OpenFoodFactsProvider
from .enrichment.wineapi import WineApiProvider
from . import sommellerie, wine_profile
from .ingest import enrich_cuvee_from_wineapi, upsert_cuvee
from .models import Cepage, Cuvee, Domaine
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

    def test_robuste_aux_doublons_preexistants(self):
        """Des doublons en base (ex: scans concurrents passés) ne doivent pas faire
        planter les scans suivants : on retourne le plus ancien, sans exception."""
        domaine = Domaine.objects.create(nom="Dom")
        c1 = Cuvee.objects.create(domaine=domaine, nom="A", couleur="ROUGE", code_barres="555")
        Cuvee.objects.create(domaine=domaine, nom="B", couleur="ROUGE", code_barres="555")

        cuvee, created = upsert_cuvee(
            NormalizedWine(domaine_nom="Dom", cuvee_nom="Peu importe", code_barres="555")
        )
        self.assertFalse(created)
        self.assertEqual(cuvee.pk, c1.pk)  # le plus ancien gagne


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
}


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

    def test_detail_vide_donne_des_valeurs_neutres(self):
        d = wine_profile.normalize_detail({})
        self.assertEqual(d["region"], "")
        self.assertIsNone(d["note_moyenne"])
        self.assertEqual(d["accords"], [])
        self.assertEqual(d["cepages"], [])


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
        self.assertIsNotNone(self.cuvee.enrichi_le)
        self.assertTrue(self.cuvee.cepages.filter(nom="Merlot").exists())

    def test_ne_remplace_pas_une_valeur_par_du_vide(self):
        self.cuvee.description = "Déjà là"
        self.cuvee.save()
        enrich_cuvee_from_wineapi(self.cuvee, {"body": "Full-bodied"})  # pas de description
        self.cuvee.refresh_from_db()
        self.assertEqual(self.cuvee.description, "Déjà là")  # conservé
        self.assertEqual(self.cuvee.corps, "Full-bodied")  # ajouté

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
