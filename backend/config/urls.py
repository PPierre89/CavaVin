from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenRefreshView,
    TokenVerifyView,
)

from apps.catalog.views import (
    CepageViewSet,
    CuveeViewSet,
    DomaineViewSet,
    IdentifierVinView,
    RechercheVinsView,
    ScanCodeBarresView,
    ScanEtiquetteView,
)
from apps.cellars.views import CaveViewSet, EmplacementViewSet
from apps.inventory.views import (
    BouteilleViewSet,
    MouvementStockViewSet,
    NoteDegustationViewSet,
    RangementViewSet,
)
from .admin_panel import ApercuView as AdminApercuView
from .admin_panel import ConfigurationView as AdminConfigurationView
from .admin_panel import ImportLwinView as AdminImportLwinView
from .admin_panel import UtilisateurViewSet as AdminUtilisateurViewSet
from .auth import MeView, RegisterView, ThrottledTokenObtainPairView
from .views import index

router = DefaultRouter()
router.register("domaines", DomaineViewSet, basename="domaine")
router.register("cepages", CepageViewSet, basename="cepage")
router.register("cuvees", CuveeViewSet, basename="cuvee")
router.register("caves", CaveViewSet, basename="cave")
router.register("emplacements", EmplacementViewSet, basename="emplacement")
router.register("bouteilles", BouteilleViewSet, basename="bouteille")
router.register("rangements", RangementViewSet, basename="rangement")
router.register("mouvements", MouvementStockViewSet, basename="mouvement")
router.register("notes-degustation", NoteDegustationViewSet, basename="note-degustation")
router.register("admin-panel/utilisateurs", AdminUtilisateurViewSet, basename="admin-utilisateur")

urlpatterns = [
    path("", index, name="home"),
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    path("api/scan-code-barres/", ScanCodeBarresView.as_view(), name="scan-code-barres"),
    path("api/identifier-vin/", IdentifierVinView.as_view(), name="identifier-vin"),
    path("api/recherche-vins/", RechercheVinsView.as_view(), name="recherche-vins"),
    path("api/scan-etiquette/", ScanEtiquetteView.as_view(), name="scan-etiquette"),
    path("api/admin-panel/apercu/", AdminApercuView.as_view(), name="admin-apercu"),
    path("api/admin-panel/configuration/", AdminConfigurationView.as_view(), name="admin-configuration"),
    path("api/admin-panel/import-lwin/", AdminImportLwinView.as_view(), name="admin-import-lwin"),
    path("api/auth/me/", MeView.as_view(), name="me"),
    path("api/auth/", include("rest_framework.urls")),
    path("api/auth/register/", RegisterView.as_view(), name="register"),
    path("api/auth/token/", ThrottledTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
