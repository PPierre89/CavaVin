from django.db import models


class Domaine(models.Model):
    """Producteur / château / maison."""

    nom = models.CharField(max_length=255)
    region = models.CharField(max_length=255, blank=True)
    pays = models.CharField(max_length=100, blank=True)
    site_web = models.URLField(blank=True)

    class Meta:
        ordering = ["nom"]
        constraints = [
            models.UniqueConstraint(fields=["nom", "region"], name="unique_domaine_par_region"),
        ]

    def __str__(self):
        return self.nom


class Cepage(models.Model):
    """Cépage (Chardonnay, Pinot Noir, ...)."""

    nom = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Cuvee(models.Model):
    """Un vin donné d'un domaine, indépendamment du millésime."""

    class Couleur(models.TextChoices):
        ROUGE = "ROUGE", "Rouge"
        BLANC = "BLANC", "Blanc"
        ROSE = "ROSE", "Rosé"
        BULLES = "BULLES", "Bulles"
        AUTRE = "AUTRE", "Autre"

    domaine = models.ForeignKey(Domaine, on_delete=models.CASCADE, related_name="cuvees")
    nom = models.CharField(max_length=255)
    appellation = models.CharField(max_length=255, blank=True)
    couleur = models.CharField(max_length=10, choices=Couleur.choices)
    cepages = models.ManyToManyField(Cepage, blank=True, related_name="cuvees")
    code_barres = models.CharField(max_length=64, blank=True, db_index=True)
    reference_externe_id = models.CharField(
        max_length=128,
        blank=True,
        help_text="Identifiant dans une base de référence externe (ex: Wine-Searcher, Vivino).",
    )

    class Meta:
        ordering = ["domaine__nom", "nom"]

    def __str__(self):
        return f"{self.domaine.nom} - {self.nom}"
