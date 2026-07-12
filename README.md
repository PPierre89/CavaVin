# Cave à Vin — API

Assistant sommelier & gestionnaire de cave. Fondation de l'architecture orientée API décrite
dans la spec produit (Epics 1 à 4), construite avec **Django + Django REST Framework**,
prête à conteneuriser avec **Docker**.

## Ce qui est implémenté aujourd'hui

Cette première itération couvre le socle de données et l'API REST pour :

- **Epic 2 (Modélisation & gestion visuelle)** : `Cave`, `Emplacement` (arborescence auto-référencée
  Armoire > Casier > Clayette > Case, avec position ligne/colonne pour un futur éditeur drag & drop,
  et calcul d'occupation en temps réel).
- **Epic 1 (Acquisition, sans la partie IA/OCR)** : saisie manuelle rapide d'un `Domaine`, d'une `Cuvee`
  (cépages, appellation, couleur, code-barres) et ajout "en vrac" de bouteilles (`Bouteille`, quantité,
  emplacement optionnel si non encore placé).
- **Epic 3/4 (fenêtre de dégustation)** : calcul de la **fenêtre d'apogée** et du **statut**
  (à garder / à boire / dépassé) qui pilote le code couleur — voir la section dédiée ci-dessous — et
  `MouvementStock` pour l'historique des sorties/consommations ("bouteilles mortes").

L'action `POST /api/bouteilles/{id}/consommer/` retire N bouteilles du stock et journalise le
mouvement de façon atomique.

### Scan de code-barres (US 01)

`POST /api/scan-code-barres/` avec `{"code_barres": "<EAN 8-14 chiffres>"}` résout une bouteille
en cascade :

1. **Base locale** (`Cuvee.code_barres`) — si trouvée, réponse `source: "local"`, **aucun appel
   réseau externe**.
2. **Fournisseurs d'enrichissement activés** — cascade des providers ; le premier hit est normalisé,
   **mis en cache en base locale**, puis renvoyé (`source: "openfoodfacts"`, `created: true`).
3. **Échec total** — `404 { "detail": "Vin non reconnu par son code-barres" }` ; côté mobile, l'app
   propose alors le scan d'étiquette (US 03, ci-dessous).

Côté mobile, le scan de code-barres est proposé en **repli** (dépliant « Autre méthode ») : le bouton
**📷 Scanner le code-barres** utilise l'API native `BarcodeDetector`
(caméra arrière) quand elle est disponible, et bascule sinon sur une **saisie manuelle**. ⚠️ La caméra
exige un contexte sécurisé : elle fonctionne sur `http://localhost` mais nécessite **HTTPS** sur un
vrai téléphone via IP LAN.

### Scan d'étiquette (US 02/03)

`POST /api/scan-etiquette/` avec un upload multipart (champ `image`, JPEG/PNG ≤ 10 Mo) — identifie
le vin à partir d'une **photo de l'étiquette** via la cascade **Claude (vision, Anthropic)** →
**wineapi.io** → **OCR local + LWIN** (repli gratuit, voir plus bas). Claude lit l'étiquette avec un modèle multimodal et
renvoie directement une fiche complète (domaine, cuvée, appellation, millésime, cépages, corps,
acidité, description, accords) — bien plus fiable que l'identification wineapi, souvent incorrecte ou
lacunaire. Le hit est normalisé et **mis en cache local** comme pour le texte, et la réponse a la même
forme que `/api/identifier-vin/` (`confidence`, `infos`, `suggestions`). Échec →
`404 "Vin non identifié sur l'étiquette"`.

Côté mobile, c'est la **méthode d'ajout par défaut** : le bouton principal **🏷️ Photographier
l'étiquette** ouvre directement la caméra arrière (`<input type="file" capture="environment">` —
fonctionne partout, pas d'API caméra requise) et pré-remplit le formulaire d'ajout, exactement comme
le scan de code-barres. Un second bouton **🖼️ Importer une photo** pioche dans la galerie (même
input, sans `capture`). Le scan de code-barres et la recherche par nom deviennent des **méthodes de
repli**, regroupées sous un dépliant discret « Autre méthode : nom ou code-barres » afin de garder
l'écran centré sur la photo.

### Identification par texte (US 04)

`POST /api/identifier-vin/` avec `{"query": "Chateau Petrus 2015"}` — pensé pour la sortie OCR
(US 03) comme pour une saisie manuelle. Cascade : **base locale** (nom, millésime retiré) → **Claude
(Anthropic)** → **wineapi.io** (`POST /identify/text` puis `GET /wines/{id}` pour enrichir) →
**référentiel LWIN local** (repli gratuit) → `404 "Vin non identifié"`. Le hit est normalisé et
**mis en cache local** (domaine + cuvée + **cépages**), et la réponse renvoie `confidence`, `infos`
(région, description…) et `suggestions`.

**Claude en tête de cascade** : le provider `claude.py` interroge un modèle multimodal (défaut
`claude-opus-4-8`) dont la sortie est **contrainte par un schéma JSON** (structured outputs) au format
du détail wineapi — le pipeline de persistance (`wine_profile.normalize_detail`, `ingest.upsert_cuvee`)
est donc réutilisé tel quel. Le modèle complète la fiche à partir de ses connaissances œnologiques
(couleur, appellation, **cépages**, corps, acidité, degré, description, accords) — ce qui **couvre le
besoin de l'US 05** (remplissage des trous de données) **sans recourir au scraping Vivino**. Les
données volatiles qu'il ne peut pas connaître (prix marchands, notes communautaires, avis critiques)
ne lui sont volontairement **pas demandées** — pas de données inventées ; wineapi, resté dans la
cascade, peut les apporter.

Les erreurs documentées des API sont gérées proprement : `429` (quota) et `401` (clé invalide) sont
remontés via `EnrichmentError` avec un message clair (pas déguisés en « vin non trouvé ») ; réseau
indisponible ou 4xx/5xx divers = simple *miss* (la cascade passe au provider suivant).

**Interface d'enrichissement enfichable** (`backend/apps/catalog/enrichment/`) : chaque source implémente
`lookup_by_barcode(ean)`, `lookup_by_text(query)` et/ou `lookup_by_image(data, content_type)` renvoyant
un `NormalizedWine`. Actifs : **Open Food Facts** (code-barres), **Claude** (texte + étiquette),
**wineapi.io** (texte + étiquette, en repli) et **LWIN + OCR local** (dernier repli, gratuit).
`Vivino` reste un slot désactivé (pas d'API publique, scraping = violation des CGU). La persistance
est mutualisée (`ingest.upsert_cuvee`).

**Config** : les clés se mettent dans `.env` (`ANTHROPIC_API_KEY=...`, `WINEAPI_KEY=...`, voir
`.env.example`) — jamais dans le code. Sans clé, chaque provider se désactive tout seul. Le fichier
`.env` est ignoré par git.

### Repli 100 % gratuit et hors-ligne : OCR local + référentiel LWIN

Sans aucune clé d'API, l'identification reste fonctionnelle grâce au dernier maillon de la cascade,
le provider `lwin` :

- **OCR local** : la photo d'étiquette est lue par le binaire **Tesseract** (installé dans l'image
  Docker avec le pack français, appelé en sous-processus — aucune dépendance Python). Sans binaire,
  le provider est simplement inerte.
- **Référentiel LWIN** (Liv-ex Wine Identifiers, ~100 000 identités de vins : producteur, vin,
  région, pays, couleur, classification) : dump **gratuit** téléchargeable après inscription sur
  <https://www.liv-ex.com/lwin/>, importé en base via :
  ```bash
  docker compose exec app python manage.py import_lwin /chemin/LWINdatabase.csv
  ```
  L'import est idempotent (ré-exécutable après chaque mise à jour du dump). Le code LWIN est
  persisté sur la cuvée (`lwin_code`).
- **Correspondance floue** (rapidfuzz), orientée *précision* : tous les tokens significatifs d'une
  référence doivent être retrouvés dans la sortie OCR / la saisie (tolérance aux coquilles d'OCR),
  avec pondération par rareté (IDF) pour départager les étiquettes qui mentionnent plusieurs noms
  (ex. un Château Palmer mentionne aussi sa commune, Margaux). Un doute = un *miss*, plutôt qu'un
  mauvais vin injecté dans le catalogue partagé.

La qualité d'identification est inférieure à Claude (polices stylisées, reflets, étiquettes
courbes…), et la fiche est plus sobre (pas de cépages/description ; corps, accords et conseils
retombent sur `sommellerie.py`, dérivés de la couleur) — mais c'est **0 € et 100 % local**.
Réglages : `LWIN_ENABLED`, `TESSERACT_CMD`, `TESSERACT_TIMEOUT` (voir `.env.example`).

### Fiche vin consolidée

`GET /api/cuvees/{id}/fiche/` renvoie en une requête tout ce dont la fiche vin du frontend a besoin :

- **Référentiel partagé** : nom, appellation, couleur, domaine et **cépages** de la cuvée.
- **Conseil de dégustation** dérivé de la couleur (`backend/apps/catalog/sommellerie.py`, logique
  *pure* et testée) : température de service, carafage, profil gustatif type et accords mets-vins.
- **Enrichissement wineapi.io persisté** (si le vin a été identifié — `Cuvee.reference_externe_id`) :
  le détail `GET /wines/{id}` est **enregistré en base sur la cuvée** (via
  `ingest.enrich_cuvee_from_wineapi`, mapping pur et testé dans `wine_profile.normalize_detail` —
  **source de vérité unique** réutilisée par le provider `wineapi.py`, plus de double extraction).
  **Toutes les informations remontées par wineapi.io** y sont mappées :
  région/pays, appellation, classification, description & élaboration, corps/acidité, degré d'alcool,
  image, code LWIN, cépages, **note & nombre d'avis communautaires**, **avis de critiques**
  (score, texte, date), **accords mets-vins notés** (aliment + confiance), **fourchette de prix
  marché** et **prix par marchand** (`prices` : caviste, tarif, devise, lien, **date de relevé**
  `fetchedAt` affichée « relevé le… »). La date de relevé alimente aussi un **historique de prix**
  (`Cuvee.historique_prix`) : les offres sont regroupées **par jour de relevé** (min/max) et
  **accumulées au fil des synchros**, ce qui construit une série temporelle affichée en **graphe**
  sur la fiche (section « Historique de prix »). Au-delà de ces champs
  mappés en colonnes, la **réponse brute complète** du dernier `GET /wines/{id}` est aussi conservée
  telle quelle (`Cuvee.wineapi_detail`), pour ne **jamais perdre une information remontée** — même non
  encore exploitée ou ajoutée plus tard par l'API — et pouvoir re-dériver les champs sans re-consommer
  le quota. L'enrichissement a lieu
  **à l'identification** (texte/image) et à la **synchro manuelle** (bouton 🔄). La fiche **lit alors
  la base, sans appel réseau** — ce qui préserve le quota wineapi. Un ré-appel (« actualiser »)
  **met à jour** les données (prix, scores…) et rafraîchit le snapshot brut. Un vin importé avant cette
  persistance est enrichi **paresseusement au premier accès** à sa fiche (une seule fois). Les champs
  absents retombent sur le conseil couleur. Quand wineapi signale un détail encore incomplet
  (`X-Update-Status: pending` / `pendingEnrichment`, enrichissement asynchrone en cours), il **n'est
  pas figé** : le cache est court et la fiche re-fetch un détail complet plus tard.
- **Données privées** (si authentifié, cloisonnées par propriétaire) : **prix d'achat moyen** pondéré
  par les quantités, **millésimes en stock** (quantité + fenêtre d'apogée agrégée) et **`ma_note`**
  (l'entrée de carnet de dégustation la plus récente pour cette cuvée).

L'endpoint est en lecture publique pour la partie référentiel/conseil (`IsAuthenticatedOrReadOnly`) ;
les données privées (stock, `ma_note`) ne remontent que pour l'utilisateur authentifié.

**Synchro à la demande** : `POST /api/cuvees/{id}/rafraichir/` force un re-fetch wineapi et **met à
jour la cuvée en base** (le bouton 🔄 de la fiche). **Garde-fou anti-quota** : un *cooldown* par vin
(`WINEAPI_REFRESH_COOLDOWN`, défaut 1 h) renvoie `429` si le vin a déjà été synchronisé récemment, et
l'action est soumise au throttle `enrichment` — de quoi préserver le nombre d'appels wineapi limité.

### Carnet de dégustation

`NoteDegustation` (app `inventory`, données privées cloisonnées) est un **journal** : plusieurs
appréciations personnelles sont possibles pour une même cuvée, au fil des dégustations (note /5,
commentaire, curseurs acidité/tanin/fruit, date). CRUD via `/api/notes-degustation/`
(filtrable par `cuvee`/`millesime`). Côté fiche vin, « Ma note » reflète l'entrée la plus récente ;
côté mobile, l'onglet **Carnet** liste toutes les dégustations et « Enregistrer une dégustation »
ouvre la saisie.

### Fenêtre de dégustation & statut (apogée)

Chaque bouteille se voit calculer une **fenêtre d'apogée** (« quand la boire ») et un **statut de
dégustation** — `À garder`, `À boire` ou `Dépassé` — qui alimente le **code couleur** de la cave
(anneau vert = à boire, rouge = dépassé sur les alvéoles de « Ma cave »).

- **Logique métier pure** (`backend/apps/catalog/apogee.py`, testée, sans base de données) : à partir
  de la **couleur** du vin et de son **millésime**, on estime un potentiel de garde typique (un rouge
  s'ouvre ~3 à ~12 ans après la récolte, un blanc ~1 à ~5 ans, un rosé se boit dans les deux ans…),
  d'où une année de début et de fin d'apogée, puis le statut par comparaison à l'**année courante**.
- **La saisie manuelle prime** : si `apogee_debut`/`apogee_fin` sont renseignés sur la `Bouteille`,
  ils sont respectés tels quels ; sinon la fenêtre est estimée. L'API expose la **fenêtre effective**
  (`apogee_debut_effectif`/`apogee_fin_effectif`) et un `statut` **calculé en lecture** (le champ
  stocké n'est plus le pilote de l'affichage).
- Côté fiche vin, chaque **millésime en stock** porte sa fenêtre et son statut ; l'onglet **Mes vins**
  et la feuille bouteille affichent la pastille de statut colorée et la fenêtre de dégustation.

### Pas encore fait (roadmap)

- Mode hors-ligne avec synchronisation asynchrone.
- Recommandation mets-vins enrichie (LLM) et **apogée affinée par cépage / qualité du millésime**
  (l'estimation actuelle est dérivée de la couleur et du millésime).
- Valorisation financière (cote en temps réel) et qualité du millésime par région/année.
- Partage de cave / carnet en lecture seule.

## Frontend — SPA React + Tailwind (mobile-first)

Le front est une **application React (Vite + TypeScript + Tailwind v4)** dans `frontend/`, direction
artistique **« CavaVin »** (thème sombre lie-de-vin/or, cartes mates ; serif Cormorant Garamond +
Manrope — maquette « CavaVin Écrans »),
pensée pour un usage à 90 % mobile (optimisée Pixel 9). Authentification **JWT** (écran de
connexion / inscription).

**Développement** : `cd frontend && npm install && npm run dev` (Vite sur `:5173`, proxifie l'API
vers Django `:8000`). Lancer Django en parallèle (`python manage.py runserver`).

**Production** : le SPA est **buildé et servi par Django/WhiteNoise** dans le même conteneur — le
`Dockerfile` multi-stage build `frontend/` (Node) puis copie `dist/` dans `spa/` ; la vue `/` renvoie
`spa/index.html` et WhiteNoise sert les assets. Aucun serveur front séparé.

L'ancien template vanilla (`backend/templates/index.html`) reste comme fallback si le build est absent.

### Fonctionnalités de l'interface :

- **Navigation par onglets en bas d'écran** (Accueil / Vin / Cave / Carnet), zone tactile large,
  safe-areas iOS gérées ; l'ajout s'ouvre depuis l'accueil ou le « + » de « Mes vins », et les
  derniers mouvements (journal) s'affichent sur l'accueil.
- **Visualisation en alvéoles** : chaque bouteille est une case colorée (couleur du vin) avec
  un anneau de statut (vert = à boire, rouge = dépassé) ; si l'emplacement a une capacité, les
  places libres apparaissent en pointillés, façon casier réel. Rendu plafonné à 96 alvéoles
  par emplacement.
- **Bottom sheet** au tap sur une bouteille : fiche (domaine, millésime, couleur, statut,
  emplacement), stepper de quantité + occasion pour consommer, et déplacement vers un autre
  emplacement. Plus aucun `prompt()` bloquant.
- **Stats en tête de cave** (total, rouges, blancs, autres), sélecteur de caves en chips
  horizontales, toasts de confirmation/erreur.

Authentification par **JWT** : écran de connexion / inscription intégré (`POST /api/auth/register/`
et `/api/auth/token/`), token stocké côté navigateur et rafraîchi automatiquement. L'ancien template
vanilla, lui, réutilisait la session Django (`/admin/login/`).

## Confidentialité des données (RGPD)

Conformément à la spec, les données sont séparées en deux niveaux :

- **Référentiel partagé** (`catalog` : domaines, cépages, cuvées) : lisible par tous,
  modifiable par tout utilisateur authentifié — c'est la "base de données mutualisée"
  alimentée par la communauté.
- **Données privées** (`cellars` + `inventory` : caves, emplacements, bouteilles, mouvements) :
  strictement cloisonnées par propriétaire. Chaque queryset est filtré côté serveur ;
  un utilisateur ne peut ni voir, ni modifier, ni référencer (placement de bouteille,
  emplacement parent…) les objets d'un autre compte.

## Authentification API

Deux mécanismes cohabitent :

- **Session (DRF)** : utilisée par le mini frontend et le bouton "Authorize" (cookieAuth) du
  Swagger, via `/admin/login/`.
- **JWT (djangorestframework-simplejwt)** : utilisé par le SPA React et pensé pour les clients
  externes (mobile, self-hosted). Endpoints :
  - `POST /api/auth/register/` — `{"username", "password", "email"?}` → crée le compte et renvoie
    directement `{"username", "access", "refresh"}` (throttle `auth`, anti-abus). C'est l'onboarding
    utilisé par l'écran d'inscription du SPA.
  - `POST /api/auth/token/` — `{"username": "...", "password": "..."}` → `{"access", "refresh"}`
  - `POST /api/auth/token/refresh/` — `{"refresh": "..."}` → nouveau `access` (rotation activée)
  - `POST /api/auth/token/verify/` — vérifie la validité d'un token
  - Puis header `Authorization: Bearer <access>` sur les appels API. Durée de vie : 60 min
    (access) / 7 jours (refresh) — voir `SIMPLE_JWT` dans `backend/config/settings.py`.

## Architecture

```
cave-a-vin/
├── backend/
│   ├── config/          # settings, urls, auth (register), wsgi/asgi
│   ├── apps/
│   │   ├── catalog/     # Domaine, Cepage, Cuvee — référentiel vin partagé
│   │   │   ├── enrichment/    # providers enfichables (Open Food Facts, Claude, wineapi.io, LWIN/OCR, stub Vivino)
│   │   │   ├── ingest.py      # persistance mutualisée (upsert_cuvee, enrich_cuvee_from_wineapi)
│   │   │   ├── wine_profile.py# mapping pur détail wineapi → Cuvee (source de vérité unique)
│   │   │   ├── apogee.py      # logique pure fenêtre/statut de dégustation
│   │   │   └── sommellerie.py # conseil de service pur (dérivé de la couleur)
│   │   ├── cellars/     # Cave, Emplacement — structure physique (privé)
│   │   └── inventory/   # Bouteille (stock), MouvementStock, NoteDegustation (privé)
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/             # SPA React + Vite + Tailwind (voir frontend/README.md)
├── docker-compose.yml    # app tout-en-un (Django/gunicorn + SQLite)
└── .env.example
```

La base de données est **SQLite** (zéro dépendance externe, adapté au déploiement mono-conteneur). En
conteneur, `SQLITE_PATH` place le fichier SQLite sur un volume persistant — voir
`backend/config/settings.py`.

## Intégration continue & déploiement (CI/CD)

Trois workflows GitHub Actions automatisent la vérification, le versionnage et la livraison, sur
chaque **Pull Request vers `main`** et sur **`main`**.

### `ci.yml` — tests, couverture & build front

- **Backend** : installe les dépendances (Python 3.12), lance la suite de tests Django
  (`manage.py test`) instrumentée par [coverage.py](https://coverage.readthedocs.io/). La
  configuration (source mesurée, exclusions, **seuil minimal**) vit dans
  [`backend/.coveragerc`](backend/.coveragerc).
  - Le **seuil de couverture** (`fail_under = 85`) est appliqué : sous 85 %, le job échoue.
    La couverture réelle est d'environ **92 %** ; relevez le seuil au fil de l'enrichissement des tests.
  - Un **résumé de couverture** est écrit dans le récapitulatif du job (onglet *Summary* du run),
    et le **rapport HTML** est publié en artefact téléchargeable (`coverage-html`, conservé 14 jours).
- **Frontend** : `npm ci`, puis **lint** (`oxlint`) et **type-check + build** (`tsc -b && vite build`).
- **E2E (Playwright)** : job dédié qui **build le SPA, le sert** (webServer Playwright) et lance les
  tests bout-en-bout (`frontend/e2e/`). L'API est **mockée** (`page.route`) — aucun backend Django
  requis, tests déterministes. Le rapport HTML est publié en artefact (`playwright-report`).

Reproduire en local :

```bash
# Backend — couverture
cd backend
pip install -r requirements.txt coverage
coverage run manage.py test      # config lue depuis .coveragerc
coverage report                  # résumé + application du seuil
coverage html                    # rapport détaillé dans backend/htmlcov/

# Frontend — E2E
cd frontend
npm ci
npx playwright install chromium  # ou PLAYWRIGHT_CHROMIUM_PATH=<chemin> si déjà présent
npm run test:e2e
```

### `release.yml` — versionnage automatique (semantic-release)

À chaque push sur `main`, [semantic-release](https://semantic-release.gitbook.io/) analyse les
**commits conventionnels** depuis le dernier tag et en déduit la prochaine version — **plus aucun
numéro de version à gérer à la main** :

| Type de commit | Effet |
| --- | --- |
| `feat(...)` | bump **mineur** (`0.1.0` → `0.2.0`) |
| `fix` · `refactor` · `perf` · `build` · `revert` | bump **patch** (`0.1.0` → `0.1.1`) |
| footer `BREAKING CHANGE:` ou `type!` | bump **majeur** (`0.x` → `1.0.0`) |
| `docs` · `test` · `ci` · `chore` · `style` | **aucune release** (pas de redéploiement) |

Quand une version est publiée, le workflow crée le **tag `vX.Y.Z`** + la **release GitHub** (notes
générées depuis les commits), puis appelle `docker.yml` pour publier l'image versionnée. La règle est
donc : **écris des messages de commit conventionnels** (`feat(cave): …`, `fix(api): …`) — la version
et la publication suivent automatiquement. Les commits qui ne changent rien au runtime (`docs`, `ci`…)
ne déclenchent volontairement ni version ni redéploiement.

### `docker.yml` — build & publication de l'image

- Sur **PR** : build de vérification du `Dockerfile` multi-stage (front + back), **sans publication**.
- **Appelé par `release.yml`** (à chaque nouvelle version) : bake la version calculée dans l'image
  (`APP_VERSION` → front `__APP_VERSION__` + schéma d'API back) et publie sur
  `ghcr.io/ppierre89/cavavin` les tags `latest`, `X.Y.Z`, `X.Y`, `X` et `sha-xxxx`.

La **version est la source de vérité unique** : le tag semver, baké dans l'image, s'affiche dans l'UI
(en-tête / écran de connexion) et dans le schéma OpenAPI (`/api/schema/`). Le récapitulatif du job
liste les tags publiés. La mise à jour du NAS reste `docker compose pull && docker compose up -d`
(voir ci-dessous) : `latest` suit désormais la dernière **release** plutôt que chaque push.

## Lancer en local (sans Docker)

```powershell
cd cave-a-vin
python -m venv venv
.\venv\Scripts\pip install -r backend\requirements.txt
cd backend
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- Admin Django : http://localhost:8000/admin/
- Documentation Swagger de l'API : http://localhost:8000/api/docs/
- Racine de l'API : http://localhost:8000/api/

## Lancer avec Docker

```bash
cp .env.example .env
docker compose up
```

Compose tire l'image publiée `ghcr.io/ppierre89/cavavin:latest` (aucun build local). **Un seul
conteneur** : Django/gunicorn avec SQLite (aucun service base séparé). L'API est exposée sur
http://localhost:8000/. Le conteneur applique les migrations et régénère les fichiers statiques (admin,
Swagger — servis par [WhiteNoise](https://whitenoise.readthedocs.io/), pas besoin de nginx) à chaque
démarrage.

> Si le package ghcr est privé, authentifie d'abord Docker :
> `echo <TON_PAT> | docker login ghcr.io -u PPierre89 --password-stdin`. Une fois le package rendu
> public, aucune authentification n'est nécessaire.

## Déploiement sur un NAS (Docker)

L'image est **construite et publiée automatiquement** sur `ghcr.io/ppierre89/cavavin:latest` à chaque
merge sur `main` (voir `.github/workflows/docker.yml`). Le NAS n'a donc **pas besoin du code source**
ni de construire quoi que ce soit : deux fichiers suffisent.

1. **Copier deux fichiers sur le NAS** (par SMB/SFTP, ou les recréer à la main) :
   - `docker-compose.yml`
   - `.env` (à partir de `.env.example` du dépôt)
2. **Configurer `.env`** :
   - `DJANGO_SECRET_KEY` : génère une vraie valeur, ex. `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`.
   - `DJANGO_ALLOWED_HOSTS` : ajoute l'IP ou le nom d'hôte du NAS (ex. `192.168.1.50,mon-nas.local`),
     sinon Django refusera les requêtes venant d'un autre appareil que `localhost`.
   - `DJANGO_DEBUG` : laisser vide/absent pour garder le défaut sûr (`False`) en Docker.
   - `ANTHROPIC_API_KEY` : recommandé — reconnaissance d'étiquette et identification de vin via
     Claude (US 02/03/04), provider principal de la cascade.
   - `WINEAPI_KEY` : optionnel, repli de la cascade + données marchandes (prix, notes).
   - Sans aucune clé, le **repli gratuit OCR + LWIN** garde l'identification fonctionnelle : voir
     « Repli 100 % gratuit et hors-ligne » (import du dump via `manage.py import_lwin`).
   > Si le package ghcr est privé, authentifie Docker sur le NAS :
   > `echo <TON_PAT> | docker login ghcr.io -u PPierre89 --password-stdin`.
3. **Démarrer** (via l'interface Docker/Container Manager de ton NAS, ou en SSH) :
   ```bash
   docker compose up -d
   ```
   Un seul conteneur démarre (Django + SQLite), applique les migrations et régénère les statiques. La
   base SQLite est stockée dans le volume Docker `data` (persiste aux redémarrages et mises à jour).
4. **Créer le premier compte** :
   ```bash
   docker compose exec app python manage.py createsuperuser
   ```
5. Accéder depuis un navigateur (PC ou mobile sur le même réseau) : `http://<ip-du-nas>:8000/`.

**Mises à jour** : `docker compose pull && docker compose up -d` — récupère la dernière image publiée ;
migrations et fichiers statiques se réappliquent automatiquement au redémarrage.

**Dépannage — « l'ajout de vin plante » / `WORKER TIMEOUT` / worker `SIGKILL`** : les appels
d'enrichissement Claude / wineapi (identification texte/image) sont lents ; si gunicorn tourne avec un
`--timeout` court (30 s par défaut), il tue le worker en plein appel. Les réglages sûrs (timeout 120,
threads) sont dans `backend/gunicorn.conf.py`, **chargé automatiquement** — donc un simple
`docker compose pull && docker compose up -d` suffit à corriger, **même si un ancien
`docker-compose.yml` surcharge la commande** (tant qu'il ne force pas lui-même un `--timeout` court :
dans ce cas, retire ce flag ou réaligne le `command:` sur celui du dépôt).

**HTTPS** : ce compose sert du HTTP simple, adapté à un accès réseau local. Si tu exposes l'app sur
Internet (via un reverse proxy comme Nginx Proxy Manager ou Traefik), termine le TLS là et active en
plus `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` dans `backend/config/settings.py`
(désactivés par défaut ici pour ne pas casser un accès LAN sans certificat).

## Modèle de données (résumé)

- `Domaine` → `Cuvee` (couleur, appellation, cépages M2M) : le référentiel vin.
- `Cave` → `Emplacement` (auto-référencé, type Armoire/Casier/Clayette/Caisse/Case) : la structure
  physique de rangement, avec `chemin()` pour l'affichage complet (ex: "Armoire 1 > Clayette 3 > B4").
- `Bouteille` : une ligne de stock (cuvée + millésime + quantité) rattachée à un emplacement
  (nullable si pas encore placée), avec statut, prix d'achat, fenêtre d'apogée. Si l'emplacement
  a une `capacite`, l'API refuse tout placement ou changement de quantité qui la dépasserait
  (les bouteilles comptées sont celles directement assignées à l'emplacement ; chaque
  sous-emplacement a sa propre capacité).
- `MouvementStock` : historique des entrées/sorties/consommations sur une `Bouteille`.
- `NoteDegustation` (privé) : carnet de dégustation — plusieurs appréciations personnelles par cuvée
  (note /5, commentaire, curseurs acidité/tanin/fruit, date), cloisonnées par propriétaire.

## Prochaines étapes suggérées

1. Endpoint de recommandation mets-vins (entrée: description du menu, sortie: bouteilles suggérées),
   éventuellement enrichi par LLM.
2. Partage en lecture seule d'une `Cave` / du carnet de dégustation.
3. Apogée affinée par cépage et par qualité du millésime (l'estimation actuelle dérive de la couleur
   et du millésime).
4. Valorisation financière temps réel (au-delà de l'historique de prix wineapi déjà persisté) et
   mode hors-ligne avec synchronisation asynchrone.
