from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Bouteille, MouvementStock, NoteDegustation, Rangement
from .serializers import (
    BouteilleSerializer,
    ConsommerSerializer,
    MouvementStockSerializer,
    NoteDegustationSerializer,
    RangementSerializer,
)


class BouteilleViewSet(viewsets.ModelViewSet):
    serializer_class = BouteilleSerializer
    # « statut » n'est plus filtrable : il est désormais calculé (fenêtre
    # d'apogée × année courante), pas stocké de façon fiable.
    filterset_fields = ["cuvee", "emplacement", "millesime", "cuvee__couleur"]
    search_fields = ["cuvee__nom", "cuvee__domaine__nom"]
    ordering_fields = ["millesime", "cree_le", "prix_achat"]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Bouteille.objects.none()
        return (
            Bouteille.objects.filter(proprietaire=user)
            .select_related("cuvee__domaine", "emplacement__parent__parent__parent")
            # La fenêtre d'apogée est affinée par les cépages : on précharge le
            # M2M pour éviter une requête par ligne au calcul du statut.
            .prefetch_related("cuvee__cepages")
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
        # La consommation peut rendre des cases excédentaires : on les libère.
        bouteille.synchroniser_rangements()
        return Response(
            {
                "bouteille": BouteilleSerializer(bouteille, context={"request": request}).data,
                "mouvement": MouvementStockSerializer(mouvement).data,
            },
            status=status.HTTP_200_OK,
        )


class RangementViewSet(viewsets.ModelViewSet):
    """Placement case par case des bouteilles dans les grilles d'emplacement."""

    serializer_class = RangementSerializer
    filterset_fields = ["emplacement", "bouteille", "emplacement__cave"]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Rangement.objects.none()
        return Rangement.objects.filter(bouteille__proprietaire=user).select_related(
            "bouteille__cuvee", "emplacement"
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
