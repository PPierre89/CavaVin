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

    class Disposition(models.TextChoices):
        """Façon dont les bouteilles s'empilent d'une rangée à l'autre."""

        ALIGNE = "ALIGNE", "Aligné"
        DECALE_GAUCHE = "DECALE_GAUCHE", "Décalé à gauche"
        DECALE_DROITE = "DECALE_DROITE", "Décalé à droite"

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
    # Disposition en grille : une rangée = nb_colonnes bouteilles, empilée sur
    # nb_rangees niveaux. Quand les deux sont renseignés, la capacité en découle.
    nb_colonnes = models.PositiveIntegerField(
        null=True, blank=True, help_text="Bouteilles par rangée (largeur)."
    )
    nb_rangees = models.PositiveIntegerField(
        null=True, blank=True, help_text="Nombre de rangées empilées (hauteur)."
    )
    disposition = models.CharField(
        max_length=13,
        choices=Disposition.choices,
        default=Disposition.ALIGNE,
        help_text="Empilement des bouteilles d'une rangée à l'autre.",
    )

    class Meta:
        ordering = ["cave", "parent_id", "ligne", "colonne", "nom"]

    def save(self, *args, **kwargs):
        # Une grille est auto-suffisante : sa capacité découle de ses dimensions,
        # on n'oblige donc pas l'appelant à la maintenir en cohérence à la main.
        if self.nb_colonnes and self.nb_rangees:
            self.capacite = self.nb_colonnes * self.nb_rangees
        super().save(*args, **kwargs)

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
