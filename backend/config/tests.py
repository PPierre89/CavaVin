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

    def test_mot_de_passe_trop_faible_400(self):
        resp = self.client.post(self.url, {"username": "alice", "password": "123"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", resp.data)
