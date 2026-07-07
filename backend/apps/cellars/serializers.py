from rest_framework import serializers

from .models import Cave, Emplacement


class EmplacementSerializer(serializers.ModelSerializer):
    chemin = serializers.CharField(read_only=True)
    occupation_actuelle = serializers.IntegerField(read_only=True)

    class Meta:
        model = Emplacement
        fields = [
            "id",
            "cave",
            "parent",
            "nom",
            "type_emplacement",
            "capacite",
            "ligne",
            "colonne",
            "chemin",
            "occupation_actuelle",
        ]

    def validate(self, attrs):
        cave = attrs.get("cave") or (self.instance.cave if self.instance else None)
        parent = attrs.get("parent") or (self.instance.parent if self.instance else None)

        request = self.context.get("request")
        if cave and request and cave.proprietaire != request.user:
            raise serializers.ValidationError(
                {"cave": "Cette cave ne vous appartient pas."}
            )
        if parent and cave and parent.cave_id != cave.id:
            raise serializers.ValidationError(
                {"parent": "L'emplacement parent doit appartenir à la même cave."}
            )
        return attrs


class CaveSerializer(serializers.ModelSerializer):
    proprietaire = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Cave
        fields = ["id", "proprietaire", "nom", "description", "cree_le"]
        read_only_fields = ["cree_le"]
