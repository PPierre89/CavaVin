"""Relie le catalogue au référentiel LWIN : code LWIN, appellation, classification.

Réconciliation **hors ligne** de deux tables qui ne se parlaient pas :
`ReferenceLwin` (identités Liv-ex, qui portent la sous-région — donc
l'appellation) et `Cuvee` (le catalogue, garni notamment par `import_xwines`,
qui porte cépages, accords et profil). Aucun appel réseau, aucun quota.

Effet visible : `GET /api/recherche-vins/` (l'autocomplétion de l'écran d'ajout)
cherche dans LWIN et enrichit ses suggestions en joignant `Cuvee.lwin_code`. Une
cuvée appariée y remonte donc désormais ses cépages, sa note et ses accords.

Prérequis : avoir importé le dump LWIN (`manage.py import_lwin`).

Usage :
    python manage.py apparier_lwin --simuler        # inspecter, rien n'est écrit
    python manage.py apparier_lwin
    python manage.py apparier_lwin --seuil 0.90     # plus prudent encore
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.catalog.appariement import SEUIL_DEFAUT, SEUIL_PRODUCTEUR, apparier_lwin
from apps.catalog.models import Cuvee, ReferenceLwin


class Command(BaseCommand):
    help = (
        "Apparie les cuvées sans code LWIN au référentiel Liv-ex importé "
        "(pose lwin_code, appellation et classification). Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--seuil", type=float, default=SEUIL_DEFAUT,
            help=(
                f"Similarité minimale des noms de vin, 0 à 1 (défaut : {SEUIL_DEFAUT}). "
                "Plus haut = moins d'appariements, mais plus sûrs."
            ),
        )
        parser.add_argument(
            "--seuil-producteur", type=int, default=SEUIL_PRODUCTEUR,
            help=(
                f"Similarité minimale des noms de producteurs, 0 à 100 "
                f"(défaut : {SEUIL_PRODUCTEUR}). Ce seuil dépend de la façon dont "
                "votre dump nomme les domaines (« Aurora » vs « Vinicola Aurora ») : "
                "calibrez-le avec --simuler avant d'écrire."
            ),
        )
        parser.add_argument(
            "--limite", type=int, default=None,
            help="N'examiner que les N premières cuvées (essai).",
        )
        parser.add_argument(
            "--simuler", action="store_true",
            help="N'écrire dans le catalogue partagé, afficher seulement le bilan.",
        )

    def handle(self, *args, seuil, seuil_producteur, limite, simuler, **options):
        if not 0 < seuil <= 1:
            raise CommandError("--seuil doit être compris entre 0 (exclu) et 1.")
        if not 0 < seuil_producteur <= 100:
            raise CommandError("--seuil-producteur doit être compris entre 0 (exclu) et 100.")
        if not ReferenceLwin.objects.exists():
            raise CommandError(
                "Référentiel LWIN vide : importez-le d'abord "
                "(python manage.py import_lwin /chemin/LWINdatabase.xlsx)."
            )

        def progression(resultat):
            self.stdout.write(f"  … {resultat}", ending="\r")

        resultat = apparier_lwin(
            seuil=seuil,
            seuil_producteur=seuil_producteur,
            limite=limite,
            simuler=simuler,
            progression=progression,
        )

        self.stdout.write("")
        style = self.style.WARNING if simuler else self.style.SUCCESS
        self.stdout.write(style(("[simulation] " if simuler else "") + str(resultat)))
        if resultat.examinees:
            taux = 100 * resultat.appariees / resultat.examinees
            self.stdout.write(f"Taux d'appariement : {taux:.1f} %")
        self.stdout.write(
            f"Catalogue : {Cuvee.objects.exclude(lwin_code='').count()} cuvées avec code LWIN, "
            f"{Cuvee.objects.exclude(appellation='').count()} avec appellation "
            f"(sur {Cuvee.objects.count()})."
        )
