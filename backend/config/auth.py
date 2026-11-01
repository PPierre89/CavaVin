from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import generics, permissions, serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

User = get_user_model()


class MeView(generics.RetrieveAPIView):
    """Profil du compte connecté (identité + rôle).

    Permet au frontend de savoir s'il doit exposer le panneau d'administration
    (``is_staff``) sans avoir à décoder le JWT côté client."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        return Response(
            {
                "username": user.username,
                "email": user.email,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
            }
        )


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """Login JWT (`/api/auth/token/`) soumis au throttle ``auth`` (anti-brute-force).

    La vue standard de simplejwt n'a aucune limite : sans ça, le mot de passe
    d'un compte peut être deviné par force brute sans plafond. Le scope ``auth``
    (cf. settings) limite les tentatives par IP/utilisateur, comme l'inscription.
    """

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class RegisterSerializer(serializers.ModelSerializer):
    """Inscription : crée un compte et renvoie directement une paire de tokens JWT."""

    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = User
        fields = ["username", "email", "password"]
        extra_kwargs = {"email": {"required": False}}

    def validate_username(self, value):
        # Unicité insensible à la casse : empêche de créer « Pierre » quand
        # « pierre » existe déjà. Sans ça, un utilisateur qui n'arrive pas à se
        # connecter (majuscule automatique du clavier mobile) crée un doublon
        # vide et croit avoir perdu ses données.
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Un compte existe déjà avec cet identifiant.")
        return value

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data.get("email", ""),
            password=validated_data["password"],
        )


class RegisterView(generics.CreateAPIView):
    """Inscription en libre-service, soumise à ``settings.INSCRIPTION_OUVERTE``.

    Fermée par défaut : un compte peut écrire dans le catalogue mutualisé et
    déclenche des identifications facturées sur la clé de l'instance. Le staff
    crée alors les comptes depuis le panneau d'administration. ``GET`` indique
    à l'écran de connexion s'il doit proposer l'inscription.
    """

    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    # Limite les tentatives de création de compte (anti-abus).
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def get_throttles(self):
        # La simple lecture de l'état (affichage de l'écran de connexion) ne
        # consomme pas le budget anti-brute-force du scope « auth ».
        if self.request.method == "GET":
            return []
        return super().get_throttles()

    def get(self, request, *args, **kwargs):
        return Response({"ouverte": settings.INSCRIPTION_OUVERTE})

    def create(self, request, *args, **kwargs):
        if not settings.INSCRIPTION_OUVERTE:
            raise PermissionDenied(
                "Les inscriptions sont fermées sur cette instance : demandez un compte "
                "à son administrateur."
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "username": user.username,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )
