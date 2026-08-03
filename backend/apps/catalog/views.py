import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import connection
from django.db.models import DecimalField, ExpressionWrapper, F, Max, Min, Sum
from django.http import FileResponse, Http404
from django.urls import reverse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.inventory.models import Bouteille, NoteDegustation

from . import apogee, quotas, sommellerie, wine_profile
from .enrichment import (
    EnrichmentError,
    get_enabled_providers,
    refresh_wineapi_detail,
    wineapi_detail,
)
from .enrichment.image import recadrer_etiquette, reduire as reduire_image
from .enrichment.lwin import LwinProvider, evaluer_confiance, rechercher_lwin
from .enrichment.normalize import guess_couleur, parse_vintage, strip_vintage
from .ingest import (
    identifiant_wineapi,
    synchroniser_wineapi,
    upsert_cuvee,
    upsert_multi,
)
from .models import Cepage, Cuvee, Domaine, ReferenceLwin
from .permissions import LectureOuEcritureSansSuppression
from .serializers import (
    CepageSerializer,
    CuveeSerializer,
    DomaineSerializer,
    IdentifierVinSerializer,
    RechercheVinsSerializer,
    ScanCodeBarresSerializer,
    ScanEtiquetteSerializer,
)

logger = logging.getLogger(__name__)


def _local_response(cuvee):
    """Réponse d'un hit en cache local (aucun enrichissement externe)."""
    return Response({"source": "local", "created": False, "cuvee": CuveeSerializer(cuvee).data})


def _enriched_response(wine, cuvee, created):
    """Réponse commune aux endpoints d'identification texte/image (Claude, wineapi.io)."""
    return Response(
        {
            "source": wine.source,
            "created": created,
            "millesime": wine.millesime,
            "confidence": wine.raw.get("confidence"),
            "auto_added": wine.raw.get("auto_added", False),
            "pending": wine.raw.get("pending", False),
            "cuvee": CuveeSerializer(cuvee).data,
            "infos": {
                "region": wine.raw.get("region"),
                "pays": wine.raw.get("pays"),
                "note": wine.raw.get("note"),
                "alcool": wine.raw.get("alcool"),
                "prix": wine.raw.get("prix"),
                "prix_marchands": wine.raw.get("prix_marchands") or [],
                "description": wine.raw.get("description"),
            },
            "suggestions": wine.raw.get("suggestions") or [],
        }
    )


def _conserver_etiquette(cuvee, photo: bytes) -> None:
    """Attache au catalogue la vignette d'étiquette issue d'un scan.

    On ne remplit que si la cuvée n'a pas déjà de vignette : le catalogue est
    mutualisé, un scan ultérieur — pris de plus loin ou plus flou — ne doit pas
    écraser une photo correcte pour tout le monde. Enrichir le catalogue d'une
    photo est un effet de bord agréable de l'identification, jamais une raison de
    la faire échouer : toute erreur est donc absorbée.
    """
    if cuvee.photo_etiquette:
        return
    try:
        vignette, _ = recadrer_etiquette(photo)
        cuvee.photo_etiquette.save(f"cuvee-{cuvee.pk}.jpg", ContentFile(vignette), save=True)
    except Exception as exc:  # disque plein, volume en lecture seule…
        logger.warning("vignette d'étiquette non conservée pour %s : %s", cuvee.pk, exc)


def _interroger(provider, appel):
    """Exécute le lookup d'une source et neutralise l'erreur remontable.

    Renvoie ``(wine, erreur)`` — l'un des deux est toujours ``None``. Seule
    ``EnrichmentError`` (quota/clé) est interceptée : les erreurs réseau sont
    déjà traduites en miss par les providers, et tout le reste doit remonter
    comme avant la parallélisation.
    """
    try:
        return appel(provider), None
    except EnrichmentError as exc:
        return None, exc


