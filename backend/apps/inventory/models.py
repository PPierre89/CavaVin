from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.catalog import apogee


class Bouteille(models.Model):
    """
    Une ligne de stock : N bouteilles identiques (même cuvée + millésime)
    à un emplacement donné (ou non encore placées).
    """

    class Statut(models.TextChoices):
        """Statut de dégustation — **calculé** à la lecture (cf. ``statut_apogee``),
        jamais stocké : la fenêtre d'apogée dépend de l'année courante, une colonne
        serait périmée dès le 1er janvier suivant."""

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

    def fenetre_apogee(self):
        """Fenêtre d'apogée effective (année de début, année de fin).

        La saisie manuelle prime : si au moins une borne est renseignée sur la
        bouteille, on la respecte telle quelle. Sinon on l'estime depuis la
        couleur de la cuvée et le millésime, affinée par les cépages et la
        qualité du millésime de la région (base sommelière, cf. apogee.py).
        """
        if self.apogee_debut is not None or self.apogee_fin is not None:
            return (self.apogee_debut, self.apogee_fin)
        # Table de qualité des millésimes sourçable (mise en cache) ; apogee reste
        # pur et reçoit la table par injection.
        from apps.catalog.models import MillesimeReference

        return apogee.fenetre_apogee(
            self.cuvee.couleur,
            self.millesime,
            cepages=[c.nom for c in self.cuvee.cepages.all()],
            region=self.cuvee.region,
            millesimes=MillesimeReference.table(),
        )

    @property
    def statut_apogee(self):
        """Statut calculé (à garder / à boire / dépassé) déduit de la fenêtre
        d'apogée effective et de l'année courante — pilote le code couleur."""
        debut, fin = self.fenetre_apogee()
        return apogee.statut_pour_fenetre(debut, fin)

    def synchroniser_rangements(self):
        """Libère les cases en trop quand la quantité diminue (ou tombe à 0).

        Une ligne de stock ne peut pas occuper plus de cases qu'elle ne compte
        d'unités : après une consommation, on retire d'abord les cases d'index
        les plus élevés (les dernières rangées).
        """
        surplus = self.rangements.count() - self.quantite
        if surplus > 0:
            a_liberer = list(
                self.rangements.order_by("-case").values_list("pk", flat=True)[:surplus]
            )
            Rangement.objects.filter(pk__in=a_liberer).delete()


class Rangement(models.Model):
    """
    Assignation d'une bouteille physique à une case précise d'un emplacement en
    grille (placement « case par case »). Une case ne contient qu'une bouteille ;
    une ligne de stock (Bouteille de quantité N) peut occuper jusqu'à N cases,
    rangées une par une.
    """

    bouteille = models.ForeignKey(
        Bouteille, on_delete=models.CASCADE, related_name="rangements"
    )
    emplacement = models.ForeignKey(
        "cellars.Emplacement", on_delete=models.CASCADE, related_name="rangements"
    )
    case = models.PositiveIntegerField(
        help_text="Index 0-based de la case dans la grille (ligne × nb_colonnes + colonne)."
    )

    class Meta:
        ordering = ["emplacement_id", "case"]
        constraints = [
            models.UniqueConstraint(
                fields=["emplacement", "case"],
                name="rangement_case_unique_par_emplacement",
            )
        ]

    def __str__(self):
        return f"{self.bouteille} → case {self.case}"


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


class NoteDegustation(models.Model):
    """
    Carnet de dégustation : appréciation personnelle d'un vin par un utilisateur.

    C'est un journal — plusieurs entrées sont possibles pour une même cuvée, au
    fil des dégustations. Sur la fiche vin, « Ma note » correspond à l'entrée la
    plus récente. Les données sont privées (cloisonnées par propriétaire), à la
    différence des notes communautaires (référentiel / wineapi).
    """

    proprietaire = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="degustations"
    )
    # PROTECT (et non CASCADE) : une note de dégustation est une donnée *privée*.
    # Supprimer une cuvée du catalogue mutualisé ne doit jamais effacer en cascade
    # le carnet d'un autre utilisateur (garde-fou RGPD, en plus de la permission
    # qui réserve déjà la suppression du catalogue au staff).
    cuvee = models.ForeignKey(
        "catalog.Cuvee", on_delete=models.PROTECT, related_name="degustations"
    )
    millesime = models.PositiveIntegerField(
        null=True, blank=True, help_text="Millésime dégusté (facultatif)."
    )
    note = models.DecimalField(
        max_digits=2,
        decimal_places=1,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text="Note personnelle de 0 à 5.",
    )
    commentaire = models.TextField(blank=True)

    # Curseurs de dégustation (0 à 5), facultatifs.
    acidite = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(5)])
    tanin = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(5)])
    fruit = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(5)])

    date_degustation = models.DateField(default=timezone.localdate)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_degustation", "-cree_le"]
        verbose_name = "Note de dégustation"
        verbose_name_plural = "Notes de dégustation"

    def __str__(self):
        return f"{self.cuvee} — {self.note}/5"
