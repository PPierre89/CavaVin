"""Panneau d'administration (réservé au staff).

Regroupe les endpoints du « control panel » exposés au frontend admin :
- un aperçu global du déploiement (comptes, catalogue mutualisé, stock, système) ;
- la gestion des comptes utilisateurs (activation, staff, suppression).

Tout est cloisonné derrière ``IsAdminUser`` (``user.is_staff``). Ces vues ne
touchent qu'aux métadonnées des comptes et à des agrégats : elles ne donnent
jamais accès aux données *privées* d'un autre utilisateur (bouteilles, notes),
conformément à la séparation RGPD du projet.
"""

import logging
import os
import tempfile
import threading

from django.conf import settings
from django.db import connection
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from rest_framework import serializers, status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.catalogue_portable import charger
from apps.catalog.enrichment import get_all_providers
from apps.catalog.lwin_import import LwinImportError, importer_lwin
from apps.catalog.models import Cepage, Cuvee, Domaine, ReferenceLwin, TacheImport
from apps.catalog import quotas
from apps.catalog.runtime_config import (
    CLES_PILOTABLES,
    SOURCES_PILOTABLES,
    definir_source_activee,
    effacer_parametre,
    etat_parametre,
    set_parametre,
    source_override,
)
from apps.cellars.models import Cave, Emplacement
from apps.inventory.models import Bouteille, MouvementStock, NoteDegustation

User = get_user_model()

logger = logging.getLogger(__name__)


class ApercuView(APIView):
    """Aperçu global du déploiement (tableau de bord admin)."""

    permission_classes = [IsAdminUser]

    def get(self, request):
        users = User.objects.all()
        unites = Bouteille.objects.aggregate(total=Sum("quantite"))["total"] or 0
        providers = [
            {"nom": p.name, "actif": bool(p.enabled)} for p in get_all_providers()
        ]
        return Response(
            {
                "utilisateurs": {
                    "total": users.count(),
                    "actifs": users.filter(is_active=True).count(),
                    "staff": users.filter(is_staff=True).count(),
                },
                "catalogue": {
                    "domaines": Domaine.objects.count(),
                    "cepages": Cepage.objects.count(),
                    "cuvees": Cuvee.objects.count(),
                    "references_lwin": ReferenceLwin.objects.count(),
                },
                "stock": {
                    "lignes": Bouteille.objects.count(),
                    "unites": unites,
                    "caves": Cave.objects.count(),
                    "emplacements": Emplacement.objects.count(),
                },
                "activite": {
                    "notes_degustation": NoteDegustation.objects.count(),
                    "mouvements": MouvementStock.objects.count(),
                },
                "systeme": {
                    "version": settings.APP_VERSION,
                    "debug": settings.DEBUG,
                    "providers": providers,
                },
            }
        )


class UtilisateurSerializer(serializers.ModelSerializer):
    """Compte utilisateur vu par l'admin (métadonnées + volumétrie privée agrégée)."""

    # Volumétrie privée de l'utilisateur, en simple comptage (jamais le contenu).
    nb_bouteilles = serializers.IntegerField(read_only=True)
    nb_caves = serializers.IntegerField(read_only=True)
    nb_degustations = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "is_active",
            "is_staff",
            "is_superuser",
            "date_joined",
            "last_login",
            "nb_bouteilles",
            "nb_caves",
            "nb_degustations",
        ]
        # Seuls l'activation, le rôle staff et l'email sont modifiables ; le reste
        # est en lecture seule (identité, dates, super-utilisateur, compteurs).
        read_only_fields = [
            "id",
            "username",
            "is_superuser",
            "date_joined",
            "last_login",
            "nb_bouteilles",
            "nb_caves",
            "nb_degustations",
        ]


