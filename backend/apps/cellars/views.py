from django.db.models import Sum
from django.db.models.functions import Coalesce
from rest_framework import viewsets

from .models import Cave, Emplacement
from .serializers import CaveSerializer, EmplacementSerializer


class CaveViewSet(viewsets.ModelViewSet):
    serializer_class = CaveSerializer
    filterset_fields = ["nom"]

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated:
            return Cave.objects.filter(proprietaire=user)
        return Cave.objects.none()


class EmplacementViewSet(viewsets.ModelViewSet):
    serializer_class = EmplacementSerializer
    filterset_fields = ["cave", "parent", "type_emplacement"]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Emplacement.objects.none()
        return (
            Emplacement.objects.filter(cave__proprietaire=user)
            # parent__parent__parent couvre chemin() jusqu'à 4 niveaux sans requête
            # supplémentaire (Armoire > Casier > Clayette > Case).
            .select_related("cave", "parent__parent__parent")
            .annotate(occupation_actuelle=Coalesce(Sum("bouteilles__quantite"), 0))
            # L'annotation GROUP BY fait perdre le Meta.ordering : sans ordre
            # explicite, la pagination devient non déterministe.
            .order_by("cave", "parent_id", "ligne", "colonne", "nom")
        )
