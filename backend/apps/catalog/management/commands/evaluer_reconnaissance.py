"""Mesure le taux de reconnaissance d'un vin, pour pouvoir régler sans deviner."""

from __future__ import annotations

import time
import urllib.request
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.catalog import evaluation
from apps.catalog.enrichment import get_all_providers
from apps.catalog.models import ReferenceLwin

# Corpus de photos réelles livré avec le projet (annotations maison).
CORPUS_DEFAUT = Path(__file__).resolve().parents[2] / "evaluation_corpus" / "etiquettes.json"


class Command(BaseCommand):
    help = (
        "Évalue la reconnaissance de vin sur un corpus de photos annotées et/ou "
        "sur des requêtes synthétiques dérivées du référentiel LWIN."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--corpus", default=None,
            help=f"Manifeste JSON de photos annotées (défaut : {CORPUS_DEFAUT.name} du projet).",
        )
        parser.add_argument(
            "--synthetique", type=int, default=0, metavar="N",
            help="Évalue N requêtes générées depuis le référentiel LWIN (bruit d'OCR simulé).",
        )
        parser.add_argument(
            "--intensite", type=float, default=0.3,
            help="Dose du bruit synthétique, 0 (texte propre) à 1 (très dégradé). Défaut 0.3.",
        )
        parser.add_argument("--graine", type=int, default=0, help="Graine du tirage synthétique.")
        parser.add_argument(
            "--sources", default="lwin",
            help="Sources à évaluer, séparées par des virgules (défaut : lwin, la seule "
                 "gratuite et hors-ligne). « toutes » consomme les quotas des API.",
        )
        parser.add_argument(
            "--voie", choices=["image", "code-barres"], default="image",
            help="Chemin d'identification évalué sur un corpus de photos : lecture de "
                 "l'étiquette (défaut) ou scan du code-barres, quand le corpus en porte.",
        )
        parser.add_argument(
            "--details", action="store_true",
            help="Détaille chaque cas (utile pour comprendre un silence ou une erreur).",
        )

    # ------------------------------------------------------------------ #

    def handle(self, *args, **options):
        noms = [n.strip() for n in options["sources"].split(",") if n.strip()]
        providers = self._providers(noms)
        if not providers:
            raise CommandError(f"Aucune source utilisable parmi : {options['sources']}")

        # --synthetique seul  -> synthétique seul
        # --corpus seul       -> photos seules
        # les deux            -> les deux
        # aucun des deux      -> photos (le corpus livré avec le projet)
        if options["synthetique"]:
            self._evaluer_synthetique(providers, options)
        if options["corpus"] or not options["synthetique"]:
            chemin = Path(options["corpus"]) if options["corpus"] else CORPUS_DEFAUT
            self._evaluer_corpus(providers, chemin, options)

    def _providers(self, noms):
        """Sources demandées, activées ou non — on évalue ce qu'on nous désigne."""
        tous = {p.name: p for p in get_all_providers()}
        if noms == ["toutes"]:
            return [p for p in tous.values() if p.enabled]
        manquants = [n for n in noms if n not in tous]
        if manquants:
            raise CommandError(f"Source(s) inconnue(s) : {', '.join(manquants)}")
        return [tous[n] for n in noms]

    # ------------------------------------------------------------------ #

    def _evaluer_synthetique(self, providers, options):
        total_refs = ReferenceLwin.objects.count()
        if not total_refs:
            self.stderr.write(self.style.WARNING(
                "Référentiel LWIN vide : importe le dump (manage.py import_lwin) pour "
                "l'évaluation synthétique. Étape ignorée."
            ))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n== Synthétique — {options['synthetique']} requêtes, bruit {options['intensite']}, "
            f"référentiel de {total_refs} entrées =="
        ))
        # Échantillon borné : inutile de matérialiser 200 000 lignes pour en tirer N.
        echantillon = list(
            ReferenceLwin.objects.order_by("?")[: max(options["synthetique"] * 3, 500)]
        )
        cas = evaluation.generer_cas_synthetiques(
            echantillon, options["synthetique"],
            graine=options["graine"], intensite=options["intensite"],
        )
        for provider in providers:
            self._executer(provider, cas, options, voie="texte")

    def _evaluer_corpus(self, providers, chemin: Path, options):
        if not chemin.exists():
            raise CommandError(f"Corpus introuvable : {chemin}")
        cas, meta = evaluation.charger_corpus(chemin)
        if not cas:
            raise CommandError(f"Corpus vide : {chemin}")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n== Photos réelles — {len(cas)} étiquettes ({meta.get('nom') or chemin.name}) =="
        ))
        if meta.get("attribution"):
            self.stdout.write(self.style.HTTP_INFO(f"   {meta['attribution']}"))
        self._avertir_circularite(providers, meta)

        voie = options["voie"]
        if voie == "code-barres":
            cas = [c for c in cas if c.code_barres]
            if not cas:
                raise CommandError(
                    "Ce corpus ne porte aucun code-barres : utilise --voie image, ou "
                    "génère un corpus Open Food Facts (manage.py corpus_openfoodfacts)."
                )
        else:
            manquantes = self._telecharger(cas, chemin)
            if manquantes:
                self.stderr.write(self.style.WARNING(
                    f"{manquantes} image(s) non téléchargée(s) : cas ignorés."
                ))
            cas = [c for c in cas if c.image and c.image.exists()]
            if not cas:
                raise CommandError("Aucune image disponible : vérifie l'accès réseau.")

        self._avertir_referentiel_vide(providers)
        for provider in providers:
            self._executer(provider, cas, options, voie=voie)

    def _avertir_circularite(self, providers, meta):
        """Refuse de laisser passer une mesure circulaire sans le dire.

        Évaluer une source sur un corpus qu'elle a elle-même produit donne 100 %
        par construction. Le chiffre est flatteur, ne mesure rien, et c'est
        exactement le genre de résultat qu'on cite ensuite de bonne foi.
        """
        origine = (meta.get("source") or "").lower()
        for provider in providers:
            if provider.name == "openfoodfacts" and "openfoodfacts" in origine:
                self.stderr.write(self.style.ERROR(
                    "   MESURE CIRCULAIRE : la source « openfoodfacts » est évaluée sur "
                    "un corpus issu d'Open Food Facts. Le score sera de ~100 % par "
                    "construction et ne mesure rien.\n"
                    "   Évalue plutôt les autres sources (--sources lwin, claude…), pour "
                    "lesquelles ce corpus est un arbitre indépendant."
                ))

    def _avertir_referentiel_vide(self, providers):
        """Un référentiel vide fait tout échouer en silence : autant le dire.

        Sans cet avertissement, un « 0 % reconnu » se lit comme une régression de
        l'OCR alors que la correspondance n'a simplement rien contre quoi jouer.
        """
        if not any(p.name == "lwin" for p in providers):
            return
        try:
            if ReferenceLwin.objects.exists():
                return
            detail = "Référentiel LWIN VIDE"
        except Exception:
            # Base non migrée : c'est précisément le cas où l'utilisateur a besoin
            # d'un message, pas d'un traceback — la fonction est là pour aider.
            detail = "Référentiel LWIN INACCESSIBLE (base non migrée ?)"
        self.stderr.write(self.style.ERROR(
            f"   {detail} : la source « lwin » ne peut rien reconnaître, "
            "tous les cas tomberont en silence.\n"
            "   Lance `manage.py migrate` puis importe le dump Liv-ex "
            "(`manage.py import_lwin`) avant d'interpréter ces chiffres."
        ))

    def _telecharger(self, cas, chemin_manifeste: Path) -> int:
        """Récupère les images absentes du cache local (non versionnées)."""
        import json

        urls = {
            str(e.get("id")): e.get("url")
            for e in json.loads(chemin_manifeste.read_text(encoding="utf-8")).get("cas", [])
        }
        manquantes = 0
        for c in cas:
            if not c.image or c.image.exists():
                continue
            url = urls.get(c.identifiant)
            if not url:
                manquantes += 1
                continue
            c.image.parent.mkdir(parents=True, exist_ok=True)
            try:
                with urllib.request.urlopen(url, timeout=30) as reponse:
                    c.image.write_bytes(reponse.read())
                self.stdout.write(f"   téléchargé {c.identifiant}")
            except Exception as exc:  # réseau coupé, URL morte…
                self.stderr.write(self.style.WARNING(f"   {c.identifiant} : {exc}"))
                manquantes += 1
        return manquantes

    # ------------------------------------------------------------------ #

    def _executer(self, provider, cas, options, voie: str = "texte"):
        bilan = evaluation.Bilan()
        debut = time.monotonic()
        for c in cas:
            try:
                if voie == "image":
                    wine = provider.lookup_by_image(c.image.read_bytes(), "image/jpeg")
                elif voie == "code-barres":
                    wine = provider.lookup_by_barcode(c.code_barres)
                else:
                    wine = provider.lookup_by_text(c.texte)
            except Exception as exc:  # une source en panne ne fausse pas le reste
                self.stderr.write(self.style.WARNING(f"   {c.identifiant} : {exc}"))
                wine = None
            bilan.resultats.append(evaluation.juger(c, wine))
        duree = time.monotonic() - debut

        par_cas = duree / len(cas) if cas else 0
        self.stdout.write(
            f"\n  {self.style.SQL_FIELD(provider.name)} — {bilan.resume()}"
            f"  [{duree:.1f} s, {par_cas:.2f} s/cas]"
        )
        # L'erreur est l'issue coûteuse : elle pollue le catalogue mutualisé.
        if bilan.erreurs:
            self.stdout.write(self.style.ERROR(
                f"    {bilan.erreurs} erreur(s) — un autre vin a été renvoyé"
            ))
        if options["details"]:
            for r in bilan.resultats:
                if r.issue == "trouve":
                    continue
                attendu = f"{r.cas.producteur} {r.cas.vin}".strip()
                obtenu = r.obtenu or "—"
                style = self.style.ERROR if r.issue == "erreur" else self.style.WARNING
                self.stdout.write(style(
                    f"    {r.issue:8s} {r.cas.identifiant:24s} attendu « {attendu} » "
                    f"· obtenu « {obtenu} »"
                ))