class UtilisateurViewSet(viewsets.ModelViewSet):
    """Gestion des comptes utilisateurs (staff uniquement).

    Lecture, activation/désactivation, promotion staff et suppression. Les
    garde-fous empêchent un admin de se verrouiller lui-même dehors et
    protègent les super-utilisateurs (le propriétaire de l'instance).
    """

    permission_classes = [IsAdminUser]
    serializer_class = UtilisateurSerializer
    # Pas de création de compte ici (elle passe par l'inscription publique).
    http_method_names = ["get", "patch", "delete", "head", "options"]
    search_fields = ["username", "email"]
    ordering_fields = ["date_joined", "username", "last_login"]

    def get_queryset(self):
        return User.objects.annotate(
            nb_bouteilles=Count("bouteilles", distinct=True),
            nb_caves=Count("caves", distinct=True),
            nb_degustations=Count("degustations", distinct=True),
        ).order_by("-date_joined")

    def _verifier_cible(self, instance):
        """Refuse les actions dangereuses : sur soi-même, ou sur un super-utilisateur
        depuis un compte qui ne l'est pas."""
        if instance == self.request.user:
            raise ValidationError(
                "Vous ne pouvez pas modifier ou supprimer votre propre compte depuis le panneau."
            )
        if instance.is_superuser and not self.request.user.is_superuser:
            raise PermissionDenied(
                "Seul un super-utilisateur peut agir sur un autre super-utilisateur."
            )

    def perform_update(self, serializer):
        self._verifier_cible(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._verifier_cible(instance)
        instance.delete()


class ConfigurationView(APIView):
    """Paramétrage à chaud des clés d'API (staff uniquement).

    ``GET`` liste les paramètres pilotables avec leur état — les secrets ne sont
    jamais renvoyés en clair, seulement masqués (``••••``+4 derniers). ``PUT``
    enregistre une valeur (override en base, prioritaire sur le ``.env``) ; une
    valeur vide efface l'override et fait retomber le paramètre sur le ``.env``.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response({"parametres": [etat_parametre(cle) for cle in CLES_PILOTABLES]})

    def put(self, request):
        cle = request.data.get("cle")
        if cle not in CLES_PILOTABLES:
            raise ValidationError({"cle": "Paramètre inconnu ou non modifiable."})
        # On tolère une valeur absente/None comme un effacement de l'override.
        valeur = request.data.get("valeur")
        valeur = "" if valeur is None else str(valeur).strip()
        if valeur:
            set_parametre(cle, valeur)
        else:
            effacer_parametre(cle)
        return Response({"parametres": [etat_parametre(c) for c in CLES_PILOTABLES]})


class ImportLwinView(APIView):
    """Upload d'un dump LWIN (XLSX/CSV) pour (ré)injecter le référentiel (staff).

    Le fichier est ingéré via la même logique que ``manage.py import_lwin``
    (idempotent : upsert par code LWIN). Réservé au staff : l'import remplace un
    accès shell au conteneur pour alimenter le repli d'identification gratuit.
    """

    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    # Garde-fou de taille : le dump Liv-ex complet pèse ~30–40 Mo ; on laisse une
    # marge confortable sans permettre un upload arbitrairement gros.
    TAILLE_MAX = 100 * 1024 * 1024

    def post(self, request):
        fichier = request.FILES.get("fichier")
        if fichier is None:
            raise ValidationError({"fichier": "Aucun fichier fourni."})
        if fichier.size > self.TAILLE_MAX:
            raise ValidationError({"fichier": "Fichier trop volumineux (max 100 Mo)."})

        nom = fichier.name or ""
        suffixe = os.path.splitext(nom)[1].lower() or ".csv"
        if suffixe not in (".xlsx", ".xlsm", ".csv", ".txt"):
            raise ValidationError({"fichier": "Format non supporté (attendu : .xlsx ou .csv)."})
        delimiter = request.data.get("delimiter") or ","

        # On écrit l'upload dans un fichier temporaire : l'ingestion XLSX/CSV
        # travaille sur un chemin (lecture en flux, économe en mémoire).
        tmp = tempfile.NamedTemporaryFile(suffix=suffixe, delete=False)
        try:
            for morceau in fichier.chunks():
                tmp.write(morceau)
            tmp.close()
            importes = importer_lwin(tmp.name, delimiter)
        except LwinImportError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            os.unlink(tmp.name)

        return Response(
            {"importes": importes, "total": ReferenceLwin.objects.count()},
            status=status.HTTP_200_OK,
        )


class TacheImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = TacheImport
        fields = [
            "id", "type_import", "nom_fichier", "etat", "avancement", "message",
            "demarree_le", "terminee_le",
        ]
        read_only_fields = fields


def _executer_chargement(tache_id: int, chemin: str) -> None:
    """Charge un catalogue en tâche de fond, puis consigne le résultat.

    Tourne dans un thread : Django ne referme que la connexion du thread de
    requête, donc celle-ci doit être fermée à la main en sortie — même piège que
    la cascade d'identification parallèle (``catalog.views._cascade_multi``).
    """
    try:
        def progression(resultat):
            TacheImport.objects.filter(pk=tache_id).update(avancement=vars(resultat))

        resultat = charger(chemin, progression=progression)
        TacheImport.objects.filter(pk=tache_id).update(
            etat=TacheImport.Etat.TERMINEE,
            avancement=vars(resultat),
            terminee_le=timezone.now(),
        )
    except Exception as exc:  # noqa: BLE001 - filet volontaire, cf. commentaire
        # Un échec doit rester *visible* : sans ce filet la tâche resterait « en
        # cours » indéfiniment et bloquerait toutes les suivantes (un seul import
        # à la fois).
        logger.exception("chargement de catalogue interrompu")
        TacheImport.objects.filter(pk=tache_id).update(
            etat=TacheImport.Etat.ECHEC,
            message=str(exc)[:2000],
            terminee_le=timezone.now(),
        )
    finally:
        try:
            os.unlink(chemin)
        except OSError:
            pass
        connection.close()


class ChargerCatalogueView(APIView):
    """Upload et chargement d'un catalogue transportable (staff).

    Le fichier est produit par ``manage.py exporter_catalogue`` sur une autre
    installation. Il ne contient que le catalogue mutualisé : le chargement
    n'écrit jamais dans les caves, bouteilles ou notes de dégustation.

    Répond **202 sans attendre**. Un chargement dure plusieurs minutes, là où
    ``gunicorn.conf.py`` coupe un worker à 120 s : une vue synchrone se ferait
    tuer en plein travail, laissant un import à moitié fait. Le panneau suit
    l'avancement en interrogeant ``GET`` sur ce même point d'entrée.
    """

    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    # Un catalogue complet avec ses relevés bruts peut peser lourd ; la variante
    # allégée (``--sans-observations``) tient en quelques dizaines de Mo.
    TAILLE_MAX = 500 * 1024 * 1024

    def get(self, request):
        """Dernières tâches d'import, la plus récente d'abord."""
        return Response(TacheImportSerializer(TacheImport.objects.all()[:10], many=True).data)

    def post(self, request):
        if TacheImport.une_est_en_cours():
            return Response(
                {"detail": "Un import est déjà en cours. Attendez qu'il se termine."},
                status=status.HTTP_409_CONFLICT,
            )

        fichier = request.FILES.get("fichier")
        if fichier is None:
            raise ValidationError({"fichier": "Aucun fichier fourni."})
        if fichier.size > self.TAILLE_MAX:
            raise ValidationError({"fichier": "Fichier trop volumineux (max 500 Mo)."})

        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        for morceau in fichier.chunks():
            tmp.write(morceau)
        tmp.close()

        tache = TacheImport.objects.create(
            type_import=TacheImport.Type.CATALOGUE,
            nom_fichier=(fichier.name or "")[:255],
        )
        threading.Thread(
            target=_executer_chargement, args=(tache.pk, tmp.name), daemon=True
        ).start()
        return Response(TacheImportSerializer(tache).data, status=status.HTTP_202_ACCEPTED)


def _etat_source(provider) -> dict:
    """État complet d'une source d'identification pour le panneau d'admin :
    activation effective, intention admin, et quota (usage / plafond)."""
    etat = {
        "source": provider.name,
        # ``actif`` = état effectif (combine l'intention et les prérequis : clé
        # d'API présente…). Un toggle « on » sans clé restera donc inactif.
        "actif": bool(provider.enabled),
        # ``voulu`` = intention admin brute (None = on suit le défaut .env).
        "voulu": source_override(provider.name),
    }
    etat.update(quotas.etat(provider.name))
    return etat


def _sources_pilotables() -> list[dict]:
    par_nom = {p.name: p for p in get_all_providers()}
    return [_etat_source(par_nom[n]) for n in SOURCES_PILOTABLES if n in par_nom]


class SourcesView(APIView):
    """Sources d'identification : activation (on/off) et quota mensuel (staff).

    ``GET`` liste les sources pilotables avec leur état effectif, l'intention admin
    et la consommation du mois face au plafond. ``PUT`` accepte ``{source, actif}``
    (toggle on/off, override en base prioritaire sur le ``.env``) et/ou
    ``{source, plafond}`` (plafond mensuel : entier > 0 pour limiter, ``0`` pour
    illimité, ``null`` pour revenir au défaut). Le comptage d'usage n'expose aucune
    donnée privée — seulement des agrégats par source.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response({"sources": _sources_pilotables()})

    def put(self, request):
        source = request.data.get("source")
        if source not in SOURCES_PILOTABLES:
            raise ValidationError({"source": "Source inconnue ou non pilotable."})

        if "actif" in request.data:
            definir_source_activee(source, bool(request.data.get("actif")))

        if "plafond" in request.data:
            plafond = request.data.get("plafond")
            if plafond is None:
                # Retour au plafond par défaut de la source.
                effacer_parametre(quotas.cle_plafond(source))
            else:
                try:
                    valeur = int(plafond)
                except (TypeError, ValueError):
                    raise ValidationError({"plafond": "Entier attendu (0 = illimité)."})
                if valeur < 0:
                    raise ValidationError({"plafond": "Le plafond ne peut pas être négatif."})
                set_parametre(quotas.cle_plafond(source), str(valeur))

        return Response({"sources": _sources_pilotables()})
