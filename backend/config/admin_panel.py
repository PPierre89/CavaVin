"""Panneau d'administration (réservé au staff).

Regroupe les endpoints du « control panel » exposés au frontend admin :
- un aperçu global du déploiement (comptes, catalogue mutualisé, stock, système) ;
- la gestion des comptes utilisateurs (activation, staff, suppression).

Tout est cloisonné derrière ``IsAdminUser`` (``user.is_staff``). Ces vues ne
touchent qu'aux métadonnées des comptes et à des agrégats : elles ne donnent
jamais accès aux données *privées* d'un autre utilisateur (bouteilles, notes),
conformément à la séparation RGPD du projet.
"""

import os
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from rest_framework import serializers, status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.enrichment import get_all_providers
from apps.catalog.lwin_import import LwinImportError, importer_lwin
from apps.catalog.models import Cepage, Cuvee, Domaine, ReferenceLwin
from apps.catalog.runtime_config import (
    CLES_PILOTABLES,
    effacer_parametre,
    etat_parametre,
    set_parametre,
)
from apps.cellars.models import Cave, Emplacement
from apps.inventory.models import Bouteille, MouvementStock, NoteDegustation

User = get_user_model()


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
