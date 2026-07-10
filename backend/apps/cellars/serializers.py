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
            "nb_colonnes",
            "nb_rangees",
            "disposition",
            "chemin",
            "occupation_actuelle",
        ]

    def validate(self, attrs):
        cave = attrs.get("cave") or (self.instance.cave if self.instance else None)
        parent = attrs.get("parent") or (self.instance.parent if self.instance else None)

        # Une grille se décrit par ses deux dimensions : refuser une demi-grille
        # évite une capacité incohérente ou un rendu bancal côté client.
        def resolved(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None) if self.instance else None

        cols, rows = resolved("nb_colonnes"), resolved("nb_rangees")
        if bool(cols) != bool(rows):
            raise serializers.ValidationError(
                {
                    "nb_colonnes": "Renseignez la largeur et la hauteur ensemble, ou aucune des deux."
                }
            )

        request = self.context.get("request")
        if cave and request and cave.proprietaire != request.user:
            raise serializers.ValidationError(
                {"cave": "Cette cave ne vous appartient pas."}
            )
        if parent and cave and parent.cave_id != cave.id:
            raise serializers.ValidationError(
                {"parent": "L'emplacement parent doit appartenir à la même cave."}
            )
        # Anti-cycle : un emplacement ne peut être ni son propre parent, ni un
        # descendant de lui-même — sinon chemin() / __str__ bouclent à l'infini.
        if parent and self.instance:
            node = parent
            while node is not None:
                if node.pk == self.instance.pk:
                    raise serializers.ValidationError(
                        {"parent": "Ce parent créerait un cycle dans l'arborescence."}
                    )
                node = node.parent
        return attrs


class CaveSerializer(serializers.ModelSerializer):
    proprietaire = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Cave
        fields = ["id", "proprietaire", "nom", "description", "cree_le"]
        read_only_fields = ["cree_le"]
