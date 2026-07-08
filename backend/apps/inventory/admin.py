from django.contrib import admin

from .models import Bouteille, MouvementStock, NoteDegustation


class MouvementStockInline(admin.TabularInline):
    model = MouvementStock
    extra = 0
    readonly_fields = ["date"]


@admin.register(Bouteille)
class BouteilleAdmin(admin.ModelAdmin):
    list_display = ["cuvee", "millesime", "quantite", "statut", "emplacement"]
    list_filter = ["statut", "cuvee__couleur"]
    search_fields = ["cuvee__nom", "cuvee__domaine__nom"]
    autocomplete_fields = ["cuvee", "emplacement"]
    inlines = [MouvementStockInline]


@admin.register(MouvementStock)
class MouvementStockAdmin(admin.ModelAdmin):
    list_display = ["bouteille", "type_mouvement", "quantite", "date", "occasion"]
    list_filter = ["type_mouvement"]
    search_fields = ["bouteille__cuvee__nom", "occasion"]


@admin.register(NoteDegustation)
class NoteDegustationAdmin(admin.ModelAdmin):
    list_display = ["cuvee", "millesime", "note", "proprietaire", "date_degustation"]
    list_filter = ["note", "cuvee__couleur"]
    search_fields = ["cuvee__nom", "cuvee__domaine__nom", "commentaire"]
    autocomplete_fields = ["cuvee"]
