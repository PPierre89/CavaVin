from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    """Gestionnaire d'exceptions DRF étendu.

    Traduit ``ProtectedError`` (levée quand on tente de supprimer un objet encore
    référencé par une clé étrangère ``PROTECT`` — ex. supprimer une cuvée qui a
    des bouteilles en stock, ou un domaine qui a des cuvées) en un ``409 Conflict``
    parlant, au lieu du ``500`` brut renvoyé par défaut.
    """
    response = drf_exception_handler(exc, context)
    if response is None and isinstance(exc, ProtectedError):
        return Response(
            {
                "detail": (
                    "Suppression impossible : cet élément est encore référencé "
                    "par des données existantes (stock, notes de dégustation…)."
                )
            },
            status=status.HTTP_409_CONFLICT,
        )
    return response
