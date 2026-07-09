from django.contrib import admin

from .models import Cepage, Cuvee, Domaine


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


admin.site.site_header = "Cave à Vin - Administration"
