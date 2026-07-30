"""Catalogue portable : exporter le référentiel partagé, le charger ailleurs.

Une installation neuve démarre avec un catalogue **vide** : l'autocomplétion ne
propose rien et la première identification est forcément un appel externe. Les
commandes d'import (`import_xwines`, `import_vivino`…) le remplissent, mais elles
demandent de récupérer les jeux de données et de payer plusieurs dizaines de
minutes de calcul — sur un NAS, davantage.

Ce module permet de faire ce travail **une fois** et de transporter le résultat :

- ``exporter`` produit un fichier SQLite ne contenant que le **catalogue
  mutualisé** (domaines, cépages, cuvées, observations) ;
- ``charger`` fusionne un tel fichier dans une base existante.

**Le cloisonnement RGPD est la contrainte structurante.** Catalogue partagé et
données privées (caves, bouteilles, carnet) vivent dans le *même* fichier SQLite :
livrer une base toute faite écraserait la cave de son destinataire. L'export
purge donc toute table hors `catalog`, et le chargement n'écrit **que** dans les
tables du catalogue — il ne lit ni ne touche `cellars` et `inventory`.

Le chargement ne court-circuite pas la consolidation : il rejoue les
``SourceObservation`` du fichier sur la base cible, puis consolide. Chaque champ
reste donc arbitré par la politique en vigueur *chez le destinataire*, avec sa
provenance — et non figé par celle de l'expéditeur.
"""

from __future__ import annotations

import datetime as _datetime
import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .consolidation import consolider
from .ingest import _completer_identites, _domaine_pour, resoudre_identite
from .models import Cepage, Cuvee, SourceObservation
from .tabular import ImportFichierError

# Tables du catalogue mutualisé, seules exportées et seules écrites au
# chargement. `Parametre` et `AppelSource` en sont volontairement absents : clés
# d'API et compteurs de quota sont propres à une installation.
_TABLES_CATALOGUE = (
    "catalog_domaine",
    "catalog_cepage",
    "catalog_cuvee",
    "catalog_cuvee_cepages",
    "catalog_sourceobservation",
    "catalog_referencelwin",
    "catalog_millesimereference",
)

_LOT = 500


class CataloguePortableError(ImportFichierError):
    """Fichier de catalogue illisible ou de schéma inattendu."""


@dataclass
class ResultatChargement:
    cuvees_lues: int = 0
    creees: int = 0
    completees: int = 0
    observations: int = 0
    ignorees: int = 0

    def __str__(self) -> str:  # pragma: no cover - confort d'affichage
        return (
            f"{self.cuvees_lues} cuvées lues — {self.creees} créées, "
            f"{self.completees} complétées, {self.observations} observations reprises, "
            f"{self.ignorees} ignorées"
        )


@dataclass
class ResultatExport:
    chemin: str = ""
    cuvees: int = 0
    domaines: int = 0
    observations: int = 0
    octets: int = 0

    def __str__(self) -> str:  # pragma: no cover - confort d'affichage
        mo = self.octets / (1024 * 1024)
        return (
            f"{self.cuvees} cuvées, {self.domaines} domaines, "
            f"{self.observations} observations — {mo:.1f} Mo ({self.chemin})"
        )