def _interroger_en_thread(provider, appel):
    """``_interroger`` dans un thread de travail de la cascade."""
    try:
        return _interroger(provider, appel)
    finally:
        # Django ouvre une connexion par thread et ne la referme qu'en fin de
        # requête HTTP — ce thread-ci n'en est pas un. Sans cela, chaque
        # identification laisserait filer autant de connexions SQLite que de
        # sources interrogées (quotas.compter écrit depuis le thread). À ne
        # surtout pas faire dans le thread de la requête : on y fermerait la
        # connexion de la requête elle-même.
        connection.close()


def _cascade_multi(appel):
    """Interroge **toutes** les sources activées (fusion multi-sources) et collecte
    leurs résultats, au lieu de s'arrêter au premier hit.

    ``appel(provider)`` renvoie un ``NormalizedWine`` ou ``None`` (le lookup adapté :
    code-barres / texte / image). Chaque source dont le **plafond mensuel** est
    atteint est ignorée (``quotas.reste``). Les erreurs remontables (quota 429 / clé
    invalide) d'une source n'interrompent pas les autres : on les mémorise pour ne
    les renvoyer qu'en dernier recours (aucun hit du tout).

    Les sources sont interrogées **en parallèle** : elles sont indépendantes et
    toutes appelées de toute façon (fusion multi-sources), si bien que la latence
    d'une identification est celle de la source la plus lente et non plus la
    *somme* de toutes. Une identification par photo enchaînait Claude (vision),
    wineapi et l'OCR local : plusieurs dizaines de secondes, jusqu'à friser le
    timeout du worker gunicorn. L'attente est d'entrée/sortie (réseau, sous-processus
    tesseract), donc des threads suffisent.

    Renvoie ``(hits, erreur)`` : la liste des résultats **dans l'ordre de la
    cascade** — indépendante de l'ordre d'arrivée, le 1er servant d'ancre
    d'identité (cf. ``upsert_multi``) — et la première erreur éventuelle dans ce
    même ordre (déterministe d'un appel à l'autre).
    """
    # Le plafond mensuel se lit avant de lancer quoi que ce soit : c'est une
    # lecture base, autant la faire dans le thread de la requête.
    sources = [p for p in get_enabled_providers() if quotas.reste(p.name)]
    if not sources:
        return [], None
    if len(sources) == 1:
        # Une seule source : inutile de payer un thread, on reste dans celui de
        # la requête (dont la connexion base ne doit pas être fermée).
        wine, erreur = _interroger(sources[0], appel)
        return ([wine] if wine else []), erreur

    with ThreadPoolExecutor(
        max_workers=len(sources), thread_name_prefix="identification"
    ) as pool:
        resultats = list(pool.map(lambda p: _interroger_en_thread(p, appel), sources))

    hits = [wine for wine, _ in resultats if wine]
    erreur = next((exc for _, exc in resultats if exc is not None), None)
    return hits, erreur


class DomaineViewSet(viewsets.ModelViewSet):
    queryset = Domaine.objects.all()
    serializer_class = DomaineSerializer
    permission_classes = [LectureOuEcritureSansSuppression]
    search_fields = ["nom", "region", "pays"]
    filterset_fields = ["region", "pays"]


