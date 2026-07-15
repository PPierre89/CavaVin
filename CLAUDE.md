# CLAUDE.md

Guidance for AI assistants working in this repository. Read this before making changes.

## What this is

**CavaVin** ("Cave à Vin") is a self-hosted wine-cellar manager and sommelier assistant:
a Django REST API + a React SPA, packaged into a **single Docker container** (Django serves
the built SPA via WhiteNoise; SQLite is the only datastore). It is deployed on a home NAS from
a published GHCR image.

The project language is **French**: model fields, function names, comments, commit messages, UI
copy, and docs are all in French. Match this convention — new code, identifiers, and comments
should be written in French, in the same voice as the surrounding code.

`README.md` is the authoritative, detailed product/feature spec (Epics 1–4, endpoint behaviour,
deployment). This file is the orientation layer; when in doubt about *behaviour*, read the README
and the code.

## Repository layout

```
CavaVin/
├── backend/                     # Django 6 + DRF API (Python 3.12)
│   ├── config/                  # settings, urls, auth (RegisterView), wsgi/asgi, index view
│   ├── apps/
│   │   ├── catalog/             # SHARED reference data: Domaine, Cepage, Cuvee
│   │   │   ├── enrichment/      # pluggable providers (OFF, Claude, wineapi.io, GrapeMinds, LWIN/OCR local, Vivino stub)
│   │   │   ├── ingest.py        # shared persistence: upsert_cuvee, enrich_cuvee_from_wineapi
│   │   │   ├── wine_profile.py  # pure mapping of wineapi detail → Cuvee fields (source of truth)
│   │   │   ├── apogee.py        # pure drink-window / status logic (no DB)
│   │   │   ├── sommellerie.py   # pure serving-advice logic derived from wine colour
│   │   │   └── views.py         # Domaine/Cepage/Cuvee viewsets + scan/identify APIViews
│   │   ├── cellars/             # PRIVATE: Cave, Emplacement (self-referencing storage tree)
│   │   └── inventory/           # PRIVATE: Bouteille (stock), MouvementStock, NoteDegustation
│   ├── templates/index.html     # legacy vanilla fallback if SPA build absent
│   ├── .coveragerc              # coverage source/omit/threshold config
│   ├── gunicorn.conf.py         # prod server config (timeout 120 — do not lower)
│   └── requirements.txt
├── frontend/                    # React 19 + Vite 8 + TypeScript + Tailwind v4 SPA
│   ├── src/
│   │   ├── screens/             # screens: Accueil, Cave, MesVins, Ajouter, Carnet, Login
│   │   ├── components/          # FicheVin, FiltresSheet, TastingSheet, VinIdentification, bottle
│   │   ├── api.ts               # JWT fetch client with auto-refresh + pagination helper
│   │   ├── data.tsx             # DataProvider context (caves/emplacements/bouteilles/cuvees)
│   │   ├── auth.tsx             # AuthProvider (JWT in localStorage)
│   │   └── types.ts             # shared TS types mirroring API payloads
│   └── e2e/                     # Playwright end-to-end tests (API mocked via page.route)
├── docker-compose.yml           # pulls ghcr.io/ppierre89/cavavin:latest (no local build)
├── .env.example                 # copy to .env; holds secrets like WINEAPI_KEY
└── .github/workflows/           # ci.yml (tests+coverage, front build, e2e) · docker.yml (image)
```

## Common commands

### Backend (run from `backend/`)
```bash
python manage.py migrate
python manage.py runserver              # http://localhost:8000
python manage.py createsuperuser        # accounts also via /api/auth/register/
python manage.py makemigrations         # after any model change — commit the migration
python manage.py test                   # Django test suite

# Coverage (config from .coveragerc; CI enforces fail_under = 85, actual ~92%)
coverage run manage.py test && coverage report
```

### Frontend (run from `frontend/`)
```bash
npm install
npm run dev            # Vite on :5173, proxies /api /admin /static → Django :8000
npm run lint           # oxlint (must pass in CI)
npm run build          # tsc -b && vite build → dist/  (type-check + build, must pass in CI)
npm run test:e2e       # Playwright; builds+serves SPA, mocks the API (no backend needed)
```

Run **both** servers for local full-stack dev: Django on `:8000` and Vite on `:5173`.

### Docker
```bash
cp .env.example .env && docker compose up   # single all-in-one container on :8000
```

## Architecture & conventions

### Privacy split (RGPD) — enforce this in every change
Data is split into two strict tiers; keep them separate:
- **Shared reference data** (`catalog`: Domaine, Cepage, Cuvee) — world-readable, writable by any
  authenticated user. This is the community-fed "mutualised" catalog. Permission:
  `IsAuthenticatedOrReadOnly`.