def _connexion(chemin: str) -> sqlite3.Connection:
    """Ouvre le fichier en lecture, avec accès par nom de colonne."""
    if not Path(chemin).exists():
        raise CataloguePortableError(f"Fichier introuvable : {chemin}")
    try:
        connexion = sqlite3.connect(f"file:{chemin}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise CataloguePortableError(f"Impossible d'ouvrir {chemin} : {exc}") from exc
    connexion.row_factory = sqlite3.Row
    return connexion


def _verifier_schema(connexion: sqlite3.Connection) -> None:
    tables = {
        ligne["name"]
        for ligne in connexion.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    manquantes = {"catalog_domaine", "catalog_cuvee"} - tables
    if manquantes:
        raise CataloguePortableError(
            "Schéma inattendu : ce fichier ne contient pas de catalogue CavaVin "
            f"(tables manquantes : {', '.join(sorted(manquantes))})."
        )


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def exporter(source: str, destination: str) -> ResultatExport:
    """Copie ``source`` en ne gardant que le catalogue mutualisé.

    Procède par copie du fichier puis **purge** des tables hors catalogue, plutôt
    que par recréation : le schéma et les index restent exactement ceux de
    l'application, donc le fichier produit est lisible par n'importe quelle
    installation à la même migration. Un `VACUUM` final récupère la place.
    """
    if not Path(source).exists():
        raise CataloguePortableError(f"Base source introuvable : {source}")
    shutil.copyfile(source, destination)

    connexion = sqlite3.connect(destination)
    connexion.row_factory = sqlite3.Row
    try:
        tables = [
            ligne["name"]
            for ligne in connexion.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        # Tout ce qui n'est pas le catalogue est vidé : données privées
        # (cellars, inventory), comptes, sessions, journal d'admin, et les
        # réglages propres à l'installation (clés d'API, quotas).
        for table in tables:
            if table in _TABLES_CATALOGUE or table.startswith("sqlite_"):
                continue
            if table == "django_migrations":
                continue  # nécessaire pour que la cible reconnaisse le schéma.
            connexion.execute(f'DELETE FROM "{table}"')  # noqa: S608 - nom issu du schéma
        connexion.commit()
        resultat = ResultatExport(
            chemin=destination,
            cuvees=connexion.execute("SELECT COUNT(*) FROM catalog_cuvee").fetchone()[0],
            domaines=connexion.execute("SELECT COUNT(*) FROM catalog_domaine").fetchone()[0],
            observations=connexion.execute(
                "SELECT COUNT(*) FROM catalog_sourceobservation"
            ).fetchone()[0],
        )
        connexion.execute("VACUUM")
        connexion.commit()
    finally:
        connexion.close()
    resultat.octets = Path(destination).stat().st_size
    return resultat


# --------------------------------------------------------------------------- #
# Chargement
# --------------------------------------------------------------------------- #

def _cepages_par_cuvee(connexion: sqlite3.Connection) -> dict[int, list[str]]:
    """{id de cuvée source: [noms de cépages]} — une seule requête jointe."""
    par_cuvee: dict[int, list[str]] = {}
    try:
        lignes = connexion.execute(
            "SELECT cc.cuvee_id AS cuvee_id, c.nom AS nom "
            "FROM catalog_cuvee_cepages cc JOIN catalog_cepage c ON c.id = cc.cepage_id"
        )
    except sqlite3.Error:
        return par_cuvee
    for ligne in lignes:
        par_cuvee.setdefault(ligne["cuvee_id"], []).append(ligne["nom"])
    return par_cuvee


def _observations_par_cuvee(connexion: sqlite3.Connection) -> dict[int, list[sqlite3.Row]]:
    par_cuvee: dict[int, list[sqlite3.Row]] = {}
    try:
        lignes = connexion.execute(
            "SELECT cuvee_id, canal, releve_le, confiance, payload_brut, champs "
            "FROM catalog_sourceobservation ORDER BY id"
        )
    except sqlite3.Error:
        return par_cuvee
    for ligne in lignes:
        par_cuvee.setdefault(ligne["cuvee_id"], []).append(ligne)
    return par_cuvee


def _sans_auto_now_add():
    """Neutralise temporairement ``auto_now_add`` sur ``releve_le``.

    Une observation porte la date à laquelle **le canal a été lu** ; c'est une
    donnée du relevé, pas de son insertion. Sans cette neutralisation, Django
    réécrirait toutes les dates à l'instant du chargement et le catalogue
    transporté perdrait sa chronologie — or la consolidation arbitre les champs
    de marché à la récence."""
    champ = SourceObservation._meta.get_field("releve_le")

    class _Contexte:
        def __enter__(self):
            champ.auto_now_add = False

        def __exit__(self, *exc):
            champ.auto_now_add = True

    return _Contexte()


def charger(chemin: str, *, limite: int | None = None, progression=None) -> ResultatChargement:
    """Fusionne le catalogue de ``chemin`` dans la base courante.

    Idempotent : les cuvées sont rapprochées par les mêmes clés d'identité que
    l'ingestion (``ingest.resoudre_identite``), et une observation déjà présente
    — même canal, même date de relevé — n'est pas redéposée. Recharger le même
    fichier ne crée donc ni doublon ni relevé fantôme.
    """
    connexion = _connexion(chemin)
    try:
        _verifier_schema(connexion)
        resultat = ResultatChargement()
        cepages = _cepages_par_cuvee(connexion)
        observations = _observations_par_cuvee(connexion)
        domaines = {
            ligne["id"]: ligne
            for ligne in connexion.execute("SELECT id, nom FROM catalog_domaine")
        }
        lignes = list(connexion.execute("SELECT * FROM catalog_cuvee ORDER BY id"))
    finally:
        connexion.close()

    if limite is not None:
        lignes = lignes[:limite]

    for debut in range(0, len(lignes), _LOT):
        with transaction.atomic():
            for ligne in lignes[debut : debut + _LOT]:
                resultat.cuvees_lues += 1
                _charger_cuvee(ligne, domaines, cepages, observations, resultat)
        if progression is not None:
            progression(resultat)
    if progression is not None:
        progression(resultat)
    return resultat


def _charger_cuvee(ligne, domaines, cepages, observations, resultat) -> None:
    """Rapproche une cuvée du fichier de la base cible, puis rejoue ses relevés."""
    source_domaine = domaines.get(ligne["domaine_id"])
    if source_domaine is None or not (source_domaine["nom"] or "").strip():
        resultat.ignorees += 1
        return

    domaine = _domaine_pour(source_domaine["nom"])
    identites = [
        (champ, ligne[champ])
        for champ in ("code_barres", "reference_externe_id", "lwin_code")
        if ligne[champ]
    ]
    cuvee = resoudre_identite(domaine, identites, ligne["nom"])

    if cuvee is None:
        # Même garde-fou qu'à l'ingestion : une identité déjà revendiquée par une
        # autre cuvée n'est pas volée, le premier arrivé la garde.
        libres = {
            champ: valeur
            for champ, valeur in identites
            if not Cuvee.objects.filter(**{champ: valeur}).exists()
        }
        cuvee = Cuvee.objects.create(
            domaine=domaine,
            nom=ligne["nom"],
            couleur=ligne["couleur"] or Cuvee.Couleur.AUTRE,
            appellation=ligne["appellation"] or "",
            code_barres=libres.get("code_barres", ""),
            reference_externe_id=libres.get("reference_externe_id", ""),
            lwin_code=libres.get("lwin_code", ""),
        )
        resultat.creees += 1
    else:
        _completer_identites(cuvee, identites)
        resultat.completees += 1

    noms = cepages.get(ligne["id"], [])
    if noms and cuvee.cepages.count() == 0:
        cuvee.cepages.set([Cepage.objects.get_or_create(nom=n)[0] for n in noms])

    resultat.observations += _rejouer_observations(cuvee, observations.get(ligne["id"], []))
    # La fiche est arbitrée par la politique de *cette* installation, pas figée
    # par celle qui a produit le fichier.
    consolider(cuvee)


def _horodatage(brut) -> object:
    """Date de relevé du fichier -> ``datetime`` conscient du fuseau.

    SQLite ne type pas les dates : elles reviennent en chaîne. Les laisser telles
    quelles avait deux conséquences, l'une visible et l'autre non — Django
    avertissait d'un datetime naïf, et surtout la déduplication au rechargement
    comparait une chaîne à un ``datetime`` et ne coïncidait jamais, si bien qu'un
    second chargement redéposait tous les relevés."""
    if isinstance(brut, str):
        valeur = parse_datetime(brut)
    else:
        valeur = brut
    if valeur is None:
        return timezone.now()
    if timezone.is_naive(valeur):
        valeur = timezone.make_aware(valeur, _datetime.timezone.utc)
    return valeur


def _rejouer_observations(cuvee: Cuvee, relevés) -> int:
    """Dépose les relevés absents de la cuvée cible. Retourne le nombre ajouté."""
    if not relevés:
        return 0

    existants = set(cuvee.observations.values_list("canal", "releve_le"))
    nouvelles = []
    for ligne in relevés:
        releve_le = _horodatage(ligne["releve_le"])
        if (ligne["canal"], releve_le) in existants:
            continue  # rechargement du même fichier : rien à redéposer.
        nouvelles.append(
            SourceObservation(
                cuvee=cuvee,
                canal=ligne["canal"],
                releve_le=releve_le,
                confiance=ligne["confiance"],
                payload_brut=json.loads(ligne["payload_brut"] or "{}"),
                champs=json.loads(ligne["champs"] or "{}"),
            )
        )
    if not nouvelles:
        return 0
    with _sans_auto_now_add():
        SourceObservation.objects.bulk_create(nouvelles)
    return len(nouvelles)
