# CavaVin 🍷

**Cave à vin & assistant sommelier auto-hébergé.** Gérez votre cave, identifiez vos bouteilles
d'une photo d'étiquette, suivez leur fenêtre de dégustation et tenez votre carnet — le tout dans
un **conteneur Docker unique** (Django REST + SPA React, SQLite).

<p align="center">
  <img src="docs/images/accueil.png"  alt="Accueil"   width="23%">
  <img src="docs/images/mes-vins.png" alt="Mes vins"  width="23%">
  <img src="docs/images/fiche-vin.png" alt="Fiche vin" width="23%">
  <img src="docs/images/cave.png"     alt="Cave"       width="23%">
</p>

## Fonctionnalités

- **Ajout par photo d'étiquette** (méthode par défaut), code-barres ou recherche par nom —
  identification en cascade : base locale → Open Food Facts / wineapi.io, avec mise en cache.
- **Fiche vin enrichie** : appellation, cépages, conseil de service (température, carafage),
  profil gustatif, accords mets-vins, prix marché et **historique de prix**.
- **Fenêtre de dégustation** calculée (à garder / à boire / dépassé) qui pilote un code couleur.
- **Cave visuelle** : emplacements en arborescence (armoire → casier → clayette), placement des
  bouteilles case par case en glisser-déposer, jauge de remplissage.
- **Carnet de dégustation** privé (note /5, commentaire, profil).
- **SPA mobile-first** (thème sombre lie-de-vin/or), authentification JWT.

## Stack

Django 6 + DRF · React 19 + Vite + Tailwind v4 · SQLite · Docker (mono-conteneur, Django sert le
SPA via WhiteNoise). Docs API : **Swagger** sur `/api/docs/`.

## Démarrage rapide (Docker)

```bash
cp .env.example .env      # renseignez au moins DJANGO_SECRET_KEY (et WINEAPI_KEY si dispo)
docker compose up         # tire ghcr.io/ppierre89/cavavin:latest — aucun build local
```

L'app est sur http://localhost:8000/. Le conteneur applique les migrations et sert les statiques
tout seul. Créez un compte : `docker compose exec app python manage.py createsuperuser` (ou via
l'écran d'inscription).

## Développement local

```bash
# Backend — Django sur :8000
cd backend && pip install -r requirements.txt
python manage.py migrate && python manage.py runserver

# Frontend — Vite sur :5173 (proxifie /api vers Django)
cd frontend && npm install && npm run dev
```

En production, un `Dockerfile` multi-stage build le SPA puis le fait servir par Django.
Voir [`frontend/README.md`](frontend/README.md) pour le détail du front.

## Architecture

```
CavaVin/
├── backend/
│   ├── config/            # settings, urls, auth JWT
│   └── apps/
│       ├── catalog/       # Domaine, Cepage, Cuvee — référentiel partagé
│       │   ├── enrichment/  # providers enfichables (Open Food Facts, wineapi.io)
│       │   ├── apogee.py     # fenêtre/statut de dégustation (logique pure)
│       │   └── sommellerie.py # conseil de service (logique pure)
│       ├── cellars/       # Cave, Emplacement — structure physique (privé)
│       └── inventory/     # Bouteille, MouvementStock, NoteDegustation (privé)
├── frontend/              # SPA React + Vite + Tailwind
├── docker-compose.yml     # app tout-en-un (Django/gunicorn + SQLite)
└── .env.example
```

**Confidentialité (RGPD)** — les données sont cloisonnées en deux niveaux : un **référentiel vin
partagé** (catalogue mutualisé, lisible par tous) et des **données privées** (caves, bouteilles,
carnet) strictement filtrées par propriétaire côté serveur.

## API — points clés

| Endpoint | Rôle |
| --- | --- |
| `POST /api/auth/register/` · `token/` | Inscription / JWT (`Bearer`) |
| `POST /api/scan-etiquette/` · `scan-code-barres/` · `identifier-vin/` | Identification (photo / EAN / texte) |
| `GET /api/cuvees/{id}/fiche/` | Fiche vin consolidée (référentiel + conseil + enrichissement) |
| `POST /api/cuvees/{id}/rafraichir/` | Re-synchro wineapi (cooldown anti-quota) |
| `POST /api/bouteilles/{id}/consommer/` | Sortie de stock atomique + journal |
| `/api/notes-degustation/` | Carnet de dégustation (privé) |

L'enrichissement externe (wineapi.io) est **mis en cache en base** et protégé par un throttle et un
cooldown par vin ; la clé se met dans `.env` (`WINEAPI_KEY`). Détail complet dans Swagger.

## CI/CD

Trois workflows GitHub Actions sur chaque PR vers `main` et sur `main` :

- **`ci.yml`** — tests Django + couverture (seuil 85 %, ~92 % actuel), lint + build front, E2E
  Playwright (API mockée).
- **`release.yml`** — [semantic-release](https://semantic-release.gitbook.io/) : la version découle
  des **commits conventionnels** (`feat` → mineur, `fix`/`refactor` → patch, `BREAKING CHANGE` →
  majeur). Aucun numéro à gérer à la main.
- **`docker.yml`** — publie `ghcr.io/ppierre89/cavavin` (`latest` + tags semver) à chaque release.

## Déploiement NAS

L'image est publiée automatiquement ; le NAS n'a besoin que de `docker-compose.yml` + `.env`.

1. Dans `.env` : `DJANGO_SECRET_KEY` (valeur forte), `DJANGO_ALLOWED_HOSTS` (IP/nom du NAS),
   `WINEAPI_KEY` (optionnel).
2. `docker compose up -d` puis `docker compose exec app python manage.py createsuperuser`.
3. Accès : `http://<ip-du-nas>:8000/`. La base SQLite persiste dans le volume Docker `data`.

**Mises à jour** : `docker compose pull && docker compose up -d`.

> Sert du HTTP simple (accès LAN). Pour une exposition Internet, terminez le TLS via un reverse
> proxy et activez `SECURE_SSL_REDIRECT` / `*_COOKIE_SECURE` dans `settings.py`.

## Roadmap

- Recommandation mets-vins enrichie (LLM) et apogée affinée par cépage / qualité du millésime.
- Valorisation financière temps réel · partage de cave en lecture seule · mode hors-ligne.
