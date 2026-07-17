"""Configuration modifiable à chaud (override base > .env).

Certains réglages — au premier chef les clés d'API d'enrichissement — doivent
pouvoir se régler depuis le panneau d'administration sans redémarrer le
conteneur. Ce module résout la valeur *effective* d'un paramètre :

    override en base (modèle ``Parametre``, si non vide) > variable
    d'environnement / ``settings`` (repli).

Les fournisseurs (``claude``, ``wineapi``) lisent leurs clés via
``get_parametre`` plutôt que ``settings`` directement, de sorte qu'un changement
en base prenne effet immédiatement. Un cache court amortit la lecture (une table
d'une poignée de lignes) et propage les changements entre workers gunicorn en
quelques secondes.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache

# Clés pilotables depuis l'admin, avec repli sur ``settings``/.env.
CLES_PILOTABLES: list[str] = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "WINEAPI_KEY",
    "WINEAPI_BASE_URL",
]

# Clés à traiter comme secrètes : jamais renvoyées en clair par l'API (masquées).
CLES_SECRETES: set[str] = {"ANTHROPIC_API_KEY", "WINEAPI_KEY"}

_PREFIXE = "cavavin:param:"
_TTL = 30  # secondes — propage un changement entre workers sans marteler la base.
_MISS = object()


def _cle_cache(cle: str) -> str:
    return _PREFIXE + cle


def _override_en_base(cle: str) -> str:
    """Valeur brute stockée en base pour ``cle`` (chaîne vide si absente).

    Tolérant : si la base n'est pas prête (migrations…), on renvoie vide pour
    retomber proprement sur le repli ``settings``."""
    cached = cache.get(_cle_cache(cle), _MISS)
    if cached is not _MISS:
        return cached
    from .models import Parametre

    try:
        obj = Parametre.objects.filter(cle=cle).first()
        valeur = obj.valeur if obj else ""
    except Exception:
        # Tolérant : base indisponible (migrations) ou contexte sans base -> on
        # retombe proprement sur le repli ``settings``/.env.
        return ""
    cache.set(_cle_cache(cle), valeur, _TTL)
    return valeur


def get_parametre(cle: str) -> str:
    """Valeur effective d'un paramètre : override base (non vide) sinon settings."""
    valeur = _override_en_base(cle)
    if valeur:
        return valeur
    return getattr(settings, cle, "")


def set_parametre(cle: str, valeur: str) -> None:
    """Enregistre (ou met à jour) un override en base et invalide le cache."""
    from .models import Parametre

    Parametre.objects.update_or_create(cle=cle, defaults={"valeur": valeur})
    cache.delete(_cle_cache(cle))


def effacer_parametre(cle: str) -> None:
    """Supprime l'override en base : le paramètre retombe sur le repli .env."""
    from .models import Parametre

    Parametre.objects.filter(cle=cle).delete()
    cache.delete(_cle_cache(cle))


_PREFIXE_SOURCE = "SOURCE_ENABLED_"  # clé Parametre : SOURCE_ENABLED_<nom>

# Sources d'identification pilotables (on/off + quota) depuis le panneau d'admin.
# Les stubs désactivés en permanence (vivino, cellartracker) en sont exclus.
SOURCES_PILOTABLES: list[str] = [
    "openfoodfacts",
    "claude",
    "wineapi",
    "grapeminds",
    "vinou",
    "lwin",
]


def source_override(nom: str) -> str | None:
    """Intention admin brute pour une source : ``"1"``, ``"0"`` ou ``None`` (défaut)."""
    brut = _override_en_base(_PREFIXE_SOURCE + nom)
    return brut if brut in ("0", "1") else None


def source_activee(nom: str, defaut: bool) -> bool:
    """État on/off *piloté depuis l'admin* d'une source d'enrichissement.

    Override en base (``SOURCE_ENABLED_<nom>`` = ``"1"``/``"0"``) prioritaire sur le
    défaut issu du ``.env``/settings. Ne préjuge pas des prérequis techniques (clé
    d'API présente…) : le fournisseur combine ce drapeau avec ses propres conditions
    dans sa propriété ``enabled``."""
    brut = _override_en_base(_PREFIXE_SOURCE + nom)
    if brut == "1":
        return True
    if brut == "0":
        return False
    return defaut


def definir_source_activee(nom: str, actif: bool) -> None:
    """Fixe l'override on/off d'une source (piloté depuis le panneau d'admin)."""
    set_parametre(_PREFIXE_SOURCE + nom, "1" if actif else "0")


def masquer(valeur: str) -> str:
    """Aperçu masqué d'un secret : ne révèle que les 4 derniers caractères."""
    if not valeur:
        return ""
    if len(valeur) <= 4:
        return "•" * len(valeur)
    return "••••" + valeur[-4:]


def etat_parametre(cle: str) -> dict:
    """Décrit un paramètre pour l'admin, sans jamais divulguer un secret en clair.

    Renvoie la clé, sa source (``base`` / ``env`` / ``absent``), s'il est
    configuré, s'il est secret, et un aperçu (masqué si secret)."""
    override = _override_en_base(cle)
    effective = override or getattr(settings, cle, "")
    secret = cle in CLES_SECRETES
    if not override:
        source = "env" if getattr(settings, cle, "") else "absent"
    else:
        source = "base"
    return {
        "cle": cle,
        "secret": secret,
        "configure": bool(effective),
        "source": source,
        "apercu": masquer(effective) if secret else effective,
    }
