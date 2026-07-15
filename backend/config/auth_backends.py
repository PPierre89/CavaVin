from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

User = get_user_model()


class CaseInsensitiveModelBackend(ModelBackend):
    """Authentifie en ignorant la casse de l'identifiant.

    Django distingue par défaut « Pierre » et « pierre ». Sur mobile, le clavier
    met une majuscule automatique au premier caractère : un compte créé en
    minuscules sur un appareil devient introuvable à la connexion depuis un
    autre (l'utilisateur finit par recréer un second compte vide et « perd »
    ses données). On résout donc l'identifiant sans tenir compte de la casse.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username is None or password is None:
            return None
        field = User.USERNAME_FIELD
        try:
            user = User.objects.get(**{f"{field}__iexact": username})
        except User.DoesNotExist:
            # Même coût de hachage que ModelBackend pour ne pas révéler par le
            # temps de réponse qu'un identifiant n'existe pas.
            User().set_password(password)
            return None
        except User.MultipleObjectsReturned:
            # Comptes historiques ne différant que par la casse : on retombe sur
            # une correspondance exacte pour rester déterministe.
            try:
                user = User.objects.get(**{field: username})
            except User.DoesNotExist:
                return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
