from django.db.models import Sum
from rest_framework import serializers

from .models import Bouteille, MouvementStock


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
    emplacement_chemin = serializers.SerializerMethodField()

    class Meta:
        model = Bouteille
        fields = [
            "id",
            "proprietaire",
            "cuvee",
            "cuvee_nom",
            "domaine_nom",
            "millesime",
            "emplacement",
            "emplacement_chemin",
            "quantite",
            "statut",
            "prix_achat",
            "date_achat",
            "apogee_debut",
            "apogee_fin",
            "notes",
            "cree_le",
            "maj_le",
        ]
        read_only_fields = ["cree_le", "maj_le"]

    def get_emplacement_chemin(self, obj) -> str | None:
        return obj.emplacement.chemin() if obj.emplacement else None

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


class ConsommerSerializer(serializers.Serializer):
    quantite = serializers.IntegerField(min_value=1, default=1)
    occasion = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
