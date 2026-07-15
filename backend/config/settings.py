"""
Django settings for the Cave à Vin API project.
"""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR.parent / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-dev-key-change-me")

DEBUG = os.getenv("DJANGO_DEBUG", "True") == "True"

# Garde-fou : la clé de dev ne doit jamais tourner en production (signature des
# sessions/tokens prévisible). On avertit sans bloquer le démarrage.
if not DEBUG and SECRET_KEY == "django-insecure-dev-key-change-me":
    import logging

    logging.getLogger(__name__).warning(
        "DJANGO_SECRET_KEY n'est pas définie : la clé de développement est utilisée "
        "en production. Définis une vraie valeur dans .env."
    )

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()
]

# Origines de confiance pour la vérification CSRF — nécessaire pour les POST
# (login admin, frontend) servis via un domaine proxifié en HTTPS (accès distant
# UGREEN, reverse proxy…). Format : "https://mon.domaine", séparés par des virgules ;
# le joker de sous-domaine est accepté ("https://*.ugdocker.link").
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

# Derrière un reverse proxy qui termine le TLS, se fier à l'en-tête X-Forwarded-Proto
# pour que Django sache que la requête d'origine est en HTTPS. Activer via
# DJANGO_TRUST_PROXY_SSL=True.
if os.getenv("DJANGO_TRUST_PROXY_SSL", "False") == "True":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # L'accès se fait alors en HTTPS : on n'expose plus les cookies sensibles
    # (session admin, jeton CSRF) sur une éventuelle connexion en clair.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # HSTS : opt-in (défaut 0 = désactivé) pour ne pas verrouiller par erreur un
    # accès LAN en HTTPS. Mets p. ex. DJANGO_HSTS_SECONDS=31536000 (1 an) une fois
    # le HTTPS stable et permanent sur le domaine d'accès distant.
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
    SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    # Local apps
    "apps.catalog",
    "apps.cellars",
    "apps.inventory",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Sert les fichiers statiques (CSS/JS de l'admin, Swagger UI) directement
    # depuis Gunicorn quand DEBUG=False — évite d'avoir à ajouter nginx pour un
    # déploiement NAS simple.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database — SQLite (aucun service externe ; déploiement mono-conteneur).
# En conteneur, SQLITE_PATH pointe vers un volume persistant (ex: /data/db.sqlite3)
# pour survivre à une recréation du conteneur ; en local, défaut backend/db.sqlite3.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("SQLITE_PATH") or (BASE_DIR / "db.sqlite3"),
        "OPTIONS": {
            # Plusieurs workers gunicorn écrivent dans la même base : WAL permet
            # lecteurs et écrivain simultanés, et le timeout évite les erreurs
            # "database is locked" immédiates en attendant le verrou.
            "timeout": 20,
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            # Prend le verrou d'écriture dès le début de la transaction plutôt
            # qu'au premier write — évite les deadlocks de promotion de verrou.
            "transaction_mode": "IMMEDIATE",
        },
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Identifiant insensible à la casse : indispensable pour retrouver le même
# compte depuis plusieurs appareils (la majuscule automatique des claviers
# mobiles transforme sinon « pierre » en « Pierre » et casse la connexion).
AUTHENTICATION_BACKENDS = ["config.auth_backends.CaseInsensitiveModelBackend"]


LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# SPA React (Vite) : le build est copié dans backend/spa/ par le Dockerfile.
# WhiteNoise sert ses assets (/assets/...) directement à la racine ; la vue index
# renvoie spa/index.html. En dev, on utilise le serveur Vite (:5173) à la place.
SPA_DIR = BASE_DIR / "spa"
if SPA_DIR.exists():
    WHITENOISE_ROOT = SPA_DIR

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Traduit ProtectedError (suppression d'un objet encore référencé) en 409
    # au lieu d'un 500 brut.
    "EXCEPTION_HANDLER": "config.exceptions.exception_handler",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # JWT first: the primary path for external/mobile clients.
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        # Session auth keeps the browser-based mini frontend (which reuses the
        # Django admin login) and the Swagger "Authorize" flow working.
        "rest_framework.authentication.SessionAuthentication",
        # (BasicAuthentication retirée : inutilisée, et enverrait les identifiants
        # en clair à chaque requête sur un déploiement LAN en HTTP.)
    ],
    # Limite les endpoints qui consomment le quota wineapi / Open Food Facts
    # (scan code-barres, identification texte, scan d'étiquette).
    "DEFAULT_THROTTLE_RATES": {
        "enrichment": "30/min",
        "auth": "10/min",
        # Recherche dynamique (autocomplétion) : 100 % locale (aucun quota
        # externe), appelée au fil de la frappe — cadence large mais bornée.
        "recherche": "120/min",
    },
}

# Version de l'app : source de vérité unique = le tag de release (semver),
# injecté par la CI dans l'environnement (APP_VERSION) et baké dans l'image
# Docker. En local (hors release), on retombe sur une valeur de dev.
APP_VERSION = os.getenv("APP_VERSION", "0.0.0-dev")