- **Private data** (`cellars` + `inventory`: Cave, Emplacement, Bouteille, MouvementStock,
  NoteDegustation) — strictly partitioned per owner. **Every private viewset filters its queryset
  by `request.user`** and returns `.none()` for anonymous users. When adding a private endpoint,
  filter server-side and never let one user see, reference, or place bottles into another user's
  objects.

### API design (DRF)
- Global defaults live in `backend/config/settings.py` (`REST_FRAMEWORK`): PageNumberPagination
  (page size 25), DjangoFilter + Search + Ordering backends, JWT-first + Session auth.
- Endpoints are registered via a `DefaultRouter` in `config/urls.py` plus a few `APIView`s
  (scan-code-barres, identifier-vin, scan-etiquette). Custom actions use `@action(detail=True)`
  (e.g. `consommer`, `fiche`, `rafraichir`).
- Schema/docs: drf-spectacular at `/api/schema/` and Swagger UI at `/api/docs/`.
- Throttling: enrichment endpoints (barcode/text/image identify, refresh) use the `enrichment`
  scope (30/min); auth uses `auth` (10/min). Preserve throttles on quota-consuming endpoints.

### Authentication
Two mechanisms coexist: **JWT** (`/api/auth/token/`, `refresh`, `verify`; 60 min access / 7 day
refresh; used by the SPA via `Authorization: Bearer`) and **Session** (Django admin login, reused
by Swagger's Authorize). Registration: `/api/auth/register/`.

### Pure business logic (keep it DB-free and tested)
Three modules hold the domain rules as pure functions — no DB access, unit-tested directly:
- `apogee.py` — drink window (début/fin) + status (`À garder`/`À boire`/`Dépassé`) from wine colour
  + vintage vs. current year. **Manual entry wins**: if `Bouteille.apogee_debut/fin` are set they
  are respected; otherwise estimated. Status is **computed on read** (`statut_apogee` property),
  not driven by the stored `statut` field.
- `sommellerie.py` — serving advice (temperature, decanting, food pairings) from wine colour.
- `wine_profile.py` (`normalize_detail`) — the **single source of truth** mapping a wineapi.io
  `GET /wines/{id}` payload onto `Cuvee` fields, reused by both the provider and `ingest`.

### External enrichment (pluggable providers)
`backend/apps/catalog/enrichment/` defines `EnrichmentProvider` (abstract) returning
`NormalizedWine`. Providers implement `lookup_by_barcode` / `lookup_by_text` / `lookup_by_image`.
The cascade in `registry.py` tries enabled providers in order:
- **Open Food Facts** — barcodes (US 01).
- **Claude (Anthropic)** — primary for text & label image (US 02/03/04/05): multimodal vision reads
  the label; structured outputs constrain the answer to a JSON schema mirroring the wineapi detail
  format, so persistence (`wine_profile.normalize_detail` + `ingest`) is reused as-is. Volatile data
  it cannot know (prices, community ratings) is deliberately never requested. Auto-disabled when
  `ANTHROPIC_API_KEY` is unset.
- **wineapi.io** — text & label image fallback + merchant data (prices, ratings); auto-disabled when
  `WINEAPI_KEY` is unset.
- **GrapeMinds** (`api.grapeminds.eu`) — extra œnological fallback behind wineapi: text search
  (`/wines/search` → `/wines/{id}`) and Enterprise-only label photo (`/photo/analyze`). **Disabled by
  default even with a key** — needs an explicit `GRAPEMINDS_ENABLED=True`, because (a) GrapeMinds
  requires a paid Persistent Storage License to permanently store its data, which our catalog does, and
  (b) the public tier's quota is tight (~250/month). Response schema is mapped defensively (the vendor
  ships no example payloads) — revalidate `grapeminds.py`'s field-name constants against a real
  `/wines/{id}` response before relying on it. Its payload differs from wineapi's, so it contributes a
  per-canal observation rather than a raw `wineapi_detail`.
- **LWIN + local OCR** — last-resort, 100% free & offline fallback: the `tesseract` binary (installed
  in the Docker image, subprocess call) reads the label, then fuzzy matching (rapidfuzz, precision-first:
  every significant token of a reference must be found, IDF-weighted tie-break) against the LWIN
  (Liv-ex) reference table imported via `manage.py import_lwin`. Never raises `EnrichmentError`;
  inert without the imported dump or the binary. Keeps identification working with zero API keys.
- **Vivino** — permanently disabled stub (no public API; scraping violates ToS — do not implement).
Every hit is normalised and **cached into the local DB** via `ingest.upsert_cuvee`. `EnrichmentError`
(quota 429 / bad key 401) is surfaced to the user; network/other errors are treated as a plain miss.
The **raw** wineapi payload is persisted verbatim on `Cuvee.wineapi_detail` so no upstream field is
ever lost. Quota is protected by caching (registry TTLs) and a per-wine refresh cooldown
(`WINEAPI_REFRESH_COOLDOWN`, default 1h).

