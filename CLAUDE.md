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
│   │   │   ├── enrichment/      # pluggable providers (OFF, Claude, wineapi.io, GrapeMinds, Vinou, LWIN/OCR local, Vivino/CellarTracker stubs)
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
│   │   ├── data.tsx             # DataProvider context (caves/emplacements/bouteilles/rangements)
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
  (b) the public tier's quota is tight (~250/month; 5 req/s, 60 req/min). Schema validated live: name is
  `display_name`, `description`/`tasting_notes` are `{text, text_long}` objects, producer/region are
  nested objects. **Sends an explicit `User-Agent`** — GrapeMinds returns 403 to urllib's default UA.
  Its payload differs from wineapi's, so it contributes a per-canal observation rather than a raw
  `wineapi_detail`.
- **Vinou** (`api.vinou.de`) — supplementary producer catalog (wines registered by Vinou's client
  wineries, mostly German): text search (`POST /wines/search`) and barcode via the `gtin` field.
  POST-JSON routes wrapped in `{"info","data"}`. Auth is **JWT**: `POST /service/login` with
  `VINOU_AUTH_ID` + `VINOU_API_TOKEN` returns a 12 h JWT (cached ~11 h, auto re-login on 401); without
  credentials the `/wines/*` routes still work in **public mode** (leaner payloads). **Disabled by
  default** (`VINOU_ENABLED`) — coverage is niche. `region` and `grapetypeIds` come back as numeric IDs
  (no names without extra lookups), so it maps only the directly usable fields (name, winery, colour,
  country, vintage, barcode, alcohol, description).
- **LWIN + local OCR** — last-resort, 100% free & offline fallback: the `tesseract` binary (installed
  in the Docker image, subprocess call) reads the label, then fuzzy matching (rapidfuzz, precision-first:
  every significant token of a reference must be found, IDF-weighted tie-break) against the LWIN
  (Liv-ex) reference table imported via `manage.py import_lwin`. The OCR locates the label before
  reading it (WineNot-inspired, no neural network): full-photo passes, OSD orientation fix (rotated
  photos without EXIF), then a crop on the TSV word-box region re-read at full resolution (small
  label in frame), all under the `TESSERACT_TIMEOUT` total budget. Never raises `EnrichmentError`;
  inert without the imported dump or the binary. Keeps identification working with zero API keys.
- **Vivino** — permanently disabled stub (no public API; scraping violates ToS — do not implement).
- **CellarTracker** — permanently disabled stub, same rationale: no public reference API; `/wines.asp` is
  an HTML community page and scraping it violates their ToS. Their only official programmatic access
  (`xlquery.asp`) returns the *authenticated user's own* cellar/notes — a personal export, not a
  searchable catalog — so importing one's own CellarTracker cellar would belong to the private tier,
  not this enrichment cascade. Do not implement scraping.
Every hit is normalised and **cached into the local DB** via `ingest.upsert_cuvee`. `EnrichmentError`
(quota 429 / bad key 401) is surfaced to the user; network/other errors are treated as a plain miss.
The **raw** wineapi payload is persisted verbatim on `Cuvee.wineapi_detail` so no upstream field is
ever lost. Quota is protected by caching (registry TTLs) and a per-wine refresh cooldown
(`WINEAPI_REFRESH_COOLDOWN`, default 1h).

**Identity resolution (`upsert_cuvee`) — get this right or you resurrect a 500.** A reading's strong
identities are *all* checked against the catalog before creating anything, in order `code_barres` >
`reference_externe_id` > `lwin_code`. What matters is not each key's strength but what its **absence**
proves: a missing barcode is inconclusive (one wine has several bottlings) so the next key is tried,
whereas a missing external reference or LWIN code is decisive (distinct wine → create). Checking only
the first key present is what made a barcode scan of an already-known wine blow up on the
`unique_cuvee_reference_externe` constraint. Two invariants follow: identities the matched cuvée
*lacks* get backfilled from the reading (so a later scan is a free local hit), and an identity already
claimed by another cuvée is never taken — first claimant keeps it. See
`docs/architecture-referentiel.md` §5.

**Multi-source fusion.** Identification (`scan-code-barres`, `identifier-vin`, `scan-etiquette`) does
**not** stop at the first hit: `views._cascade_multi` queries *every* enabled source and
`ingest.upsert_multi` merges them — the first hit anchors the canonical cuvée, the rest are recorded as
per-canal `SourceObservation`s and `consolidation.consolider` arbitrates each field by channel
confidence (best of each: identity from one, prices from another, description from a third).