class CepageViewSet(viewsets.ModelViewSet):
    queryset = Cepage.objects.all()
    serializer_class = CepageSerializer
    permission_classes = [LectureOuEcritureSansSuppression]
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
    # millésime, à des emplacements différents). Pour chaque millésime, on
    # calcule la fenêtre d'apogée effective (saisie manuelle si présente, sinon
    # estimée depuis la couleur) et le statut de dégustation associé.
    annee = date.today().year
    # Cépages de la cuvée : affinent la fenêtre estimée (aptitude à la garde).
    cepages = [c.nom for c in cuvee.cepages.all()]
    millesimes = []
    for groupe in (
        bouteilles.filter(quantite__gt=0)
        .values("millesime")
        .annotate(
            quantite=Sum("quantite"),
            apogee_debut=Min("apogee_debut"),
            apogee_fin=Max("apogee_fin"),
        )
        .order_by(F("millesime").desc(nulls_last=True))
    ):
        debut, fin = groupe["apogee_debut"], groupe["apogee_fin"]
        if debut is None and fin is None:
            debut, fin = apogee.fenetre_apogee(
                cuvee.couleur, groupe["millesime"], cepages=cepages, region=cuvee.region
            )
        groupe["apogee_debut"] = debut
        groupe["apogee_fin"] = fin
        groupe["statut"] = apogee.statut_pour_fenetre(debut, fin, annee)
        millesimes.append(groupe)

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
            # Vignette d'étiquette issue d'un scan : sur les petits domaines, c'est
            # souvent le seul visuel disponible (image_url reste vide).
            "photo_etiquette_url": (
                reverse("cuvee-photo", args=[cuvee.pk]) if cuvee.photo_etiquette else None
            ),
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
        "prix_marchands": cuvee.prix_marchands or [],
        "historique_prix": cuvee.historique_prix or [],
        "ma_note": ma_note,
        "prix_achat_moyen": str(prix_moyen) if prix_moyen is not None else None,
        "millesimes": millesimes,
        "stock_total": sum(m["quantite"] for m in millesimes),
        # Le bouton de synchro n'a de sens que si la référence est bien un
        # identifiant wineapi — ni une référence d'import préfixée, ni un
        # code-barres recopié par Open Food Facts (cf. identifiant_wineapi).
        "enrichissable": bool(identifiant_wineapi(cuvee)),
        "enrichi_le": cuvee.enrichi_le,
    }


