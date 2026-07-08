from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Bouteille, MouvementStock, NoteDegustation
from .serializers import (
    BouteilleSerializer,
    ConsommerSerializer,
    MouvementStockSerializer,
    NoteDegustationSerializer,
)


class BouteilleViewSet(viewsets.ModelViewSet):
    serializer_class = BouteilleSerializer
    filterset_fields = ["statut", "cuvee", "emplacement", "millesime", "cuvee__couleur"]
    search_fields = ["cuvee__nom", "cuvee__domaine__nom"]
    ordering_fields = ["millesime", "cree_le", "prix_achat"]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Bouteille.objects.none()
        return Bouteille.objects.filter(proprietaire=user).select_related(
            "cuvee__domaine", "emplacement__parent__parent__parent"
        )

    @action(detail=True, methods=["post"])
    def consommer(self, request, pk=None):
        """Retire N bouteilles de cette ligne de stock et journalise le mouvement."""
        bouteille = self.get_object()
        serializer = ConsommerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quantite = serializer.validated_data["quantite"]

        with transaction.atomic():
            # Décrément conditionnel en une seule requête : deux consommations
            # concurrentes ne peuvent pas faire passer le stock en négatif.
            updated = Bouteille.objects.filter(
                pk=bouteille.pk, quantite__gte=quantite
            ).update(quantite=F("quantite") - quantite, maj_le=timezone.now())
            if not updated:
                return Response(
                    {"detail": "Quantité demandée supérieure au stock disponible."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            mouvement = MouvementStock.objects.create(
                bouteille=bouteille,
                type_mouvement=MouvementStock.TypeMouvement.CONSOMMATION,
                quantite=quantite,
                occasion=serializer.validated_data["occasion"],
                notes=serializer.validated_data["notes"],
            )

        bouteille.refresh_from_db()
        return Response(
            {
                "bouteille": BouteilleSerializer(bouteille, context={"request": request}).data,
                "mouvement": MouvementStockSerializer(mouvement).data,
            },
            status=status.HTTP_200_OK,
        )


class MouvementStockViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MouvementStockSerializer
    filterset_fields = ["bouteille", "type_mouvement"]
    ordering_fields = ["date"]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return MouvementStock.objects.none()
        return MouvementStock.objects.filter(
            bouteille__proprietaire=user
        ).select_related("bouteille__cuvee")


class NoteDegustationViewSet(viewsets.ModelViewSet):
    """Carnet de dégustation de l'utilisateur (données privées, cloisonnées)."""

    serializer_class = NoteDegustationSerializer
    filterset_fields = ["cuvee", "millesime"]
    ordering_fields = ["date_degustation", "note", "cree_le"]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return NoteDegustation.objects.none()
        return NoteDegustation.objects.filter(proprietaire=user).select_related("cuvee__domaine")
