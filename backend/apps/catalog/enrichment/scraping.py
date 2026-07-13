"""Cadre commun aux fournisseurs d'enrichissement par *scraping*.

Phase 5 de la revue d'architecture (cf. docs/architecture-referentiel.md, D7). Le
scraping est le canal de **dernier recours** du référentiel : souvent contraire
aux CGU, fragile (il casse à chaque évolution du front d'un site) et juridiquement
risqué à la redistribution. Aucun site n'est scrapé par défaut.

Ce module ne scrape rien : il fournit une **base** (`ScrapingProvider`) qui impose
les garde-fous exigés avant d'ouvrir un tel canal, pour qu'un fournisseur concret
— s'il cible une source *autorisée* — s'y branche sans les réécrire (ni pouvoir
les contourner) :

- **désactivé par défaut** : double verrou `settings.SCRAPING_ENABLED` + opt-in
  explicite du fournisseur (un ``slug`` non vide) ;
- **respect du robots.txt** du site pour le User-Agent déclaré (abstention si le
  robots.txt est injoignable) ;
- **limitation de débit** : au plus une requête par hôte et par
  ``SCRAPING_MIN_INTERVAL`` secondes (politesse) ;
- **provenance obligatoire** : la source est toujours ``scrape:<slug>``, ce qui
  lui attribue la **confiance la plus basse** — le scraping n'arbitre qu'en
  dernier lors de la consolidation (cf. ingest._confiance_pour) ;
- **jamais d'erreur bloquante** : tout échec (réseau, parsing, blocage robots,
  débit) est un simple *miss*, la cascade continue.

Un fournisseur concret n'implémente que ``url_pour_texte`` (l'URL à requêter) et
``parser_texte`` (extraction → ``NormalizedWine``) ; le cadre se charge du reste.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

from django.conf import settings
from django.core.cache import cache

from .base import EnrichmentProvider, NormalizedWine

logger = logging.getLogger(__name__)

# Durée de mise en cache d'un robots.txt (il bouge lentement).
_ROBOTS_TTL = 60 * 60


class ScrapingProvider(EnrichmentProvider):
    """Base à sous-classer pour un fournisseur par scraping. Inerte telle quelle.

    Sous-classe attendue :
        class MonSiteProvider(ScrapingProvider):
            slug = "monsite"
            def url_pour_texte(self, query): return f"https://monsite.example/?q={query}"
            def parser_texte(self, contenu, query): return NormalizedWine(...) | None
    """

    # Identifiant court du site ; la provenance devient ``scrape:<slug>``. Vide =
    # inerte (base non branchée), garde-fou contre une activation par mégarde.
    slug: str = ""

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"scrape:{self.slug}" if self.slug else "scrape"

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        # Double verrou : réglage global *et* fournisseur explicitement identifié.
        return bool(settings.SCRAPING_ENABLED) and bool(self.slug)

    # --- Points d'extension du fournisseur concret --------------------------

    def url_pour_texte(self, query: str) -> str | None:
        """URL à requêter pour une recherche texte. None => non supporté."""
        return None

    def parser_texte(self, contenu: str, query: str) -> NormalizedWine | None:
        """Extrait un vin normalisé du contenu récupéré. None => rien trouvé."""
        return None

    # --- Contrat EnrichmentProvider (template method, garde-fous imposés) ----

    def lookup_by_text(self, query: str) -> NormalizedWine | None:
        if not self.enabled:
            return None
        return self._recuperer(self.url_pour_texte(query), lambda c: self.parser_texte(c, query))

    # --- Garde-fous (non surchargeables par accident) -----------------------

    def _recuperer(self, url: str | None, parser) -> NormalizedWine | None:
        """Applique robots.txt + débit, récupère l'URL, parse, force la provenance."""
        if not url:
            return None
        if not self._robots_autorise(url):
            logger.info("scrape %s: robots.txt interdit %s", self.slug, url)
            return None
        host = urllib.parse.urlsplit(url).netloc
        if not self._debit_ok(host):
            logger.info("scrape %s: débit dépassé pour %s", self.slug, host)
            return None
        contenu = self._fetch(url)
        if contenu is None:
            return None
        try:
            wine = parser(contenu)
        except Exception as exc:  # parsing fragile : un échec reste un simple miss
            logger.warning("scrape %s: parsing impossible (%s)", self.slug, exc)
            return None
        if wine is not None:
            # Provenance imposée par le cadre (le fournisseur ne peut pas la fausser).
            wine.source = self.name
        return wine

    def _fetch(self, url: str) -> str | None:
        """Récupère le contenu d'une URL. None en cas d'échec (miss silencieux)."""
        req = urllib.request.Request(
            url, headers={"User-Agent": settings.SCRAPING_USER_AGENT}
        )
        try:
            with urllib.request.urlopen(req, timeout=settings.SCRAPING_TIMEOUT) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            logger.warning("scrape %s: %s injoignable (%s)", self.slug, url, exc)
            return None

    def _debit_ok(self, host: str) -> bool:
        """Limiteur de débit : au plus une requête par hôte / intervalle."""
        cle = f"scrape:debit:{self.slug}:{host}"
        if cache.get(cle):
            return False
        cache.set(cle, True, settings.SCRAPING_MIN_INTERVAL)
        return True

    def _robots_autorise(self, url: str) -> bool:
        """Le robots.txt du site autorise-t-il de requêter cette URL ?

        Abstention (``False``) si le respect du robots est activé et que le
        fichier est injoignable — on ne requête pas ce qu'on n'a pas pu vérifier.
        """
        if not settings.SCRAPING_RESPECT_ROBOTS:
            return True
        parts = urllib.parse.urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        texte = self._robots_txt(base)
        if texte is None:  # robots injoignable : prudence
            return False
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(texte.splitlines())
        return parser.can_fetch(settings.SCRAPING_USER_AGENT, url)

    def _robots_txt(self, base: str) -> str | None:
        """Contenu du robots.txt d'un hôte (mis en cache). None si injoignable.

        Un 404 (pas de robots.txt) est traité comme « tout est autorisé » : on
        renvoie une chaîne vide, pas None."""
        cle = f"scrape:robots:{base}"
        cached = cache.get(cle)
        if cached is not None:
            # "\0" est le marqueur « robots injoignable » (distinct d'un fichier vide).
            return None if cached == "\0" else cached
        req = urllib.request.Request(
            base + "/robots.txt", headers={"User-Agent": settings.SCRAPING_USER_AGENT}
        )
        try:
            with urllib.request.urlopen(req, timeout=settings.SCRAPING_TIMEOUT) as resp:
                texte = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            texte = "" if exc.code == 404 else None
        except (urllib.error.URLError, TimeoutError, ValueError):
            texte = None
        # On met en cache l'absence (\0) comme la présence, pour ne pas re-tenter
        # en boucle un hôte injoignable.
        cache.set(cle, texte if texte is not None else "\0", _ROBOTS_TTL)
        return texte
