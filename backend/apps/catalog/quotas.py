"""Comptage d'usage et plafonds mensuels des sources d'enrichissement.

Chaque fournisseur incrémente son compteur à chaque appel réseau réel
(``compter``). Le panneau d'admin affiche la consommation du mois et un plafond
mensuel *réglable* : au-delà, la source est ignorée par la cascade
(``reste`` renvoie ``False``), ce qui protège les quotas serrés (ex. GrapeMinds
250/mois). Le plafond est stocké comme un ``Parametre`` (override en base),
avec un défaut par source ; ``0`` ou vide = illimité.

Fonctions volontairement tolérantes : si la base n'est pas prête (migrations),
le comptage et l'enforcement retombent silencieusement en « illimité » pour ne
jamais bloquer une identification.
"""

from __future__ import annotations

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

# Plafond mensuel par défaut d'une source (0 / absent = illimité). Seul GrapeMinds
# a un quota vraiment serré côté fournisseur ; les autres restent libres par défaut.
PLAFONDS_DEFAUT: dict[str, int] = {
    "grapeminds": 250,
}

# Limites de débit connues, purement informatives (affichées telles quelles).
LIMITES_DEBIT: dict[str, str] = {
    "grapeminds": "5 req/s · 60 req/min",
    "openfoodfacts": "usage raisonnable",
}

_PREFIXE_PLAFOND = "QUOTA_"  # clé Parametre : QUOTA_<SOURCE> (ex. QUOTA_GRAPEMINDS)
_TTL_USAGE = 20  # cache court du compteur lu (propage entre workers, évite de marteler)


def _mois_courant() -> str:
    return timezone.now().strftime("%Y-%m")


def cle_plafond(source: str) -> str:
    return _PREFIXE_PLAFOND + source.upper()


def compter(source: str) -> None:
    """Incrémente le compteur mensuel de ``source`` (un appel réseau réel)."""
    mois = _mois_courant()
    from .models import AppelSource

    try:
        AppelSource.objects.get_or_create(source=source, mois=mois)
        AppelSource.objects.filter(source=source, mois=mois).update(nombre=F("nombre") + 1)
    except Exception:
        # Le comptage ne doit JAMAIS casser une identification (base indisponible,
        # migration en cours, contexte sans base…) : on échoue silencieusement.
        return
    cache.delete(f"quota:usage:{source}:{mois}")


def usage_mensuel(source: str) -> int:
    """Nombre d'appels de ``source`` sur le mois courant (0 si aucun)."""
    mois = _mois_courant()
    cle = f"quota:usage:{source}:{mois}"
    cached = cache.get(cle)
    if cached is not None:
        return cached
    from .models import AppelSource

    try:
        obj = AppelSource.objects.filter(source=source, mois=mois).first()
        valeur = obj.nombre if obj else 0
    except Exception:
        # Best-effort : en cas d'indisponibilité base, on ne bloque pas (usage 0).
        return 0
    cache.set(cle, valeur, _TTL_USAGE)
    return valeur


def plafond(source: str) -> int | None:
    """Plafond mensuel effectif : override ``Parametre QUOTA_<SOURCE>`` sinon défaut.

    Renvoie ``None`` quand la source est illimitée (override/défaut à 0 ou absent)."""
    from .runtime_config import _override_en_base

    brut = _override_en_base(cle_plafond(source))
    if brut:
        try:
            valeur = int(brut)
        except (TypeError, ValueError):
            valeur = 0
    else:
        valeur = PLAFONDS_DEFAUT.get(source, 0)
    return valeur if valeur > 0 else None


def reste(source: str) -> bool:
    """Vrai s'il reste du quota mensuel (ou si la source est illimitée)."""
    cap = plafond(source)
    if cap is None:
        return True
    return usage_mensuel(source) < cap


def etat(source: str) -> dict:
    """État de quota d'une source pour le panneau d'admin."""
    cap = plafond(source)
    usage = usage_mensuel(source)
    return {
        "usage_mois": usage,
        "plafond": cap,  # None = illimité
        "epuise": cap is not None and usage >= cap,
        "limite_debit": LIMITES_DEBIT.get(source, ""),
    }