SPECTACULAR_SETTINGS = {
    "TITLE": "Cave à Vin API",
    "DESCRIPTION": "API de gestion de cave à vin : catalogue, emplacements, stock et mouvements.",
    "VERSION": APP_VERSION,
    "SERVE_INCLUDE_SCHEMA": False,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# --- Enrichissement externe (US 04) ---
# wineapi.io : identification d'un vin à partir de texte (sortie OCR ou saisie).
# La clé est chargée depuis l'environnement (.env), jamais codée en dur. Le
# provider est automatiquement désactivé si la clé est absente.
WINEAPI_BASE_URL = os.getenv("WINEAPI_BASE_URL", "https://api.wineapi.io")
WINEAPI_KEY = os.getenv("WINEAPI_KEY", "")
# Timeouts des appels sortants wineapi. IMPORTANT : ils doivent rester
# STRICTEMENT INFÉRIEURS au timeout worker gunicorn, sinon un appel lent (surtout
# la vision) dépasse le worker et gunicorn le tue (WORKER TIMEOUT) au lieu de
# renvoyer proprement un 404. Le repo lance gunicorn avec --timeout 120, mais un
# docker-compose.yml obsolète peut retomber au défaut gunicorn (30 s) : on garde
# donc des valeurs par défaut sûres même dans ce cas (< 30 s). Sur un déploiement
# où le worker a un timeout confortable, on peut les relever via l'environnement.
WINEAPI_TIMEOUT = int(os.getenv("WINEAPI_TIMEOUT", "20"))
# L'identification par photo d'étiquette (vision) est bien plus lente : timeout dédié.
WINEAPI_IMAGE_TIMEOUT = int(os.getenv("WINEAPI_IMAGE_TIMEOUT", "25"))
# Appelle /wines/{id} après identification pour récupérer cépages/appellation/notes.
WINEAPI_ENRICH_DETAIL = os.getenv("WINEAPI_ENRICH_DETAIL", "True") == "True"
# Garde-fou du bouton de synchro de la fiche : délai minimal (secondes) entre deux
# rafraîchissements forcés d'un même vin, pour préserver le quota d'appels wineapi.
WINEAPI_REFRESH_COOLDOWN = int(os.getenv("WINEAPI_REFRESH_COOLDOWN", "3600"))

# --- Enrichissement GrapeMinds (api.grapeminds.eu) ---
# Base de données œnologique : identification par texte (/wines/search + /wines/{id})
# et, sur l'offre Enterprise, analyse d'étiquette par photo (/photo/analyze).
# La clé est chargée depuis l'environnement (.env), jamais codée en dur.
GRAPEMINDS_BASE_URL = os.getenv("GRAPEMINDS_BASE_URL", "https://api.grapeminds.eu/public/v1")
GRAPEMINDS_KEY = os.getenv("GRAPEMINDS_KEY", "")
# Slot DÉSACTIVÉ PAR DÉFAUT même clé renseignée : il faut l'activer explicitement.
# Deux raisons : (1) les conditions GrapeMinds imposent une licence de stockage
# persistant (PSL) payante pour conserver durablement leurs données dans le
# catalogue local — à valider avant activation ; (2) quota serré (~250 appels/mois),
# ce garde-fou évite de le consommer par inadvertance.
GRAPEMINDS_ENABLED = os.getenv("GRAPEMINDS_ENABLED", "False") == "True"
# Langue des libellés négociée via Accept-Language (de, en, es, fr, it, da).
GRAPEMINDS_LANG = os.getenv("GRAPEMINDS_LANG", "fr")
# Timeouts sortants. Comme wineapi : STRICTEMENT INFÉRIEURS au timeout worker
# gunicorn (--timeout 120), et sûrs même sur une conf obsolète (< 30 s).
GRAPEMINDS_TIMEOUT = int(os.getenv("GRAPEMINDS_TIMEOUT", "20"))
# L'analyse photo (vision, Enterprise) est plus lente : timeout dédié.
GRAPEMINDS_IMAGE_TIMEOUT = int(os.getenv("GRAPEMINDS_IMAGE_TIMEOUT", "25"))
# Appelle /wines/{id} après la recherche pour récupérer cépages/région/descriptions.
GRAPEMINDS_ENRICH_DETAIL = os.getenv("GRAPEMINDS_ENRICH_DETAIL", "True") == "True"
# Analyse d'étiquette par photo : désactivée par défaut (offre Enterprise requise ;
# sinon l'endpoint répond 402/403 et gaspille un appel de quota).
GRAPEMINDS_PHOTO_ANALYSIS = os.getenv("GRAPEMINDS_PHOTO_ANALYSIS", "False") == "True"
# Nombre de candidats demandés à la recherche / analyse (on retient le meilleur).
GRAPEMINDS_SEARCH_LIMIT = int(os.getenv("GRAPEMINDS_SEARCH_LIMIT", "5"))

# --- Enrichissement Claude (Anthropic) ---
# Identification par photo d'étiquette (vision) et par texte, en tête de cascade
# devant wineapi. La clé est chargée depuis l'environnement (.env), jamais codée
# en dur ; le provider est automatiquement désactivé si elle est absente.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
# Timeout de l'appel Claude (secondes). IMPORTANT : doit rester STRICTEMENT
# INFÉRIEUR au timeout worker gunicorn (le repo lance --timeout 120), sinon un
# appel vision lent fait tuer le worker (WORKER TIMEOUT) au lieu de rendre la
# main à la cascade. Le SDK réessaie une fois (max_retries=1) : le budget total
# peut donc atteindre 2 × cette valeur en cas d'erreur réseau.
ANTHROPIC_TIMEOUT = int(os.getenv("ANTHROPIC_TIMEOUT", "50"))

# --- Repli local : OCR Tesseract + référentiel LWIN (aucune clé, aucun réseau) ---
# Dernier maillon de la cascade d'identification. Nécessite l'import du dump
# LWIN (`manage.py import_lwin`) pour le texte, et le binaire tesseract (présent
# dans l'image Docker) pour la photo d'étiquette. LWIN_ENABLED=False le coupe.
LWIN_ENABLED = os.getenv("LWIN_ENABLED", "True") == "True"
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "tesseract")
# Budget total de l'OCR en secondes (toutes passes tesseract confondues) —
# même contrainte que ci-dessus : < timeout worker.
TESSERACT_TIMEOUT = int(os.getenv("TESSERACT_TIMEOUT", "20"))
