from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Cave, Emplacement


class EmplacementCheminTests(TestCase):
    """Vérifie le chemin lisible construit en remontant l'arborescence des emplacements."""

    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(username="testeur", password="x")
        cls.cave = Cave.objects.create(proprietaire=user, nom="Cave principale")

        cls.armoire = Emplacement.objects.create(
            cave=cls.cave,
            nom="Armoire 1",
            type_emplacement=Emplacement.TypeEmplacement.ARMOIRE,
        )
        cls.clayette = Emplacement.objects.create(
            cave=cls.cave,
            parent=cls.armoire,
            nom="Clayette 3",
            type_emplacement=Emplacement.TypeEmplacement.CLAYETTE,
        )
        cls.case = Emplacement.objects.create(
            cave=cls.cave,
            parent=cls.clayette,
            nom="B4",
            type_emplacement=Emplacement.TypeEmplacement.CASE,
        )

    def test_chemin_racine(self):
        """Un emplacement sans parent a pour chemin son seul nom."""
        self.assertEqual(self.armoire.chemin(), "Armoire 1")

    def test_chemin_arborescence_complete(self):
        """Le chemin remonte tous les parents, du plus haut au plus bas."""
        self.assertEqual(self.case.chemin(), "Armoire 1 > Clayette 3 > B4")

    def test_str_prefixe_par_la_cave(self):
        """__str__ préfixe le chemin par le nom de la cave."""
        self.assertEqual(str(self.case), "Cave principale > Armoire 1 > Clayette 3 > B4")


class EmplacementCycleTests(APITestCase):
    """L'API refuse tout parent qui créerait un cycle (sinon chemin() boucle à l'infini)."""

    def setUp(self):
        user = get_user_model().objects.create_user(username="alice", password="x")
        self.cave = Cave.objects.create(proprietaire=user, nom="Cave")
        self.armoire = Emplacement.objects.create(
            cave=self.cave, nom="Armoire", type_emplacement=Emplacement.TypeEmplacement.ARMOIRE
        )
        self.clayette = Emplacement.objects.create(
            cave=self.cave, parent=self.armoire, nom="Clayette",
            type_emplacement=Emplacement.TypeEmplacement.CLAYETTE,
        )
        self.client.force_authenticate(user)

    def test_refuse_d_etre_son_propre_parent(self):
        url = reverse("emplacement-detail", args=[self.armoire.pk])
        resp = self.client.patch(url, {"parent": self.armoire.pk})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("parent", resp.data)

    def test_refuse_un_cycle_via_descendant(self):
        # Armoire -> Clayette existe déjà ; rattacher Armoire sous Clayette = cycle.
        url = reverse("emplacement-detail", args=[self.armoire.pk])
        resp = self.client.patch(url, {"parent": self.clayette.pk})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("parent", resp.data)
