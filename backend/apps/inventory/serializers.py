from django.db.models import Sum
from rest_framework import serializers

from apps.catalog import apogee

from .models import Bouteille, MouvementStock, NoteDegustation, Rangement


class MouvementStockSerializer(serializers.ModelSerializer):
    class Meta:
        model = MouvementStock
        fields = [
            "id",
            "bouteille",
            "type_mouvement",
            "quantite",
            "date",
            "occasion",
            "notes",
        ]
        read_only_fields = ["date"]


class BouteilleSerializer(serializers.ModelSerializer):
    proprietaire = serializers.HiddenField(default=serializers.CurrentUserDefault())
    cuvee_nom = serializers.CharField(source="cuvee.nom", read_only=True)
    domaine_nom = serializers.CharField(source="cuvee.domaine.nom", read_only=True)
    # Attributs de la cuvée dont les écrans de stock ont besoin (couleur, lieu,
    # valeur marché). Les servir ici évite au client de télécharger tout le
    # catalogue mutualisé — qui grossit avec la communauté — pour afficher SES
    # bouteilles. La cuvée est déjà jointe (select_related) : aucun coût SQL.
    couleur = serializers.CharField(source="cuvee.couleur", read_only=True)
    appellation = serializers.CharField(source="cuvee.appellation", read_only=True)
    region = serializers.CharField(source="cuvee.region", read_only=True)
    pays = serializers.CharField(source="cuvee.pays", read_only=True)
    classification = serializers.CharField(source="cuvee.classification", read_only=True)
    prix_min = serializers.DecimalField(
        source="cuvee.prix_min", max_digits=10, decimal_places=2, read_only=True
    )
    prix_max = serializers.DecimalField(
        source="cuvee.prix_max", max_digits=10, decimal_places=2, read_only=True
    )
    devise = serializers.CharField(source="cuvee.devise", read_only=True)
    emplacement_chemin = serializers.SerializerMethodField()
    # Statut de dégustation *calculé* (à garder / à boire / dépassé), déduit de la
    # fenêtre d'apogée effective et de l'année courante. C'est lui qui pilote le
    # code couleur ; il remplace en lecture la valeur stockée (souvent neutre).
    statut = serializers.SerializerMethodField()
    # Fenêtre d'apogée effective : saisie manuelle si présente, sinon estimée.
    apogee_debut_effectif = serializers.SerializerMethodField()
    apogee_fin_effectif = serializers.SerializerMethodField()

    class Meta:
        model = Bouteille
        fields = [
            "id",
            "proprietaire",
            "cuvee",
            "cuvee_nom",
            "domaine_nom",
            "couleur",
            "appellation",
            "region",
            "pays",
            "classification",
            "prix_min",
            "prix_max",
            "devise",
            "millesime",
            "emplacement",
            "emplacement_chemin",
            "quantite",
            "statut",
            "prix_achat",
            "date_achat",
            "apogee_debut",
            "apogee_fin",
            "apogee_debut_effectif",
            "apogee_fin_effectif",
            "notes",
            "cree_le",
            "maj_le",
        ]
        read_only_fields = ["cree_le", "maj_le"]

    def get_emplacement_chemin(self, obj) -> str | None:
        return obj.emplacement.chemin() if obj.emplacement else None

    def _fenetre(self, obj):
        """Fenêtre d'apogée de la ligne, estimée une seule fois par ligne.

        Trois champs sérialisés en dérivent (statut + les deux bornes) : sans ce
        mémo, une liste de bouteilles payait trois estimations par ligne, chacune
        reconstruisant les cépages et relisant la table des millésimes. Le mémo
        vit sur le sérialiseur (une instance par requête, partagée par les items
        d'une liste), pas sur le modèle : une lecture de ``fenetre_apogee`` hors
        sérialisation reste toujours fraîche."""
        if not hasattr(self, "_fenetres"):
            self._fenetres = {}
        if obj.pk not in self._fenetres:
            self._fenetres[obj.pk] = obj.fenetre_apogee()
        return self._fenetres[obj.pk]

    def get_statut(self, obj) -> str:
        debut, fin = self._fenetre(obj)
        return apogee.statut_pour_fenetre(debut, fin)

    def get_apogee_debut_effectif(self, obj) -> int | None:
        return self._fenetre(obj)[0]

    def get_apogee_fin_effectif(self, obj) -> int | None:
        return self._fenetre(obj)[1]

    def validate_emplacement(self, emplacement):
        request = self.context.get("request")
        if emplacement and request and emplacement.cave.proprietaire != request.user:
            raise serializers.ValidationError(
                "Cet emplacement appartient à la cave d'un autre utilisateur."
            )
        return emplacement

    def validate(self, attrs):
        # Fenêtre d'apogée cohérente (le modèle a un clean() mais DRF ne l'appelle pas).
        debut = attrs.get("apogee_debut", getattr(self.instance, "apogee_debut", None))
        fin = attrs.get("apogee_fin", getattr(self.instance, "apogee_fin", None))
        if debut and fin and debut > fin:
            raise serializers.ValidationError(
                {"apogee_fin": "L'année de fin d'apogée doit suivre l'année de début."}
            )

        # Contrainte de capacité : les bouteilles directement assignées à un
        # emplacement ne peuvent pas dépasser sa capacité (si définie). Les
        # sous-emplacements ont leur propre capacité, comptée séparément.
        emplacement = attrs.get(
            "emplacement", self.instance.emplacement if self.instance else None
        )
        quantite = attrs.get(
            "quantite", self.instance.quantite if self.instance else 1
        )
        if emplacement and emplacement.capacite is not None:
            deja_place = (
                Bouteille.objects.filter(emplacement=emplacement)
                .exclude(pk=self.instance.pk if self.instance else None)
                .aggregate(total=Sum("quantite"))["total"]
                or 0
            )
            if deja_place + quantite > emplacement.capacite:
                restant = emplacement.capacite - deja_place
                raise serializers.ValidationError(
                    {
                        "emplacement": (
                            f"Capacité dépassée : {emplacement.chemin()} contient "
                            f"{deja_place}/{emplacement.capacite} bouteilles "
                            f"(reste {max(restant, 0)} place(s))."
                        )
                    }
                )
        return attrs

    def update(self, instance, validated_data):
        ancien_emplacement = instance.emplacement_id
        instance = super().update(instance, validated_data)
        # Déménager une ligne invalide ses cases (elles pointent l'ancienne grille) ;
        # la remettre « non rangée » les libère de même. Sinon, une baisse de
        # quantité peut rendre des cases excédentaires : on les libère.
        if instance.emplacement_id != ancien_emplacement:
            instance.rangements.all().delete()
        else:
            instance.synchroniser_rangements()
        return instance


