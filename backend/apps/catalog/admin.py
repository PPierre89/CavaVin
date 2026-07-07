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
    list_display = ["nom", "domaine", "couleur", "appellation"]
    list_filter = ["couleur"]
    search_fields = ["nom", "domaine__nom", "code_barres"]
    autocomplete_fields = ["domaine", "cepages"]


admin.site.site_header = "Cave à Vin - Administration"
