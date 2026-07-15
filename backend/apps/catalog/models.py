from collections import defaultdict
from decimal import Decimal

from django.core.cache import cache
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from . import apogee


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
        default=list, blank=True, help_text="Prix marchands wineapi [{marchand, prix, devise, url, releve_le}]."
    )
    # Série d'historique de prix accumulée au fil des synchros wineapi (une
    # observation {date, prix_min, prix_max, devise} par jour de relevé). Alimente
    # le graphe « Historique de prix » de la fiche.
    historique_prix = models.JSONField(
        default=list, blank=True, help_text="Historique de prix [{date, prix_min, prix_max, devise}]."
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
    # Carte de provenance de la fiche consolidée : pour chaque champ arbitré par
    # la consolidation (cf. consolidation.py, Phase 2), le canal retenu, sa date
    # de relevé et sa confiance — {champ: {canal, date, confiance}}. Permet de
    # savoir d'où vient chaque donnée et de re-arbitrer en cas de conflit.
    provenance = models.JSONField(
        default=dict, blank=True, help_text="Provenance par champ {champ: {canal, date, confiance}}."
    )

    class Meta:
        ordering = ["domaine__nom", "nom"]
        constraints = [
            # Clés d'identité canoniques : un code-barres (US 01) et une référence
            # externe (ex: wineapi.io) désignent un vin et un seul. Contraintes
            # *partielles* (seulement quand la valeur est renseignée) car ces
            # champs restent vides pour un vin saisi sans scan ni source externe.
            # Empêche la ré-accumulation de doublons décrite dans la revue
            # d'architecture (cf. docs/architecture-referentiel.md, D3).
            models.UniqueConstraint(
                fields=["code_barres"],
                condition=~models.Q(code_barres=""),
                name="unique_cuvee_code_barres",
            ),
            models.UniqueConstraint(
                fields=["reference_externe_id"],
                condition=~models.Q(reference_externe_id=""),
                name="unique_cuvee_reference_externe",
            ),
            # Le code LWIN (Liv-ex) est une identité de vin canonique : il
            # réconcilie les relevés LWIN entre eux et avec les autres canaux
            # (cf. docs/architecture-referentiel.md, Phase 3 / D4).
            models.UniqueConstraint(
                fields=["lwin_code"],
                condition=~models.Q(lwin_code=""),
                name="unique_cuvee_lwin_code",
            ),
        ]

    def __str__(self):
        return f"{self.domaine.nom} - {self.nom}"


class ReferenceLwin(models.Model):
    """Entrée du référentiel LWIN (Liv-ex Wine Identifiers).

    Base d'identités de vins (~200 000 entrées : producteur, vin, région, pays,
    couleur), importée localement via ``manage.py import_lwin`` depuis le dump
    XLSX/CSV gratuit de Liv-ex. Sert de repli 100 % local à l'identification
    (provider ``lwin``) : correspondance floue entre la sortie OCR / la saisie
    texte et un nom canonique, sans aucun appel réseau.
    """

    lwin = models.CharField(max_length=16, unique=True, help_text="Code LWIN7 Liv-ex.")
    producteur = models.CharField(max_length=255)
    vin = models.CharField(max_length=255, blank=True, default="")
    pays = models.CharField(max_length=100, blank=True, default="")
    region = models.CharField(max_length=255, blank=True, default="")
    sous_region = models.CharField(max_length=255, blank=True, default="")
    couleur = models.CharField(
        max_length=10, choices=Cuvee.Couleur.choices, default=Cuvee.Couleur.AUTRE
    )
    classification = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["producteur", "vin"]
        verbose_name = "référence LWIN"
        verbose_name_plural = "références LWIN"

    def __str__(self):
        return f"{self.producteur} - {self.vin}" if self.vin else self.producteur


class SourceObservation(models.Model):
    """Relevé brut d'un canal d'enrichissement pour une cuvée (append-only).

    Chaque hit d'un canal (Open Food Facts, Claude, wineapi, LWIN, scraping…)
    dépose ici une ligne : le payload brut *tel que renvoyé par le canal* et les
    champs normalisés qu'il affirme, horodatés et pondérés d'une confiance. On
    n'écrase jamais une observation : l'historique complet reste disponible pour
    re-consolider la cuvée sans re-solliciter les sources (cf.
    docs/architecture-referentiel.md, Phase 1). La projection consolidée et
    l'arbitrage entre observations relèvent de la Phase 2.
    """

    cuvee = models.ForeignKey(
        Cuvee, on_delete=models.CASCADE, related_name="observations"
    )
    canal = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Canal source (ex: wineapi, claude, lwin, openfoodfacts, scrape:...).",
    )
    releve_le = models.DateTimeField(auto_now_add=True)
    confiance = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.50"),
        help_text="Confiance a priori dans ce relevé (0 à 1), défaut par canal.",
    )
    payload_brut = models.JSONField(
        default=dict, blank=True, help_text="Réponse brute du canal, telle quelle."
    )
    champs = models.JSONField(
        default=dict, blank=True, help_text="Champs normalisés affirmés par ce relevé."
    )

    class Meta:
        ordering = ["-releve_le"]
        verbose_name = "observation de source"
        verbose_name_plural = "observations de source"
        indexes = [
            models.Index(fields=["cuvee", "canal"]),
        ]

    def __str__(self):
        return f"{self.cuvee} ← {self.canal} ({self.releve_le:%Y-%m-%d})"


