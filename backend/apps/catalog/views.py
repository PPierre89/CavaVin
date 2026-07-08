from decimal import ROUND_HALF_UP, Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Max, Min, Sum
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.inventory.models import Bouteille, NoteDegustation

from . import sommellerie, wine_profile
from .enrichment import EnrichmentError, get_enabled_providers, wineapi_detail
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

    @action(detail=True, methods=["get"])
    def fiche(self, request, pk=None):
        """Fiche vin consolidée : référentiel + conseil de dégustation (public)
        + prix d'achat moyen et millésimes en stock (propres à l'utilisateur)."""
        cuvee = self.get_object()
        conseil = sommellerie.conseil_pour_couleur(cuvee.couleur)

        user = request.user
        bouteilles = (
            Bouteille.objects.filter(proprietaire=user, cuvee=cuvee)
            if user.is_authenticated
            else Bouteille.objects.none()
        )

        # Prix d'achat moyen pondéré par les quantités (lignes sans prix ignorées).
        # ExpressionWrapper : le produit Decimal × entier exige un output_field explicite.
        ligne_valeur = ExpressionWrapper(
            F("prix_achat") * F("quantite"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )
        chiffres = bouteilles.filter(prix_achat__isnull=False).aggregate(
            montant=Sum(ligne_valeur),
            nb=Sum("quantite"),
        )
        prix_moyen = None
        if chiffres["nb"]:
            prix_moyen = (chiffres["montant"] / chiffres["nb"]).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        # Millésimes en stock, regroupés (une cuvée peut avoir plusieurs lignes
        # par millésime, à des emplacements différents).
        millesimes = list(
            bouteilles.filter(quantite__gt=0)
            .values("millesime")
            .annotate(
                quantite=Sum("quantite"),
                apogee_debut=Min("apogee_debut"),
                apogee_fin=Max("apogee_fin"),
            )
            .order_by(F("millesime").desc(nulls_last=True))
        )

        cepages = list(cuvee.cepages.values_list("nom", flat=True))

        # Valeurs par défaut dérivées de la couleur (repli si wineapi indisponible).
        profil = [vars(axe) for axe in conseil.gustatif]
        accords = [{**vars(a), "confiance": None} for a in conseil.accords]
        note_communaute = None
        avis = []
        prix_marche = None

        # Enrichissement wineapi.io si le vin a déjà été identifié (best-effort,
        # mis en cache) : profil gustatif réel, accords notés, note & avis
        # communautaires, fourchette de prix marché.
        detail = wineapi_detail(cuvee.reference_externe_id)
        if detail:
            profil = wine_profile.profil_gustatif(detail, profil)
            accords = wine_profile.accords_mets(detail) or accords
            note_communaute = wine_profile.note_communaute(detail)
            avis = wine_profile.avis_critiques(detail)
            prix_marche = wine_profile.prix_marche(detail)

        # Ma note : entrée la plus récente du carnet de dégustation pour cette cuvée.
        ma_note = None
        if user.is_authenticated:
            derniere = (
                NoteDegustation.objects.filter(proprietaire=user, cuvee=cuvee)
                .order_by("-date_degustation", "-cree_le")
                .first()
            )
            if derniere is not None:
                ma_note = {
                    "note": str(derniere.note),
                    "commentaire": derniere.commentaire,
                    "millesime": derniere.millesime,
                    "date": derniere.date_degustation,
                }

        return Response(
            {
                "cuvee": {
                    "id": cuvee.id,
                    "nom": cuvee.nom,
                    "appellation": cuvee.appellation,
                    "couleur": cuvee.couleur,
                    "domaine_nom": cuvee.domaine.nom,
                    "cepages": cepages,
                },
                "conseil_degustation": {
                    "temperature": conseil.temperature,
                    "carafage": conseil.carafage,
                },
                "profil_gustatif": profil,
                "accords_mets": accords,
                "note_communaute": note_communaute,
                "avis": avis,
                "prix_marche": prix_marche,
                "ma_note": ma_note,
                "prix_achat_moyen": str(prix_moyen) if prix_moyen is not None else None,
                "millesimes": millesimes,
                "stock_total": sum(m["quantite"] for m in millesimes),
            }
        )


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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "enrichment"

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
                {
                    "source": wine.source,
                    "created": True,
                    "millesime": wine.millesime,
                    "cuvee": CuveeSerializer(cuvee).data,
                }
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "enrichment"

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
                    "millesime": wine.millesime,
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "enrichment"

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
                    "millesime": wine.millesime,
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