class CuveeViewSet(viewsets.ModelViewSet):
    queryset = Cuvee.objects.select_related("domaine").prefetch_related("cepages")
    serializer_class = CuveeSerializer
    permission_classes = [LectureOuEcritureSansSuppression]
    # « region » complète la recherche : l'autocomplétion du formulaire d'ajout
    # interroge cet endpoint et cherchait aussi sur la région côté client.
    search_fields = ["nom", "domaine__nom", "appellation", "region", "code_barres"]
    filterset_fields = ["couleur", "domaine"]

    def get_throttles(self):
        # La synchro force un appel wineapi : on la soumet au throttle "enrichment".
        if self.action == "rafraichir":
            self.throttle_scope = "enrichment"
            return [ScopedRateThrottle()]
        return super().get_throttles()

    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def photo(self, request, pk=None):
        """Vignette d'étiquette de la cuvée (catalogue partagé, lecture publique).

        Servie par identifiant de cuvée, jamais par chemin : aucune valeur fournie
        par le client n'atteint le système de fichiers, la traversée de répertoire
        est donc impossible par construction. C'est aussi ce qui dispense de
        publier MEDIA_ROOT derrière une URL statique.
        """
        cuvee = self.get_object()
        if not cuvee.photo_etiquette:
            raise Http404("Aucune photo d'étiquette pour ce vin.")
        try:
            fichier = cuvee.photo_etiquette.open("rb")
        except (FileNotFoundError, OSError) as exc:
            # Fichier disparu (volume remonté, sauvegarde partielle) : 404 plutôt
            # qu'une 500, la fiche sait se passer de la vignette.
            raise Http404("Photo d'étiquette introuvable sur le disque.") from exc
        reponse = FileResponse(fichier, content_type="image/jpeg")
        # Le contenu ne change pas sans nouvelle photo : on laisse le navigateur
        # et le cache du NAS travailler.
        reponse["Cache-Control"] = "public, max-age=86400"
        return reponse

    @action(detail=True, methods=["get"])
    def fiche(self, request, pk=None):
        """Fiche vin consolidée, lue depuis les données persistées (aucun appel
        réseau). Premier accès à un vin jamais enrichi : enrichissement paresseux
        unique depuis wineapi (cache), pour bénéficier des données sans attendre
        une synchro manuelle."""
        cuvee = self.get_object()
        reference = identifiant_wineapi(cuvee)
        if reference and cuvee.enrichi_le is None:
            detail = wineapi_detail(reference)
            if detail:
                synchroniser_wineapi(cuvee, detail)
        return Response(_build_fiche(cuvee, request.user))

    @action(detail=True, methods=["post"])
    def rafraichir(self, request, pk=None):
        """Synchro à la demande : force un re-fetch des données wineapi de la fiche.

        Garde-fou anti-quota : un cooldown par vin (WINEAPI_REFRESH_COOLDOWN)
        empêche de re-solliciter wineapi trop souvent, en plus du throttle
        "enrichment" appliqué à cette action.
        """
        cuvee = self.get_object()
        reference = identifiant_wineapi(cuvee)
        if not reference:
            return Response(
                {"detail": "Aucune source externe à synchroniser pour ce vin."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cle_cooldown = f"wineapi:refresh-cooldown:{reference}"
        if cache.get(cle_cooldown):
            return Response(
                {"detail": "Fiche déjà synchronisée récemment. Réessaie plus tard."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        cache.set(cle_cooldown, True, settings.WINEAPI_REFRESH_COOLDOWN)

        detail = refresh_wineapi_detail(reference)
        synchroniser_wineapi(cuvee, detail)
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

        # --- Scénario 2 : fusion multi-sources (toutes les sources activées) ---
        hits, erreur = _cascade_multi(lambda p: p.lookup_by_barcode(ean))
        if hits:
            primary = hits[0]
            cuvee, created = upsert_multi(hits)
            return Response(
                {
                    "source": primary.source,
                    "created": created,
                    "millesime": primary.millesime,
                    "cuvee": CuveeSerializer(cuvee).data,
                }
            )

        # --- Scénario 3 : erreur remontable (quota/clé) ou échec total ---
        if erreur is not None:
            return Response({"detail": erreur.message}, status=erreur.status)
        return Response(
            {"source": None, "detail": "Vin non reconnu par son code-barres"},
            status=status.HTTP_404_NOT_FOUND,
        )


class IdentifierVinView(APIView):
    """
    US 04 — Identification d'un vin à partir de texte (nom, domaine, ou sortie OCR
    de l'US 03), via la cascade Claude puis wineapi.io.

    1. Base locale (nom) -> cache hit, aucun appel externe.
    2. Cascade des fournisseurs texte activés (Claude, puis wineapi.io) ->
       normalisation, persistance locale (domaine/cuvée/cépages), réponse enrichie.
    3. Échec -> 404 'Vin non identifié'.
    """

    serializer_class = IdentifierVinSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "enrichment"

    def post(self, request):
        serializer = IdentifierVinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data["query"]

        # --- Sélection d'une suggestion de la recherche dynamique ---
        # Le code LWIN désigne la référence sans ambiguïté : résolution locale
        # directe, aucun appel externe, aucun quota. Code inconnu (référentiel
        # ré-importé…) : on retombe sur le flux normal.
        code_lwin = serializer.validated_data.get("lwin", "")
        if code_lwin:
            reference = ReferenceLwin.objects.filter(lwin=code_lwin).first()
            if reference is not None:
                wine = LwinProvider()._to_normalized(
                    reference, confiance=1.0, millesime=parse_vintage(query)
                )
                cuvee, created = upsert_cuvee(wine)
                return _enriched_response(wine, cuvee, created)

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

        # --- Fusion multi-sources (toutes les sources texte activées) ---
        hits, erreur = _cascade_multi(lambda p: p.lookup_by_text(query))
        if hits:
            primary = hits[0]
            cuvee, created = upsert_multi(hits)
            return _enriched_response(primary, cuvee, created)

        # --- Erreur remontable (quota/clé) ou échec total ---
        if erreur is not None:
            return Response({"detail": erreur.message}, status=erreur.status)
        return Response(
            {"source": None, "detail": "Vin non identifié"},
            status=status.HTTP_404_NOT_FOUND,
        )


class RechercheVinsView(APIView):
    """
    Recherche dynamique (autocomplétion) dans le référentiel LWIN local.

    `GET /api/recherche-vins/?q=...` — pensé pour être appelé au fil de la
    frappe (debounce côté client) : 100 % local (aucun quota externe), le
    dernier mot est traité comme un préfixe (« marg » -> « Margaux »), la
    requête est comprise sémantiquement (millésime extrait, couleur mentionnée
    appliquée en filtre : « palmer rouge 2015 »), et la tolérance aux fautes
    de frappe est celle de la correspondance LWIN.

    Réponse : `{"evaluation", "resultats": [{lwin, libelle, producteur, vin,
    appellation, region, pays, couleur, score, millesime, en_base}]}`.
    ``evaluation`` guide l'affichage : « sur » (le meilleur candidat domine,
    l'appli peut proposer sa fiche pré-remplie), « hesitant » (plusieurs
    candidats plausibles, vérification manuelle) ou null. ``en_base`` porte,
    quand la cuvée existe déjà dans le catalogue partagé (mutualisé, nourri
    par les autres utilisateurs), de quoi étoffer la fiche proposée : cépages,
    note de la communauté, accords mets-vins — jamais de donnée privée.
    Sélectionner un résultat côté client revient à appeler `identifier-vin`
    avec son code `lwin`.
    """

    serializer_class = RechercheVinsSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "recherche"

    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response({"evaluation": None, "resultats": []})

        # Compréhension de la requête : millésime et couleur sont extraits et
        # appliqués (filtre couleur, millésime renvoyé pour pré-remplir).
        millesime = parse_vintage(q)
        couleur = guess_couleur(q)
        resultats = rechercher_lwin(q, limite=6, couleur=couleur if couleur != "AUTRE" else None)

        # Étoffe les suggestions déjà présentes dans le catalogue partagé :
        # ces cuvées ont été enrichies par la communauté (cépages, note,
        # accords), la fiche proposée les affiche sans aucun appel externe.
        cuvees = {
            c.lwin_code: c
            for c in Cuvee.objects.filter(
                lwin_code__in=[r["lwin"] for r in resultats]
            ).prefetch_related("cepages")
        }
        for r in resultats:
            r["millesime"] = millesime
            cuvee = cuvees.get(r["lwin"])
            r["en_base"] = None
            if cuvee is not None:
                r["en_base"] = {
                    "cuvee_id": cuvee.id,
                    "cepages": [c.nom for c in cuvee.cepages.all()],
                    "note": float(cuvee.note_moyenne) if cuvee.note_moyenne is not None else None,
                    "nb_notes": cuvee.nb_notes,
                    "accords": (cuvee.accords or [])[:4],
                    "image_url": cuvee.image_url,
                }
        return Response({"evaluation": evaluer_confiance(resultats), "resultats": resultats})


class ScanEtiquetteView(APIView):
    """
    US 02/03 — Identification d'un vin à partir d'une photo d'étiquette
    (JPEG/PNG ≤ 10 Mo), via la cascade Claude (vision) puis wineapi.io.

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

        # Réduction unique, partagée par toutes les sources distantes : une photo
        # de téléphone est bien plus lourde que ce que les APIs de vision
        # exploitent, et chacune la ré-encodait à pleine taille pour son propre
        # transport. L'OCR local garde l'original (cf. enrichment.image).
        reduite, type_reduit = reduire_image(data, image.content_type)

        def appel(p):
            # getattr : le contrat porte l'attribut sur EnrichmentProvider, mais
            # la cascade reste tolérante à un provider simplement « canard ».
            if getattr(p, "image_pleine_resolution", False):
                return p.lookup_by_image(data, image.content_type)
            return p.lookup_by_image(reduite, type_reduit)

        hits, erreur = _cascade_multi(appel)
        if hits:
            primary = hits[0]
            cuvee, created = upsert_multi(hits)
            _conserver_etiquette(cuvee, data)
            return _enriched_response(primary, cuvee, created)

        if erreur is not None:
            return Response({"detail": erreur.message}, status=erreur.status)
        return Response(
            {"source": None, "detail": "Vin non identifié sur l'étiquette"},
            status=status.HTTP_404_NOT_FOUND,
        )
