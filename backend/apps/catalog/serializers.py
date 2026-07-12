from rest_framework import serializers

from .models import Cepage, Cuvee, Domaine


class DomaineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Domaine
        fields = ["id", "nom", "region", "pays", "site_web"]
        extra_kwargs = {
            # `region` participates in a UniqueConstraint with `nom`. DRF's unique
            # validator forces the field to be present in validated_data regardless
            # of `required`, so it needs a `default` too or omitting it 400s.
            "region": {"required": False, "default": ""},
        }


class CepageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cepage
        fields = ["id", "nom"]


class ScanCodeBarresSerializer(serializers.Serializer):
    """Valide un code-barres EAN/UPC (8 à 14 chiffres)."""

    code_barres = serializers.RegexField(
        r"^\d{8,14}$",
        error_messages={"invalid": "Code-barres invalide (8 à 14 chiffres attendus)."},
    )


class IdentifierVinSerializer(serializers.Serializer):
    """Valide une requête d'identification texte (US 04).

    ``lwin`` (optionnel) désigne directement une référence du référentiel
    local — posé par la sélection d'une suggestion de la recherche dynamique,
    il court-circuite la cascade externe (aucun quota consommé)."""

    query = serializers.CharField(min_length=2, trim_whitespace=True)
    lwin = serializers.CharField(required=False, allow_blank=True, max_length=16)


class RechercheVinsSerializer(serializers.Serializer):
    """Valide les paramètres de la recherche dynamique (autocomplétion)."""

    q = serializers.CharField(min_length=2, trim_whitespace=True)


class ScanEtiquetteSerializer(serializers.Serializer):
    """Valide l'upload d'une photo d'étiquette (US 02/03) : JPEG/PNG, 10 Mo max."""

    MAX_SIZE = 10 * 1024 * 1024  # limite wineapi.io
    CONTENT_TYPES = {"image/jpeg", "image/png"}

    image = serializers.FileField()

    def validate_image(self, f):
        if f.size > self.MAX_SIZE:
            raise serializers.ValidationError("Image trop lourde (10 Mo maximum).")
        if (f.content_type or "").lower() not in self.CONTENT_TYPES:
            raise serializers.ValidationError("Format non supporté (JPEG ou PNG attendu).")
        return f


class CuveeSerializer(serializers.ModelSerializer):
    domaine_nom = serializers.CharField(source="domaine.nom", read_only=True)
    cepages_noms = serializers.SlugRelatedField(
        source="cepages", slug_field="nom", many=True, read_only=True
    )

    class Meta:
        model = Cuvee
        fields = [
            "id",
            "domaine",
            "domaine_nom",
            "nom",
            "appellation",
            "couleur",
            "cepages",
            "cepages_noms",
            "code_barres",
            "reference_externe_id",
            # Enrichissement wineapi persisté.
            "region",
            "pays",
            "classification",
            "description",
            "elaborate",
            "corps",
            "acidite",
            "degre_alcool",
            "image_url",
            "lwin_code",
            "note_moyenne",
            "nb_notes",
            "prix_min",
            "prix_max",
            "devise",
            "accords",
            "scores",
            "prix_marchands",
            "enrichi_le",
        ]
        # L'enrichissement est alimenté par wineapi (identification / synchro),
        # pas par l'API d'écriture directe.
        read_only_fields = [
            "region", "pays", "classification", "description", "elaborate",
            "corps", "acidite", "degre_alcool", "image_url", "lwin_code",
            "note_moyenne", "nb_notes", "prix_min", "prix_max", "devise",
            "accords", "scores", "prix_marchands", "enrichi_le",
        ]
