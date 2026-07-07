from django.contrib import admin

from .models import Cave, Emplacement


@admin.register(Cave)
class CaveAdmin(admin.ModelAdmin):
    list_display = ["nom", "proprietaire"]
    search_fields = ["nom"]


@admin.register(Emplacement)
class EmplacementAdmin(admin.ModelAdmin):
    list_display = ["nom", "cave", "parent", "type_emplacement", "capacite"]
    list_filter = ["type_emplacement", "cave"]
    search_fields = ["nom"]
    autocomplete_fields = ["parent"]