class RangementSerializer(serializers.ModelSerializer):
    """Range une bouteille dans une case précise d'un emplacement en grille."""

    class Meta:
        model = Rangement
        fields = ["id", "bouteille", "emplacement", "case"]
        # On désactive le UniqueTogetherValidator auto (message générique) au
        # profit du contrôle explicite ci-dessous, plus parlant ; la contrainte
        # d'unicité en base reste le garde-fou ultime contre les accès concurrents.
        validators = []

    def validate(self, attrs):
        request = self.context.get("request")
        user = request.user if request else None
        # PATCH partiel (ex. déplacer une case) : on complète depuis l'instance.
        instance = self.instance
        bouteille = attrs.get("bouteille") or (instance.bouteille if instance else None)
        emplacement = attrs.get("emplacement") or (instance.emplacement if instance else None)
        case = attrs.get("case", instance.case if instance else None)

        # Cloisonnement par propriétaire (RGPD) : on ne range que ses propres
        # bouteilles, et seulement dans ses propres emplacements.
        if user and bouteille.proprietaire_id and bouteille.proprietaire_id != user.id:
            raise serializers.ValidationError(
                {"bouteille": "Cette bouteille ne vous appartient pas."}
            )
        if user and emplacement.cave.proprietaire_id != user.id:
            raise serializers.ValidationError(
                {"emplacement": "Cet emplacement ne vous appartient pas."}
            )

        # Le placement case par case suppose une grille définie.
        if not (emplacement.nb_colonnes and emplacement.nb_rangees):
            raise serializers.ValidationError(
                {"emplacement": "Cet emplacement n'a pas de grille de rangement."}
            )
        capacite = emplacement.capacite or 0
        if case < 0 or case >= capacite:
            raise serializers.ValidationError(
                {"case": f"Case hors de la grille (0 à {capacite - 1})."}
            )

        # La ligne doit être libre ou déjà dans cet emplacement (pas ailleurs).
        if bouteille.emplacement_id not in (None, emplacement.id):
            raise serializers.ValidationError(
                {"bouteille": "Cette bouteille est déjà rangée dans un autre emplacement."}
            )

        # Une case ne reçoit qu'une bouteille.
        occupee = Rangement.objects.filter(emplacement=emplacement, case=case)
        if self.instance:
            occupee = occupee.exclude(pk=self.instance.pk)
        if occupee.exists():
            raise serializers.ValidationError({"case": "Cette case est déjà occupée."})

        # On ne range pas plus d'unités que la ligne n'en compte.
        deja = bouteille.rangements
        if self.instance:
            deja = deja.exclude(pk=self.instance.pk)
        if deja.count() >= bouteille.quantite:
            raise serializers.ValidationError(
                {"bouteille": "Toutes les bouteilles de cette ligne sont déjà rangées."}
            )
        return attrs

    def create(self, validated_data):
        bouteille = validated_data["bouteille"]
        emplacement = validated_data["emplacement"]
        # Ranger une bouteille non placée la rattache à la grille concernée.
        if bouteille.emplacement_id is None:
            bouteille.emplacement = emplacement
            bouteille.save(update_fields=["emplacement", "maj_le"])
        return super().create(validated_data)


class ConsommerSerializer(serializers.Serializer):
    quantite = serializers.IntegerField(min_value=1, default=1)
    occasion = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class NoteDegustationSerializer(serializers.ModelSerializer):
    proprietaire = serializers.HiddenField(default=serializers.CurrentUserDefault())
    cuvee_nom = serializers.CharField(source="cuvee.nom", read_only=True)
    domaine_nom = serializers.CharField(source="cuvee.domaine.nom", read_only=True)
    couleur = serializers.CharField(source="cuvee.couleur", read_only=True)
    note = serializers.DecimalField(max_digits=2, decimal_places=1, min_value=0, max_value=5)

    class Meta:
        model = NoteDegustation
        fields = [
            "id",
            "proprietaire",
            "cuvee",
            "cuvee_nom",
            "domaine_nom",
            "couleur",
            "millesime",
            "note",
            "commentaire",
            "acidite",
            "tanin",
            "fruit",
            "date_degustation",
            "cree_le",
        ]
        read_only_fields = ["cree_le"]
