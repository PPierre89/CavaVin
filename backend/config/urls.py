from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from apps.catalog.views import (
    CepageViewSet,
    CuveeViewSet,
    DomaineViewSet,
    IdentifierVinView,
    ScanCodeBarresView,
    ScanEtiquetteView,
)
from apps.cellars.views import CaveViewSet, EmplacementViewSet
from apps.inventory.views import (
    BouteilleViewSet,
    MouvementStockViewSet,
    NoteDegustationViewSet,
)
from .auth import RegisterView
from .views import index

router = DefaultRouter()
router.register("domaines", DomaineViewSet, basename="domaine")
router.register("cepages", CepageViewSet, basename="cepage")
router.register("cuvees", CuveeViewSet, basename="cuvee")
router.register("caves", CaveViewSet, basename="cave")
router.register("emplacements", EmplacementViewSet, basename="emplacement")
router.register("bouteilles", BouteilleViewSet, basename="bouteille")
router.register("mouvements", MouvementStockViewSet, basename="mouvement")
router.register("notes-degustation", NoteDegustationViewSet, basename="note-degustation")

urlpatterns = [
    path("", index, name="home"),
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    path("api/scan-code-barres/", ScanCodeBarresView.as_view(), name="scan-code-barres"),
    path("api/identifier-vin/", IdentifierVinView.as_view(), name="identifier-vin"),
    path("api/scan-etiquette/", ScanEtiquetteView.as_view(), name="scan-etiquette"),
    path("api/auth/", include("rest_framework.urls")),
    path("api/auth/register/", RegisterView.as_view(), name="register"),
    path("api/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/auth/token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
