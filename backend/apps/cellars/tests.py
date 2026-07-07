from django.contrib.auth import get_user_model
from django.test import TestCase

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
