from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from .. import quotas
from ..runtime_config import source_activee
from .base import EnrichmentProvider, NormalizedWine
from .normalize import clean, guess_couleur, parse_vintage

logger = logging.getLogger(__name__)

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{ean}.json"
# Open Food Facts demande un User-Agent identifiant l'application.
USER_AGENT = "CaveAVin/0.1 (https://github.com/; cave-a-vin)"
TIMEOUT = 4  # secondes — on ne veut pas bloquer le scan si OFF est lent.


class OpenFoodFactsProvider(EnrichmentProvider):
    """
    Open Food Facts est une base *alimentaire* ouverte. Sa couverture des vins
    est réelle mais limitée : bon premier fallback gratuit, sans garantie de hit.
    """

    name = "openfoodfacts"

    @property
    def enabled(self) -> bool:  # type: ignore[override]
        # Actif par défaut, désactivable depuis le panneau d'admin.
        return source_activee("openfoodfacts", True)

    def lookup_by_barcode(self, ean: str) -> NormalizedWine | None:
        req = urllib.request.Request(
            OFF_URL.format(ean=ean), headers={"User-Agent": USER_AGENT}
        )
        quotas.compter(self.name)  # appel réseau réel : décompte l'usage mensuel
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            # Réseau indisponible / réponse illisible : on traite comme un miss,
            # la cascade continue et l'utilisateur n'a pas d'erreur bloquante.
            logger.warning("Open Food Facts injoignable pour %s: %s", ean, exc)
            return None

        if payload.get("status") != 1:
            return None

        product = payload.get("product", {}) or {}
        brands = clean(product.get("brands", ""))
        product_name = clean(
            product.get("product_name_fr") or product.get("product_name") or ""
        )
        categories = clean(product.get("categories", ""))
        labels = clean(product.get("labels", ""))

        domaine_nom = (
            (brands.split(",")[0].strip() if brands else "")
            or product_name
            or "Domaine inconnu"
        )
        cuvee_nom = product_name or brands or "Cuvée inconnue"

        return NormalizedWine(
            domaine_nom=domaine_nom,
            cuvee_nom=cuvee_nom,
            couleur=guess_couleur(product_name, categories, labels),
            # OFF ne distingue pas les millésimes, mais l'année figure souvent dans
            # le nom ("... 2018") : on la propose pour préremplir la saisie.
            millesime=parse_vintage(product_name, product.get("generic_name")),
            code_barres=ean,
            source=self.name,
            reference_externe_id=str(product.get("code") or ean),
            raw={"brands": brands, "categories": categories, "labels": labels},
        )
