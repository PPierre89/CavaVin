from rest_framework.permissions import SAFE_METHODS, BasePermission


class LectureOuEcritureSansSuppression(BasePermission):
    """Permission du catalogue mutualisé (Domaine, Cepage, Cuvee).

    - Lecture (GET/HEAD/OPTIONS) : publique.
    - Création / édition (POST/PUT/PATCH) : tout utilisateur authentifié
      (le catalogue est communautaire, alimenté par les scans/identifications).
    - Suppression (DELETE) : réservée au staff. Supprimer une entrée du catalogue
      partagé peut détruire en cascade des données *privées* d'autres utilisateurs
      (ex. leurs notes de dégustation) : on ne laisse pas un utilisateur lambda
      effacer une référence mutualisée.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method == "DELETE":
            return bool(user.is_staff)
        return True
