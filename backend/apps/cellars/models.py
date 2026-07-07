from django.conf import settings
from django.db import models


class Cave(models.Model):
    """Une cave physique appartenant à un utilisateur (ex: 'Cave principale', 'Garage')."""

    proprietaire = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="caves"
    )
    nom = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Emplacement(models.Model):
    """
    Noeud d'une arborescence de rangement (ex: Cave > Armoire 1 > Clayette 3 > Case B4).
    Auto-référencé pour modéliser n'importe quelle profondeur de structure.
    """

    class TypeEmplacement(models.TextChoices):
        ARMOIRE = "ARMOIRE", "Armoire"
        CASIER = "CASIER", "Casier"
        CLAYETTE = "CLAYETTE", "Clayette"
        CAISSE = "CAISSE", "Caisse bois"
        CASE = "CASE", "Case individuelle"

    cave = models.ForeignKey(Cave, on_delete=models.CASCADE, related_name="emplacements")
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="enfants"
    )
    nom = models.CharField(max_length=255, help_text="Ex: Armoire 1, Clayette 3, B4")
    type_emplacement = models.CharField(max_length=10, choices=TypeEmplacement.choices)
    capacite = models.PositiveIntegerField(
        null=True, blank=True, help_text="Nombre de bouteilles que ce noeud peut contenir."
    )
    ligne = models.PositiveIntegerField(null=True, blank=True, help_text="Position en ligne pour l'affichage en grille.")
    colonne = models.PositiveIntegerField(null=True, blank=True, help_text="Position en colonne pour l'affichage en grille.")

    class Meta:
        ordering = ["cave", "parent_id", "ligne", "colonne", "nom"]

    def __str__(self):
        return f"{self.cave.nom} > {self.chemin()}"

    def chemin(self):
        """Chemin complet lisible, ex: Armoire 1 > Clayette 3 > B4."""
        segments = [self.nom]
        node = self.parent
        while node is not None:
            segments.insert(0, node.nom)
            node = node.parent
        return " > ".join(segments)
