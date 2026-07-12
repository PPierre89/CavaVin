from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import generics, permissions, serializers
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

    def create(self, validated_data):
        return User.objects.create_user(
            username=validated_data["username"],
            email=validated_data.get("email", ""),
            password=validated_data["password"],
        )


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    # Limite les tentatives de création de compte (anti-abus).
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def create(self, request, *args, **kwargs):
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
            status=201,
        )
