from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.catalog.models import Cuvee, Domaine
from apps.cellars.models import Cave, Emplacement

from .models import Bouteille, MouvementStock

User = get_user_model()


def _cuvee(nom="Clos Test"):
    domaine = Domaine.objects.create(nom=f"Domaine {nom}")
    return Cuvee.objects.create(domaine=domaine, nom=nom, couleur=Cuvee.Couleur.ROUGE)


class ConsommerTests(APITestCase):
    """Action POST /api/bouteilles/{id}/consommer/ : décrément atomique + journal."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.bouteille = Bouteille.objects.create(
            proprietaire=self.user, cuvee=_cuvee(), quantite=6
        )
        self.url = reverse("bouteille-consommer", args=[self.bouteille.pk])
        self.client.force_authenticate(self.user)

    def test_consommer_decremente_le_stock_et_journalise(self):
        resp = self.client.post(self.url, {"quantite": 2, "occasion": "dîner"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.bouteille.refresh_from_db()
        self.assertEqual(self.bouteille.quantite, 4)

        mouvement = MouvementStock.objects.get(bouteille=self.bouteille)
        self.assertEqual(mouvement.type_mouvement, MouvementStock.TypeMouvement.CONSOMMATION)
        self.assertEqual(mouvement.quantite, 2)
        self.assertEqual(mouvement.occasion, "dîner")

    def test_consommer_au_dela_du_stock_refuse_sans_effet(self):
        resp = self.client.post(self.url, {"quantite": 99})

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.bouteille.refresh_from_db()
        self.assertEqual(self.bouteille.quantite, 6)  # stock inchangé
        self.assertFalse(MouvementStock.objects.exists())  # aucun mouvement journalisé


class CapaciteTests(APITestCase):
    """La création d'une bouteille ne peut pas dépasser la capacité de l'emplacement."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.cuvee = _cuvee()
        self.cave = Cave.objects.create(proprietaire=self.user, nom="Cave principale")
        self.emplacement = Emplacement.objects.create(
            cave=self.cave,
            nom="Casier A",
            type_emplacement=Emplacement.TypeEmplacement.CASIER,
            capacite=2,
        )
        self.url = reverse("bouteille-list")
        self.client.force_authenticate(self.user)

    def _payload(self, quantite):
        return {"cuvee": self.cuvee.pk, "emplacement": self.emplacement.pk, "quantite": quantite}

    def test_placement_dans_la_capacite_ok(self):
        resp = self.client.post(self.url, self._payload(2))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_placement_depassant_la_capacite_refuse(self):
        resp = self.client.post(self.url, self._payload(3))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("emplacement", resp.data)
        self.assertEqual(Bouteille.objects.count(), 0)


class CloisonnementRgpdTests(APITestCase):
    """Un utilisateur ne peut ni voir, ni consommer, ni cibler les objets d'un autre."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.bouteille_alice = Bouteille.objects.create(
            proprietaire=self.alice, cuvee=_cuvee(), quantite=3
        )
        self.cave_alice = Cave.objects.create(proprietaire=self.alice, nom="Cave d'Alice")
        self.emplacement_alice = Emplacement.objects.create(
            cave=self.cave_alice,
            nom="Casier A",
            type_emplacement=Emplacement.TypeEmplacement.CASIER,
        )
        self.client.force_authenticate(self.bob)

    def test_bob_ne_voit_pas_la_bouteille_d_alice(self):
        resp = self.client.get(reverse("bouteille-list"))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 0)

        detail = self.client.get(reverse("bouteille-detail", args=[self.bouteille_alice.pk]))
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)

    def test_bob_ne_peut_pas_consommer_la_bouteille_d_alice(self):
        url = reverse("bouteille-consommer", args=[self.bouteille_alice.pk])
        resp = self.client.post(url, {"quantite": 1})

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.bouteille_alice.refresh_from_db()
        self.assertEqual(self.bouteille_alice.quantite, 3)  # stock d'Alice intact

    def test_bob_ne_peut_pas_placer_dans_un_emplacement_d_alice(self):
        resp = self.client.post(
            reverse("bouteille-list"),
            {"cuvee": _cuvee("Autre").pk, "emplacement": self.emplacement_alice.pk, "quantite": 1},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("emplacement", resp.data)
