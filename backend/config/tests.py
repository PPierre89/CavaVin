from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

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
