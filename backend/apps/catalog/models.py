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
        help_text="Identifiant dans une base de référence externe (ex: wineapi.io).",
    )

    # --- Enrichissement wineapi.io (persisté sur la cuvée, indépendant du millésime) ---
    region = models.CharField(max_length=255, blank=True, default="")
    pays = models.CharField(max_length=100, blank=True, default="")
    classification = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    elaborate = models.TextField(blank=True, default="")
    corps = models.CharField(max_length=50, blank=True, default="", help_text="Body wineapi (ex: Full-bodied).")
    acidite = models.CharField(max_length=50, blank=True, default="", help_text="Acidity wineapi.")
    degre_alcool = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    image_url = models.URLField(blank=True, default="")
    lwin_code = models.CharField(max_length=32, blank=True, default="")
    note_moyenne = models.DecimalField(
        max_digits=3, decimal_places=1, null=True, blank=True, help_text="Note communautaire /5."
    )
    nb_notes = models.PositiveIntegerField(null=True, blank=True)
    prix_min = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    prix_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    devise = models.CharField(max_length=8, blank=True, default="")
    accords = models.JSONField(default=list, blank=True, help_text="Accords mets-vins [{nom, emoji, confiance}].")
    scores = models.JSONField(default=list, blank=True, help_text="Avis critiques [{reviewer, score, ...}].")
    prix_marchands = models.JSONField(
        default=list, blank=True, help_text="Prix marchands wineapi [{marchand, prix, devise, url}]."
    )
    # Payload brut complet du dernier `GET /wines/{id}` wineapi.io. On conserve la
    # réponse telle quelle (au-delà des seuls champs mappés ci-dessus) pour ne
    # jamais perdre une information remontée par l'API — y compris les champs non
    # encore exploités ou ajoutés plus tard — et pouvoir re-dériver les champs de
    # fiche sans re-consommer le quota.
    wineapi_detail = models.JSONField(
        null=True, blank=True, help_text="Réponse brute du dernier GET /wines/{id} wineapi.io."
    )
    enrichi_le = models.DateTimeField(null=True, blank=True, help_text="Dernière synchro wineapi.")

    class Meta:
        ordering = ["domaine__nom", "nom"]

    def __str__(self):
        return f"{self.domaine.nom} - {self.nom}"
