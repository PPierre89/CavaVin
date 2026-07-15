from django.contrib import admin

from .models import (
    Cepage,
    Cuvee,
    Domaine,
    MillesimeReference,
    Parametre,
    ReferenceLwin,
    SourceObservation,
)


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
    # Snapshot brut wineapi + provenance consolidée : consultables mais non
    # éditables (alimentés par la synchro et la consolidation).
    readonly_fields = ["wineapi_detail", "enrichi_le", "provenance"]


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


@admin.register(MillesimeReference)
class MillesimeReferenceAdmin(admin.ModelAdmin):
    # Table sourçable, éditable : corriger une note met à jour le cache d'apogée.
    list_display = ["region_cle", "annee", "note", "source"]
    list_filter = ["region_cle", "source"]
    search_fields = ["region_cle"]
    list_editable = ["note"]


@admin.register(Parametre)
class ParametreAdmin(admin.ModelAdmin):
    # Override à chaud des clés d'API (prime sur le .env). Éditable ici aussi.
    list_display = ["cle", "maj_le"]
    search_fields = ["cle"]


admin.site.site_header = "Cave à Vin - Administration"