### Secrets & config
Secrets come from `.env` (loaded via python-dotenv in `settings.py`); `.env` is gitignored. Never
hardcode keys — `ANTHROPIC_API_KEY`, `WINEAPI_KEY`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`,
etc. are all env-driven.
See `.env.example` for the full list.

### Frontend conventions
- Single-page app, **mobile-first** (Pixel 9), art direction "CavaVin" from the « CavaVin Écrans »
  mockups (dark warm theme, lie-de-vin/gold accents, matte cards, Cormorant Garamond serif +
  Manrope). Tailwind v4 utility classes; custom oklch theme tokens (`bg-wine`, `text-gold`,
  `border-line`, `bg-surface`, etc.) — reuse them rather than raw hex.
- Bottom tab navigation (`App.tsx`): Accueil / Vin / Cave / Carnet. Adding a bottle is not a tab:
  it opens from the Accueil quick action or the "+" button in Mes vins; stock movements (journal)
  live on the Accueil screen.
- All API calls go through `api.ts` (`api()` / `apiAllPages()`), which injects the JWT, auto-refreshes
  on 401, and normalises errors (`ApiError`, `errMsg`). Global state is in `data.tsx`'s `DataProvider`.
- Default add flow is **label photo** (`VinIdentification`); barcode scan & text search are fallbacks.

### Data model summary
- `Domaine` → `Cuvee` (colour, appellation, cépages M2M, + persisted wineapi enrichment columns and
  raw `wineapi_detail`): the shared catalog.
- `Cave` → `Emplacement` (self-referencing tree Armoire/Casier/Clayette/Caisse/Case, grid via
  `nb_colonnes`×`nb_rangees` → `capacite`, `chemin()` for full path): private storage structure.
- `Bouteille`: one stock line (cuvée + millésime + quantité) at an optional emplacement; capacity
  is enforced on placement. `MouvementStock`: entry/exit/consumption history. `NoteDegustation`:
  private tasting journal (multiple entries per cuvée; "Ma note" = most recent).

## Testing & CI

CI runs on every PR to `main` and on `main` (`.github/workflows/ci.yml`); all three jobs must pass:
1. **Tests (Django)** — `coverage run manage.py test`, then `coverage report` enforces the 85%
   threshold from `.coveragerc`. **Add tests for new backend behaviour**; prefer testing pure
   modules (`apogee`, `sommellerie`, `wine_profile`) directly and viewsets via the DRF test client.
2. **Frontend (build)** — `npm ci`, `npm run lint` (oxlint), `npm run build` (tsc + vite).
3. **E2E (Playwright)** — builds & serves the SPA with the **API mocked** (`page.route`); no Django.

### Versioning & release (automated)
Versioning is **fully automated** via `release.yml` (semantic-release) — never bump a version by hand:
- On every push to `main`, commits since the last tag are analysed by **Conventional Commits**:
  `feat` → minor, `fix`/`refactor`/`perf`/`build`/`revert` → patch, `BREAKING CHANGE:`/`type!` →
  major, and `docs`/`test`/`ci`/`chore`/`style` → **no release**.
- A release creates the `vX.Y.Z` tag + GitHub release, then calls `docker.yml` to bake the version
  into the image (`APP_VERSION` build-arg → frontend `__APP_VERSION__` via `src/version.ts`, and
  backend `settings.APP_VERSION` → drf-spectacular schema `VERSION`) and publish
  `ghcr.io/ppierre89/cavavin` with `latest` + `X.Y.Z`/`X.Y`/`X` + `sha-` tags.
- The **git tag is the single source of truth** for the version; `frontend/package.json`'s version is
  only a local-dev fallback. `latest` now tracks the latest **release**, not every push.
- **Write Conventional Commit messages** (in French: `feat(cave): …`, `fix(api): …`) — the repo's
  history already follows this, and the version/publish pipeline depends on it.

## Working agreements
- Keep changes consistent with the existing French naming and comment style.
- Write **Conventional Commit** messages (`type(scope): …`) — they drive automated versioning (above).
- Any model change requires a migration (`makemigrations`) committed alongside it.
- Never lower `gunicorn.conf.py`'s timeout, and keep the outbound-call timeouts (`ANTHROPIC_TIMEOUT`,
  `WINEAPI_TIMEOUT`…) strictly below the worker timeout — slow vision calls will otherwise get
  `WORKER TIMEOUT`-killed instead of returning a clean 404.
- Preserve the privacy split, per-owner queryset filtering, throttles, and quota guards when
  touching API or enrichment code.