class MillesimeReference(models.Model):
    """Qualité d'un millésime pour une grande région viticole (note /5).

    Table sourçable extraite de la logique métier (auparavant figée dans
    ``apogee.MILLESIMES``, cf. docs/architecture-referentiel.md, Phase 4 / D6) :
    donnée du monde réel qu'un canal (saisie, LLM, scraping de tables de
    millésimes) peut alimenter et corriger sans redéploiement. La logique
    d'apogée reste *pure* : elle reçoit la table par injection (``table()``), avec
    ``apogee.MILLESIMES`` comme repli hors-ligne.
    """

    # Cache de la table {region_cle: {annee: note}} construite depuis la base.
    _CACHE_KEY = "millesimes:table"
    _CACHE_TTL = 60 * 60  # la qualité d'un millésime est quasi statique.

    region_cle = models.CharField(
        max_length=32,
        help_text="Grande région viticole normalisée (ex: bordeaux, bourgogne).",
    )
    annee = models.PositiveIntegerField()
    note = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Qualité du millésime, de 1 (faible) à 5 (exceptionnel).",
    )
    source = models.CharField(
        max_length=64, default="seed",
        help_text="Origine de la note (ex: seed, manuel, claude, scrape:...).",
    )

    class Meta:
        ordering = ["region_cle", "annee"]
        verbose_name = "référence millésime"
        verbose_name_plural = "références millésimes"
        constraints = [
            models.UniqueConstraint(
                fields=["region_cle", "annee"], name="unique_millesime_par_region"
            ),
        ]

    def __str__(self):
        return f"{self.region_cle} {self.annee} : {self.note}/5"

    @classmethod
    def table(cls) -> dict[str, dict[int, int]]:
        """Table `{region_cle: {annee: note}}`, mise en cache.

        Repli sur ``apogee.MILLESIMES`` si la base n'a pas encore été semée, pour
        rester fonctionnel hors-ligne et sur une installation neuve."""
        cached = cache.get(cls._CACHE_KEY)
        if cached is not None:
            return cached
        table: dict[str, dict[int, int]] = defaultdict(dict)
        for ref in cls.objects.all():
            table[ref.region_cle][ref.annee] = ref.note
        resultat = dict(table) or apogee.MILLESIMES
        cache.set(cls._CACHE_KEY, resultat, cls._CACHE_TTL)
        return resultat

    @classmethod
    def vider_cache(cls) -> None:
        cache.delete(cls._CACHE_KEY)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.vider_cache()  # une correction est visible sans attendre le TTL.

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        self.vider_cache()


class Parametre(models.Model):
    """Paramètre de configuration modifiable à chaud depuis le panneau d'admin.

    Sert d'override à la variable d'environnement de même nom : la valeur en base
    (si renseignée) prime sur celle du ``.env`` / des settings, ce qui permet de
    régler les clés d'API sans redémarrer le conteneur. Une valeur vide en base
    revient au repli ``.env``. Les valeurs secrètes (clés d'API) ne sont jamais
    renvoyées en clair par l'API — cf. ``runtime_config`` et le panneau d'admin.
    """

    cle = models.CharField(max_length=64, unique=True, help_text="Nom du paramètre (ex: WINEAPI_KEY).")
    valeur = models.TextField(blank=True, default="")
    maj_le = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cle"]
        verbose_name = "paramètre"
        verbose_name_plural = "paramètres"

    def __str__(self):
        return self.cle
