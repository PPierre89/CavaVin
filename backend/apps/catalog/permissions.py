from rest_framework.permissions import SAFE_METHODS, BasePermission


class LectureOuEcritureSansSuppression(BasePermission):
    """Permission du catalogue mutualisé (Domaine, Cepage, Cuvee).

    - Lecture (GET/HEAD/OPTIONS) : publique.
    - Création (POST) : tout utilisateur authentifié — le catalogue est
      communautaire : la saisie manuelle d'un vin inconnu crée son domaine et sa
      cuvée, et les actions comme ``rafraichir`` passent par POST.
    - Modification (PUT/PATCH) et suppression (DELETE) : réservées au staff. Une
      entrée existante est partagée par tous les utilisateurs : la réécrire, c'est
      changer la fiche de tous ceux qui l'ont en cave, et la supprimer peut
      détruire en cascade des données *privées* d'autres utilisateurs (ex. leurs
      notes de dégustation). Les corrections passent par la consolidation
      multi-sources, ou par le staff.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in ("PUT", "PATCH", "DELETE"):
            return bool(user.is_staff)
        return True