Since every enabled source is called anyway, they are queried **in parallel** (thread pool — the wait
is I/O: network, plus the tesseract subprocess), so an identification costs the *slowest* source
rather than the sum. Two invariants the tests pin down: `hits` stays in **cascade order**, not
completion order (the first anchors identity), and the surfaced `EnrichmentError` is the first in that
same order, so it is deterministic. Worker threads must `connection.close()` — Django only reaps the
connection of the request thread — and only there: never in the request thread itself.

**Label thumbnail on the shared catalog.** A successful `scan-etiquette` stores the photo on
`Cuvee.photo_etiquette`, cropped to the label via `enrichment.image.recadrer_etiquette` (one short
tesseract pass reusing `lwin._zone_texte`). Three rules hold it together: it only fills when
**empty** (the catalog is mutualised — a later, blurrier scan must not overwrite everyone's good
photo), any failure is swallowed (a thumbnail is a bonus, never a reason to fail an identification),
and the crop is not merely cosmetic — it strips the kitchen/hands around the bottle before the image
becomes visible to every user. Served by `CuveeViewSet.photo` **by cuvée id, never by path**, so
directory traversal is impossible by construction and `MEDIA_ROOT` need not be published. Files live
next to the SQLite file (same `data/` volume), so the documented "backup = copy the folder" still
holds.

**Label payload.** `enrichment.image.reduire` shrinks the photo **once** before the cascade (longest
side 1568 px, the point past which vision APIs downscale anyway); every remote source shares that
version, cutting a ~9 MB phone photo to well under 1 MB before base64. Providers that need the
original set `image_pleine_resolution = True` — only `lwin` does, because its crop pass re-reads the
text zone at full resolution, which is exactly what rescues a small label in a wide frame.

**Runtime source control (admin, `runtime_config` + `quotas`).** Each provider's `enabled` combines its
prerequisites (key present…) with a runtime toggle `source_activee(name, default_env)` (DB override
`SOURCE_ENABLED_<name>` > `.env`). Every real outbound call increments a monthly counter
(`quotas.compter`, model `AppelSource`); `quotas.reste` skips a source once its **settable monthly cap**
is reached (`Parametre QUOTA_<SOURCE>`; default 250 for GrapeMinds, unlimited otherwise; `0` = unlimited).
Counting/enforcement is best-effort — it never breaks an identification. The staff panel drives all this
via `GET/PUT /api/admin-panel/sources/` (on/off + cap + usage), alongside the existing API-key overrides.

### Measuring recognition quality — use it before touching OCR/matching thresholds
`manage.py evaluer_reconnaissance` (logic in `catalog/evaluation.py`) is how a change to the OCR
pipeline or the LWIN fuzzy matching is *justified* rather than guessed. Two corpora: a manifest of
annotated real label photos (`catalog/evaluation_corpus/etiquettes.json`, images fetched on demand,
gitignored) and a synthetic tier deriving thousands of OCR-noised queries from the imported LWIN
referential (seeded, so reproducible).

It reports three outcomes, and **the split is the point**: `trouve` / `silence` (no match — the user
types it manually, annoying but harmless) / `erreur` (a *different* wine — the costly one: it
contradicts the provider's precision-first stance and pollutes the shared catalog). A single
"success rate" hides the trade that matters, so never collapse them. When tuning `_TOKEN_RATIO`,
`_OCR_CONF_*` or the candidate selection, quote before/after numbers from this command.

Judging is on **wine identity, not row identity** — the LWIN dump holds the same wine under several
codes, so matching a duplicate code is a success, not an error. Getting that wrong makes the error
rate wildly pessimistic.

`manage.py corpus_openfoodfacts` regenerates a photo corpus from Open Food Facts (ODbL — extraction
is explicitly permitted there, unlike retailer sites whose ToS forbid it; the repo already refuses
scraping, see the Vivino/CellarTracker stubs). Each product carries its **barcode**, so ground truth
is unambiguous and needs no manual annotation, and both identification paths can be measured
(`--voie image` / `--voie code-barres`). Two traps the code guards against, keep them guarded:
**never evaluate the `openfoodfacts` source on an OFF-derived corpus** (100 % by construction — the
command errors loudly), and OFF is crowd-sourced so **`en:wines` alone is not enough** — a
clementine jam genuinely carries that tag, hence the incompatible-family exclusion list.

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
  on 401 (a single shared in-flight refresh, storing the rotated refresh token), and normalises errors
  (`ApiError`, `errMsg`). Global state is in `data.tsx`'s `DataProvider`.
- **The SPA never bulk-loads the shared catalog.** `DataProvider` holds private data only; the cuvée
  attributes the stock screens need (colour, appellation, region, country, classification, market
  price) are served on each `Bouteille` payload, and the add-flow autocomplete queries
  `/api/cuvees/?search=` server-side. Keep it that way — the mutualised catalog grows with the
  community, so any client-side copy of it is both wasteful and silently truncated by pagination.
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
