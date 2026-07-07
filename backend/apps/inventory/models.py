from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Bouteille(models.Model):
    """
    Une ligne de stock : N bouteilles identiques (même cuvée + millésime)
    à un emplacement donné (ou non encore placées).
    """

    class Statut(models.TextChoices):
        A_GARDER = "A_GARDER", "À garder"
        A_BOIRE = "A_BOIRE", "À boire"
        DEPASSE = "DEPASSE", "Dépassé"

    proprietaire = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bouteilles",
        # Nullable pour absorber les lignes créées avant l'ajout du champ ;
        # l'API le renseigne toujours via CurrentUserDefault.
        null=True,
        blank=True,
    )
    cuvee = models.ForeignKey(
        "catalog.Cuvee", on_delete=models.PROTECT, related_name="bouteilles"
    )
    millesime = models.PositiveIntegerField(
        null=True, blank=True, help_text="Laisser vide pour un vin non millésimé."
    )
    emplacement = models.ForeignKey(
        "cellars.Emplacement",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bouteilles",
        help_text="Laisser vide si le placement physique n'a pas encore été fait.",
    )
    quantite = models.PositiveIntegerField(default=1)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.A_GARDER)

    prix_achat = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    date_achat = models.DateField(null=True, blank=True)

    apogee_debut = models.PositiveIntegerField(
        null=True, blank=True, help_text="Année de début de la fenêtre d'apogée."
    )
    apogee_fin = models.PositiveIntegerField(
        null=True, blank=True, help_text="Année de fin de la fenêtre d'apogée."
    )

    notes = models.TextField(blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    maj_le = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-cree_le"]
        verbose_name = "Bouteille (stock)"
        verbose_name_plural = "Bouteilles (stock)"

    def __str__(self):
        millesime = self.millesime or "NM"
        return f"{self.cuvee} {millesime} x{self.quantite}"

    def clean(self):
        if self.apogee_debut and self.apogee_fin and self.apogee_debut > self.apogee_fin:
            raise ValidationError("L'année de début d'apogée doit précéder l'année de fin.")


class MouvementStock(models.Model):
    """Historique des entrées/sorties/consommations sur une ligne de stock."""

    class TypeMouvement(models.TextChoices):
        ENTREE = "ENTREE", "Entrée"
        SORTIE = "SORTIE", "Sortie"
        CONSOMMATION = "CONSOMMATION", "Consommation"
        AJUSTEMENT = "AJUSTEMENT", "Ajustement d'inventaire"

    bouteille = models.ForeignKey(
        Bouteille, on_delete=models.CASCADE, related_name="mouvements"
    )
    type_mouvement = models.CharField(max_length=15, choices=TypeMouvement.choices)
    quantite = models.PositiveIntegerField(default=1)
    date = models.DateTimeField(auto_now_add=True)
    occasion = models.CharField(
        max_length=255, blank=True, help_text="Ex: Anniversaire, dîner avec X"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.get_type_mouvement_display()} x{self.quantite} - {self.bouteille}"
