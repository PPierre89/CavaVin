from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import Parametre, ReferenceLwin

User = get_user_model()


class WineapiTimeoutInvariantTests(SimpleTestCase):
    """Garde-fou : les timeouts wineapi par défaut doivent rester sous le timeout
    worker gunicorn le plus court rencontré (30 s), sinon un appel lent (vision)
    fait tuer le worker au lieu de dégrader en 404. Voir config/settings.py."""

    def test_timeouts_par_defaut_surs(self):
        self.assertLess(settings.WINEAPI_TIMEOUT, 30)
        self.assertLess(settings.WINEAPI_IMAGE_TIMEOUT, 30)


class RegisterViewTests(APITestCase):
    """POST /api/auth/register/ : création de compte + tokens JWT immédiats."""

    def setUp(self):
        cache.clear()  # compteur de throttle "auth"
        self.url = reverse("register")

    def test_cree_le_compte_et_renvoie_des_tokens(self):
        resp = self.client.post(
            self.url,
            {"username": "bob", "email": "bob@example.com", "password": "un-mot-de-passe-costaud"},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)
        self.assertTrue(User.objects.filter(username="bob").exists())

    def test_username_deja_pris_400(self):
        User.objects.create_user(username="bob", password="x")
        resp = self.client.post(
            self.url, {"username": "bob", "password": "un-mot-de-passe-costaud"}
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_username_deja_pris_casse_differente_400(self):
        # « Bob » ne doit pas pouvoir être créé si « bob » existe : sinon deux
        # comptes distincts se forment et l'utilisateur « perd » ses données.
        User.objects.create_user(username="bob", password="x")
        resp = self.client.post(
            self.url, {"username": "Bob", "password": "un-mot-de-passe-costaud"}
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.filter(username__iexact="bob").count(), 1)

    def test_mot_de_passe_trop_faible_400(self):
        resp = self.client.post(self.url, {"username": "alice", "password": "123"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", resp.data)


class LoginThrottleTests(APITestCase):
    """Le login JWT (`/api/auth/token/`) est plafonné (scope ``auth``) pour couper
    le brute-force de mot de passe : au-delà de la limite -> 429."""

    def setUp(self):
        cache.clear()  # compteur de throttle "auth"
        self.url = reverse("token_obtain_pair")
        User.objects.create_user(username="bob", password="un-mot-de-passe-costaud")

    # DRF fige DEFAULT_THROTTLE_RATES à l'import : on patche l'attribut de classe.
    @patch.dict(
        "rest_framework.throttling.SimpleRateThrottle.THROTTLE_RATES",
        {"auth": "3/min"},
    )
    def test_429_apres_trop_de_tentatives(self):
        for _ in range(3):
            resp = self.client.post(self.url, {"username": "bob", "password": "faux"})
            self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)
        # La tentative suivante est bloquée par le throttle, pas par les identifiants.
        resp = self.client.post(self.url, {"username": "bob", "password": "faux"})
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class LoginCasseInsensibleTests(APITestCase):
    """La connexion JWT retrouve le compte quelle que soit la casse de
    l'identifiant : c'est ce qui permet de se reconnecter au même compte (et
    donc à ses données) depuis un autre appareil malgré la majuscule
    automatique des claviers mobiles."""

    def setUp(self):
        cache.clear()  # compteur de throttle "auth"
        self.url = reverse("token_obtain_pair")
        User.objects.create_user(username="pierre", password="un-mot-de-passe-costaud")

    def test_login_casse_differente_reussit(self):
        resp = self.client.post(
            self.url, {"username": "Pierre", "password": "un-mot-de-passe-costaud"}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("access", resp.data)

    def test_login_mauvais_mot_de_passe_401(self):
        resp = self.client.post(self.url, {"username": "Pierre", "password": "faux"})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class MeViewTests(APITestCase):
    """GET /api/auth/me/ : profil du compte connecté (rôle inclus)."""

    def test_anonyme_401(self):
        resp = self.client.get(reverse("me"))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_renvoie_le_role_staff(self):
        user = User.objects.create_user(
            username="chef", password="x", email="chef@example.com", is_staff=True
        )
        self.client.force_authenticate(user=user)
        resp = self.client.get(reverse("me"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["username"], "chef")
        self.assertEqual(resp.data["email"], "chef@example.com")
        self.assertTrue(resp.data["is_staff"])
        self.assertFalse(resp.data["is_superuser"])


class AdminApercuTests(APITestCase):
    """GET /api/admin-panel/apercu/ : tableau de bord réservé au staff."""

    def setUp(self):
        self.url = reverse("admin-apercu")

    def test_non_staff_403(self):
        user = User.objects.create_user(username="lambda", password="x")
        self.client.force_authenticate(user=user)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    def test_anonyme_401(self):
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_staff_recoit_les_agregats(self):
        staff = User.objects.create_user(username="chef", password="x", is_staff=True)
        User.objects.create_user(username="autre", password="x")
        self.client.force_authenticate(user=staff)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["utilisateurs"]["total"], 2)
        self.assertEqual(resp.data["utilisateurs"]["staff"], 1)
        self.assertIn("catalogue", resp.data)
        self.assertIn("stock", resp.data)
        self.assertEqual(resp.data["systeme"]["version"], settings.APP_VERSION)
        # Les fournisseurs d'enrichissement sont listés avec leur état, sans clés.
        self.assertTrue(any(p["nom"] == "openfoodfacts" for p in resp.data["systeme"]["providers"]))
        self.assertNotIn("cle", str(resp.data["systeme"]["providers"]))


class AdminUtilisateurTests(APITestCase):
    """/api/admin-panel/utilisateurs/ : gestion des comptes (staff uniquement)."""

    def setUp(self):
        self.staff = User.objects.create_user(username="chef", password="x", is_staff=True)
        self.cible = User.objects.create_user(username="cible", password="x")
        self.liste = reverse("admin-utilisateur-list")
        self.detail = reverse("admin-utilisateur-detail", args=[self.cible.pk])

    def test_non_staff_403(self):
        self.client.force_authenticate(user=self.cible)
        self.assertEqual(self.client.get(self.liste).status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_liste_les_comptes(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.get(self.liste)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 2)

    def test_desactiver_un_compte(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.patch(self.detail, {"is_active": False})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.cible.refresh_from_db()
        self.assertFalse(self.cible.is_active)

    def test_promouvoir_staff(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.patch(self.detail, {"is_staff": True})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.cible.refresh_from_db()
        self.assertTrue(self.cible.is_staff)

    def test_impossible_de_se_modifier_soi_meme(self):
        self.client.force_authenticate(user=self.staff)
        url = reverse("admin-utilisateur-detail", args=[self.staff.pk])
        resp = self.client.patch(url, {"is_staff": False})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_staff)

    def test_impossible_de_se_supprimer_soi_meme(self):
        self.client.force_authenticate(user=self.staff)
        url = reverse("admin-utilisateur-detail", args=[self.staff.pk])
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(User.objects.filter(pk=self.staff.pk).exists())

    def test_staff_non_superuser_ne_touche_pas_un_superuser(self):
        boss = User.objects.create_superuser(username="boss", password="x")
        self.client.force_authenticate(user=self.staff)
        url = reverse("admin-utilisateur-detail", args=[boss.pk])
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(pk=boss.pk).exists())

    def test_supprimer_un_compte(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.delete(self.detail)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=self.cible.pk).exists())

    def test_pas_de_creation_de_compte(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.post(self.liste, {"username": "nouveau", "password": "x"})
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class AdminConfigurationTests(APITestCase):
    """/api/admin-panel/configuration/ : paramétrage à chaud des clés d'API (staff)."""

    def setUp(self):
        cache.clear()  # cache des overrides runtime_config
        self.url = reverse("admin-configuration")
        self.staff = User.objects.create_user(username="chef", password="x", is_staff=True)

    def tearDown(self):
        cache.clear()

    def test_non_staff_403(self):
        lambda_user = User.objects.create_user(username="lambda", password="x")
        self.client.force_authenticate(user=lambda_user)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(WINEAPI_KEY="cle-env-secrete-1234")
    def test_get_masque_les_secrets(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        params = {p["cle"]: p for p in resp.data["parametres"]}
        wineapi = params["WINEAPI_KEY"]
        self.assertTrue(wineapi["secret"])
        self.assertTrue(wineapi["configure"])
        self.assertEqual(wineapi["source"], "env")
        # La clé n'est jamais renvoyée en clair : seuls les 4 derniers caractères.
        self.assertNotIn("cle-env-secrete", wineapi["apercu"])
        self.assertTrue(wineapi["apercu"].endswith("1234"))

    def test_put_enregistre_un_override(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.put(self.url, {"cle": "WINEAPI_KEY", "valeur": "nouvelle-cle-abcd"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Parametre.objects.get(cle="WINEAPI_KEY").valeur, "nouvelle-cle-abcd")
        params = {p["cle"]: p for p in resp.data["parametres"]}
        self.assertEqual(params["WINEAPI_KEY"]["source"], "base")
        self.assertTrue(params["WINEAPI_KEY"]["apercu"].endswith("abcd"))

    def test_put_vide_efface_loverride(self):
        Parametre.objects.create(cle="WINEAPI_KEY", valeur="ancienne")
        self.client.force_authenticate(user=self.staff)
        resp = self.client.put(self.url, {"cle": "WINEAPI_KEY", "valeur": ""})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(Parametre.objects.filter(cle="WINEAPI_KEY").exists())

    def test_put_cle_inconnue_400(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.put(self.url, {"cle": "DJANGO_SECRET_KEY", "valeur": "x"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Parametre.objects.exists())

    def test_override_change_letat_du_provider(self):
        """Un override en base active le provider wineapi sans redémarrage."""
        from apps.catalog.enrichment import get_provider

        with override_settings(WINEAPI_KEY=""):
            cache.clear()
            self.assertIsNone(get_provider("wineapi"))  # aucune clé -> désactivé
            self.client.force_authenticate(user=self.staff)
            self.client.put(self.url, {"cle": "WINEAPI_KEY", "valeur": "cle-a-chaud"})
            self.assertIsNotNone(get_provider("wineapi"))  # override -> activé


class AdminImportLwinTests(APITestCase):
    """/api/admin-panel/import-lwin/ : upload du dump LWIN (staff)."""

    _ENTETE = "LWIN,STATUS,PRODUCER_TITLE,PRODUCER_NAME,WINE,COUNTRY,REGION,SUB_REGION,COLOUR,TYPE,SUB_TYPE,CLASSIFICATION\n"

    def setUp(self):
        self.url = reverse("admin-import-lwin")
        self.staff = User.objects.create_user(username="chef", password="x", is_staff=True)

    def _fichier(self, corps: str, nom="lwin.csv", content_type="text/csv"):
        return SimpleUploadedFile(nom, (self._ENTETE + corps).encode("utf-8"), content_type=content_type)

    def test_non_staff_403(self):
        lambda_user = User.objects.create_user(username="lambda", password="x")
        self.client.force_authenticate(user=lambda_user)
        resp = self.client.post(self.url, {"fichier": self._fichier("")}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_sans_fichier_400(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_format_non_supporte_400(self):
        self.client.force_authenticate(user=self.staff)
        fichier = SimpleUploadedFile("dump.pdf", b"%PDF-1.4", content_type="application/pdf")
        resp = self.client.post(self.url, {"fichier": fichier}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_csv_ingere_le_referentiel(self):
        self.client.force_authenticate(user=self.staff)
        corps = (
            "1011247,Live,Chateau,Margaux,,France,Bordeaux,Margaux,Red,Wine,Still,1er Cru\n"
            "1055555,Live,,Bollinger,Grande Annee,France,Champagne,,White,Wine,Sparkling,\n"
        )
        resp = self.client.post(self.url, {"fichier": self._fichier(corps)}, format="multipart")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["importes"], 2)
        self.assertEqual(resp.data["total"], 2)
        self.assertEqual(ReferenceLwin.objects.count(), 2)
        self.assertEqual(ReferenceLwin.objects.get(lwin="1055555").couleur, "BULLES")
