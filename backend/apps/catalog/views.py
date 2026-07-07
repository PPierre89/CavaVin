from rest_framework import status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .enrichment import EnrichmentError, get_enabled_providers
from .enrichment.normalize import strip_vintage
from .ingest import upsert_cuvee
from .models import Cepage, Cuvee, Domaine
from .serializers import (
    CepageSerializer,
    CuveeSerializer,
    DomaineSerializer,
    IdentifierVinSerializer,
    ScanCodeBarresSerializer,
    ScanEtiquetteSerializer,
)


class DomaineViewSet(viewsets.ModelViewSet):
    queryset = Domaine.objects.all()
    serializer_class = DomaineSerializer
    search_fields = ["nom", "region", "pays"]
    filterset_fields = ["region", "pays"]


class CepageViewSet(viewsets.ModelViewSet):
    queryset = Cepage.objects.all()
    serializer_class = CepageSerializer
    search_fields = ["nom"]


class CuveeViewSet(viewsets.ModelViewSet):
    queryset = Cuvee.objects.select_related("domaine").prefetch_related("cepages")
    serializer_class = CuveeSerializer
    search_fields = ["nom", "domaine__nom", "appellation", "code_barres"]
    filterset_fields = ["couleur", "domaine"]


class ScanCodeBarresView(APIView):
    """
    US 01 — Cascade de résolution d'un code-barres (EAN/UPC).

    Scénario 1 : présent en base locale -> aucun appel réseau externe.
    Scénario 2 : absent en local -> cascade des fournisseurs activés
                 (Open Food Facts, ...). Le premier hit est normalisé, mis en
                 cache local, puis renvoyé.
    Scénario 3 : inconnu de tous -> 404 'Vin non reconnu par son code-barres',
                 l'app bascule alors sur le scan d'étiquette (US 03).
    """

    serializer_class = ScanCodeBarresSerializer

    def post(self, request):
        serializer = ScanCodeBarresSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ean = serializer.validated_data["code_barres"]

        # --- Scénario 1 : cache local (pas de réseau) ---
        cuvee = Cuvee.objects.select_related("domaine").filter(code_barres=ean).first()
        if cuvee:
            return Response(
                {"source": "local", "created": False, "cuvee": CuveeSerializer(cuvee).data}
            )

        # --- Scénario 2 : cascade des fournisseurs externes ---
        for provider in get_enabled_providers():
            wine = provider.lookup_by_barcode(ean)
            if not wine:
                continue
            cuvee, _ = upsert_cuvee(wine)
            return Response(
                {"source": wine.source, "created": True, "cuvee": CuveeSerializer(cuvee).data}
            )

        # --- Scénario 3 : échec total ---
        return Response(
            {"source": None, "detail": "Vin non reconnu par son code-barres"},
            status=status.HTTP_404_NOT_FOUND,
        )


class IdentifierVinView(APIView):
    """
    US 04 — Identification d'un vin à partir de texte (nom, domaine, ou sortie OCR
    de l'US 03), via la base wineapi.io.

    1. Base locale (nom) -> cache hit, aucun appel externe.
    2. Cascade des fournisseurs texte activés (wineapi.io) -> normalisation,
       persistance locale (domaine/cuvée/cépages), réponse enrichie.
    3. Échec -> 404 'Vin non identifié'.
    """

    serializer_class = IdentifierVinSerializer

    def post(self, request):
        serializer = IdentifierVinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data["query"]

        # --- Cache local : correspondance sur le nom (millésime retiré) ---
        # wineapi stocke le nom sans millésime ; on aligne la requête pour que
        # 'Chateau Petrus 2015' retombe sur la cuvée 'Chateau Petrus' déjà en base.
        cuvee = (
            Cuvee.objects.select_related("domaine")
            .filter(nom__iexact=strip_vintage(query))
            .first()
        )
        if cuvee:
            return Response(
                {"source": "local", "created": False, "cuvee": CuveeSerializer(cuvee).data}
            )

        # --- Cascade des fournisseurs texte (wineapi.io) ---
        for provider in get_enabled_providers():
            try:
                wine = provider.lookup_by_text(query)
            except EnrichmentError as exc:
                return Response({"detail": exc.message}, status=exc.status)
            if not wine:
                continue
            cuvee, created = upsert_cuvee(wine)
            return Response(
                {
                    "source": wine.source,
                    "created": created,
                    "confidence": wine.raw.get("confidence"),
                    "cuvee": CuveeSerializer(cuvee).data,
                    "infos": {
                        "region": wine.raw.get("region"),
                        "pays": wine.raw.get("country"),
                        "note": wine.raw.get("note"),
                        "alcool": wine.raw.get("alcool"),
                        "prix": wine.raw.get("prix"),
                        "description": wine.raw.get("description"),
                    },
                    "suggestions": wine.raw.get("suggestions") or [],
                }
            )

        # --- Échec total ---
        return Response(
            {"source": None, "detail": "Vin non identifié"},
            status=status.HTTP_404_NOT_FOUND,
        )


class ScanEtiquetteView(APIView):
    """
    US 02/03 — Identification d'un vin à partir d'une photo d'étiquette,
    via `POST /identify/image` de wineapi.io (JPEG/PNG ≤ 10 Mo).

    Cascade des fournisseurs image activés -> normalisation, persistance locale
    (même pipeline que le scan code-barres et l'identification texte), réponse
    enrichie. Échec -> 404 'Vin non identifié sur l'étiquette'.
    """

    parser_classes = [MultiPartParser, FormParser]
    serializer_class = ScanEtiquetteSerializer

    def post(self, request):
        serializer = ScanEtiquetteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        image = serializer.validated_data["image"]
        data = image.read()

        for provider in get_enabled_providers():
            try:
                wine = provider.lookup_by_image(data, image.content_type)
            except EnrichmentError as exc:
                return Response({"detail": exc.message}, status=exc.status)
            if not wine:
                continue
            cuvee, created = upsert_cuvee(wine)
            return Response(
                {
                    "source": wine.source,
                    "created": created,
                    "confidence": wine.raw.get("confidence"),
                    "cuvee": CuveeSerializer(cuvee).data,
                    "infos": {
                        "region": wine.raw.get("region"),
                        "pays": wine.raw.get("country"),
                        "note": wine.raw.get("note"),
                        "alcool": wine.raw.get("alcool"),
                        "prix": wine.raw.get("prix"),
                        "description": wine.raw.get("description"),
                    },
                    "suggestions": wine.raw.get("suggestions") or [],
                }
            )

        return Response(
            {"source": None, "detail": "Vin non identifié sur l'étiquette"},
            status=status.HTTP_404_NOT_FOUND,
        )
