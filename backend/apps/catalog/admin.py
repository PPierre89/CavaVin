from django.contrib import admin

from .models import Cepage, Cuvee, Domaine, ReferenceLwin, SourceObservation


@admin.register(Domaine)
class DomaineAdmin(admin.ModelAdmin):
    list_display = ["nom", "region", "pays"]
    search_fields = ["nom", "region", "pays"]


@admin.register(Cepage)
class CepageAdmin(admin.ModelAdmin):
    list_display = ["nom"]
    search_fields = ["nom"]


@admin.register(Cuvee)
class CuveeAdmin(admin.ModelAdmin):
    list_display = ["nom", "domaine", "couleur", "appellation", "enrichi_le"]
    list_filter = ["couleur"]
    search_fields = ["nom", "domaine__nom", "code_barres"]
    autocomplete_fields = ["domaine", "cepages"]
    # Snapshot brut wineapi : consultable mais non éditable (alimenté par la synchro).
    readonly_fields = ["wineapi_detail", "enrichi_le"]


@admin.register(ReferenceLwin)
class ReferenceLwinAdmin(admin.ModelAdmin):
    # Référentiel importé en masse (manage.py import_lwin) : consultation seulement.
    list_display = ["lwin", "producteur", "vin", "region", "pays", "couleur"]
    list_filter = ["couleur", "pays"]
    search_fields = ["lwin", "producteur", "vin", "region"]


@admin.register(SourceObservation)
class SourceObservationAdmin(admin.ModelAdmin):
    # Historique brut par canal (append-only) : consultation seulement.
    list_display = ["cuvee", "canal", "confiance", "releve_le"]
    list_filter = ["canal"]
    search_fields = ["cuvee__nom", "cuvee__domaine__nom", "canal"]
    readonly_fields = ["cuvee", "canal", "confiance", "releve_le", "payload_brut", "champs"]

    def has_add_permission(self, request):
        return False


admin.site.site_header = "Cave à Vin - Administration"
