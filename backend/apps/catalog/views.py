from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.cache import cache
from django.db.models import DecimalField, ExpressionWrapper, F, Max, Min, Sum
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.inventory.models import Bouteille, NoteDegustation

from . import sommellerie, wine_profile
from .enrichment import (
    EnrichmentError,
    get_enabled_providers,
    refresh_wineapi_detail,
    wineapi_detail,
)
from .enrichment.normalize import strip_vintage
from .ingest import enrich_cuvee_from_wineapi, upsert_cuvee
from .models import Cepage, Cuvee, Domaine
from .serializers import (
    CepageSerializer,
    CuveeSerializer,
    DomaineSerializer,
    IdentifierVinSerializer,
    ScanCodeBarresSerializer,
    ScanEtiquetteSerializer,
)


def _local_response(cuvee):
    """Réponse d'un hit en cache local (aucun enrichissement externe)."""
    return Response({"source": "local", "created": False, "cuvee": CuveeSerializer(cuvee).data})


def _enriched_response(wine, cuvee, created):
    """Réponse commune aux endpoints d'identification texte/image (wineapi.io)."""
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


class DomaineViewSet(viewsets.ModelViewSet):
    queryset = Domaine.objects.all()
    serializer_class = DomaineSerializer
    search_fields = ["nom", "region", "pays"]
    filterset_fields = ["region", "pays"]


class CepageViewSet(viewsets.ModelViewSet):
    queryset = Cepage.objects.all()
    serializer_class = CepageSerializer
    search_fields = ["nom"]


def _build_fiche(cuvee, user):
    """Construit la charge utile de la fiche vin, à partir des données persistées.

    L'enrichissement wineapi (corps, notes, prix, accords, avis...) est lu depuis
    la cuvée en base — aucun appel réseau ici. Le stock (prix moyen, millésimes,
    ma note) n'est renseigné que pour un utilisateur authentifié ; le référentiel
    et le conseil sont publics.
    """
    conseil = sommellerie.conseil_pour_couleur(cuvee.couleur)
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
        montant=Sum(ligne_valeur), nb=Sum("quantite")
    )
    prix_moyen = None
    if chiffres["nb"]:
        prix_moyen = (chiffres["montant"] / chiffres["nb"]).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    # Millésimes en stock, regroupés (une cuvée peut avoir plusieurs lignes par
    # millésime, à des emplacements différents).
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

    # Enrichissement wineapi persisté sur la cuvée (repli sur le conseil couleur).
    profil = wine_profile.profil_gustatif(
        {"body": cuvee.corps, "acidity": cuvee.acidite},
        [vars(axe) for axe in conseil.gustatif],
    )
    accords = cuvee.accords or [{**vars(a), "confiance": None} for a in conseil.accords]
    note_communaute = None
    if cuvee.note_moyenne is not None:
        note_communaute = {"note": float(cuvee.note_moyenne), "nb": cuvee.nb_notes or 0}
    avis = cuvee.scores or []
    prix_marche = None
    if cuvee.prix_min is not None and cuvee.prix_max is not None:
        prix_marche = {
            "min": float(cuvee.prix_min),
            "max": float(cuvee.prix_max),
            "devise": cuvee.devise or "EUR",
        }

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

    return {
        "cuvee": {
            "id": cuvee.id,
            "nom": cuvee.nom,
            "appellation": cuvee.appellation,
            "couleur": cuvee.couleur,
            "domaine_nom": cuvee.domaine.nom,
            "cepages": list(cuvee.cepages.values_list("nom", flat=True)),
            "region": cuvee.region,
            "pays": cuvee.pays,
            "classification": cuvee.classification,
            "description": cuvee.description,
            "elaborate": cuvee.elaborate,
            "degre_alcool": float(cuvee.degre_alcool) if cuvee.degre_alcool is not None else None,
            "image_url": cuvee.image_url,
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
        # Le vin a une source externe (wineapi) => le bouton de synchro est utile.
        "enrichissable": bool(cuvee.reference_externe_id),
        "enrichi_le": cuvee.enrichi_le,
    }


class CuveeViewSet(viewsets.ModelViewSet):
    queryset = Cuvee.objects.select_related("domaine").prefetch_related("cepages")
    serializer_class = CuveeSerializer
    search_fields = ["nom", "domaine__nom", "appellation", "code_barres"]
    filterset_fields = ["couleur", "domaine"]

    def get_throttles(self):
        # La synchro force un appel wineapi : on la soumet au throttle "enrichment".
        if self.action == "rafraichir":
            self.throttle_scope = "enrichment"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    @action(detail=True, methods=["get"])
    def fiche(self, request, pk=None):
        """Fiche vin consolidée, lue depuis les données persistées (aucun appel
        réseau). Premier accès à un vin jamais enrichi : enrichissement paresseux
        unique depuis wineapi (cache), pour bénéficier des données sans attendre
        une synchro manuelle."""
        cuvee = self.get_object()
        if cuvee.reference_externe_id and cuvee.enrichi_le is None:
            detail = wineapi_detail(cuvee.reference_externe_id)
            if detail:
                enrich_cuvee_from_wineapi(cuvee, detail)
        return Response(_build_fiche(cuvee, request.user))

    @action(detail=True, methods=["post"])
    def rafraichir(self, request, pk=None):
        """Synchro à la demande : force un re-fetch des données wineapi de la fiche.

        Garde-fou anti-quota : un cooldown par vin (WINEAPI_REFRESH_COOLDOWN)
        empêche de re-solliciter wineapi trop souvent, en plus du throttle
        "enrichment" appliqué à cette action.
        """
        cuvee = self.get_object()
        if not cuvee.reference_externe_id:
            return Response(
                {"detail": "Aucune source externe à synchroniser pour ce vin."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cle_cooldown = f"wineapi:refresh-cooldown:{cuvee.reference_externe_id}"
        if cache.get(cle_cooldown):
            return Response(
                {"detail": "Fiche déjà synchronisée récemment. Réessaie plus tard."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        cache.set(cle_cooldown, True, settings.WINEAPI_REFRESH_COOLDOWN)

        detail = refresh_wineapi_detail(cuvee.reference_externe_id)
        enrich_cuvee_from_wineapi(cuvee, detail)
        return Response(_build_fiche(cuvee, request.user))


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
            return _local_response(cuvee)

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
            return _local_response(cuvee)

        # --- Cascade des fournisseurs texte (wineapi.io) ---
        for provider in get_enabled_providers():
            try:
                wine = provider.lookup_by_text(query)
            except EnrichmentError as exc:
                return Response({"detail": exc.message}, status=exc.status)
            if not wine:
                continue
            cuvee, created = upsert_cuvee(wine)
            return _enriched_response(wine, cuvee, created)

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
            return _enriched_response(wine, cuvee, created)

        return Response(
            {"source": None, "detail": "Vin non identifié sur l'étiquette"},
            status=status.HTTP_404_NOT_FOUND,
        )
