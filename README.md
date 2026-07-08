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
- Début Epic 3/4 : champs `apogee_debut`/`apogee_fin` sur `Bouteille` pour le futur calcul de fenêtre
  de dégustation, `statut` (à garder / à boire / dépassé) pour le code couleur, et `MouvementStock`
  pour l'historique des sorties/consommations ("bouteilles mortes").

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

Côté mobile, le bouton **📷 Scanner le code-barres** utilise l'API native `BarcodeDetector`
(caméra arrière) quand elle est disponible, et bascule sinon sur une **saisie manuelle**. ⚠️ La caméra
exige un contexte sécurisé : elle fonctionne sur `http://localhost` mais nécessite **HTTPS** sur un
vrai téléphone via IP LAN.

### Scan d'étiquette (US 02/03)

`POST /api/scan-etiquette/` avec un upload multipart (champ `image`, JPEG/PNG ≤ 10 Mo) — identifie
le vin à partir d'une **photo de l'étiquette** via `POST /identify/image` de wineapi.io (pas d'OCR à
héberger). Le hit est normalisé et **mis en cache local** comme pour le texte, et la réponse a la même
forme que `/api/identifier-vin/` (`confidence`, `infos`, `suggestions`). Échec →
`404 "Vin non identifié sur l'étiquette"`.

Côté mobile, le bouton **🏷️ Photographier l'étiquette** ouvre directement la caméra arrière
(`<input type="file" capture="environment">` — fonctionne partout, pas d'API caméra requise) et
pré-remplit le formulaire d'ajout, exactement comme le scan de code-barres.

### Identification par texte / wineapi.io (US 04)

`POST /api/identifier-vin/` avec `{"query": "Chateau Petrus 2015"}` — pensé pour la sortie OCR
(US 03) comme pour une saisie manuelle. Cascade : **base locale** (nom, millésime retiré) → **wineapi.io**
(`POST /identify/text` puis `GET /wines/{id}` pour enrichir) → `404 "Vin non identifié"`. Le hit est
normalisé et **mis en cache local** (domaine + cuvée + **cépages**), et la réponse renvoie
`confidence`, `infos` (région, note critique, description…) et `suggestions`.

wineapi.io fournit couleur, appellation, **cépages** et notes — ce qui **couvre le besoin de l'US 05**
(remplissage des trous de données) **sans recourir au scraping Vivino**.

Les erreurs documentées de l'API sont gérées proprement : `429` (quota) et `401` (clé invalide) sont
remontés via `EnrichmentError` avec un message clair (pas déguisés en « vin non trouvé ») ; réseau
indisponible ou 4xx/5xx divers = simple *miss*.

**Interface d'enrichissement enfichable** (`backend/apps/catalog/enrichment/`) : chaque source implémente
`lookup_by_barcode(ean)` et/ou `lookup_by_text(query)` renvoyant un `NormalizedWine`. Actifs :
**Open Food Facts** (code-barres) et **wineapi.io** (texte). `Vivino` reste un slot désactivé (pas
d'API publique, scraping = violation des CGU). La persistance est mutualisée (`ingest.upsert_cuvee`).
Aucune dépendance Python nouvelle (appels via `urllib` stdlib).

**Config** : la clé wineapi se met dans `.env` (`WINEAPI_KEY=...`, voir `.env.example`) — jamais dans le
code. Sans clé, le provider wineapi se désactive tout seul. Le fichier `.env` est ignoré par git.

### Fiche vin consolidée

`GET /api/cuvees/{id}/fiche/` renvoie en une requête tout ce dont la fiche vin du frontend a besoin :

- **Référentiel partagé** : nom, appellation, couleur, domaine et **cépages** de la cuvée.
- **Conseil de dégustation** dérivé de la couleur (`backend/apps/catalog/sommellerie.py`, logique
  *pure* et testée) : température de service, carafage, profil gustatif type et accords mets-vins.
- **Données privées** (si authentifié, cloisonnées par propriétaire) : **prix d'achat moyen** pondéré
  par les quantités et **millésimes en stock** (quantité + fenêtre d'apogée agrégée).

L'endpoint est en lecture publique pour la partie référentiel/conseil (`IsAuthenticatedOrReadOnly`) ;
les chiffres de stock ne remontent que pour l'utilisateur authentifié.

### Pas encore fait (roadmap)

- Mode hors-ligne avec synchronisation asynchrone.
- Recommandation mets-vins enrichie (LLM) et calcul algorithmique d'apogée (le conseil actuel est
  dérivé de la couleur).
- Valorisation financière (cote en temps réel) et qualité du millésime par région/année.
- Carnet de dégustation (note & avis communautaires par cuvée) et partage de cave en lecture seule.

## Frontend — SPA React + Tailwind (mobile-first)

Le front est une **application React (Vite + TypeScript + Tailwind v4)** dans `frontend/`, direction
artistique **« allée des vins »** (bordeaux, crème, or ; verre givré ; serif Cormorant Garamond),
pensée pour un usage à 90 % mobile (optimisée Pixel 9). Authentification **JWT** (écran de
connexion / inscription).

**Développement** : `cd frontend && npm install && npm run dev` (Vite sur `:5173`, proxifie l'API
vers Django `:8000`). Lancer Django en parallèle (`python manage.py runserver`).

**Production** : le SPA est **buildé et servi par Django/WhiteNoise** dans le même conteneur — le
`Dockerfile` multi-stage build `frontend/` (Node) puis copie `dist/` dans `spa/` ; la vue `/` renvoie
`spa/index.html` et WhiteNoise sert les assets. Aucun serveur front séparé.

L'ancien template vanilla (`backend/templates/index.html`) reste comme fallback si le build est absent.

### Fonctionnalités de l'interface :

- **Navigation par onglets en bas d'écran** (Ma cave / Ajouter / Journal), zone tactile large,
  safe-areas iOS gérées.
- **Visualisation en alvéoles** : chaque bouteille est un cercle coloré (couleur du vin) avec
  un anneau de statut (vert = à boire, rouge = dépassé) ; si l'emplacement a une capacité, les
  places libres apparaissent en pointillés, façon casier réel. Rendu plafonné à 96 alvéoles
  par emplacement.
- **Bottom sheet** au tap sur une bouteille : fiche (domaine, millésime, couleur, statut,
  emplacement), stepper de quantité + occasion pour consommer, et déplacement vers un autre
  emplacement. Plus aucun `prompt()` bloquant.
- **Stats en tête de cave** (total, rouges, blancs, autres), sélecteur de caves en chips
  horizontales, toasts de confirmation/erreur.

Elle réutilise la session Django — connecte-toi via `/admin/login/?next=/`.

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
- **JWT (djangorestframework-simplejwt)** : pensé pour les futurs clients externes (mobile, SPA,
  self-hosted). Endpoints :
  - `POST /api/auth/token/` — `{"username": "...", "password": "..."}` → `{"access", "refresh"}`
  - `POST /api/auth/token/refresh/` — `{"refresh": "..."}` → nouveau `access` (rotation activée)
  - `POST /api/auth/token/verify/` — vérifie la validité d'un token
  - Puis header `Authorization: Bearer <access>` sur les appels API. Durée de vie : 60 min
    (access) / 7 jours (refresh) — voir `SIMPLE_JWT` dans `backend/config/settings.py`.

## Architecture

```
cave-a-vin/
├── backend/
│   ├── config/          # settings, urls, wsgi/asgi
│   ├── apps/
│   │   ├── catalog/     # Domaine, Cepage, Cuvee — référentiel vin
│   │   ├── cellars/     # Cave, Emplacement — structure physique
│   │   └── inventory/   # Bouteille (stock), MouvementStock — mouvements
│   ├── requirements.txt
│   └── Dockerfile
├── docker-compose.yml    # app tout-en-un (Django/gunicorn + SQLite)
└── .env.example
```

La base de données est **SQLite** (zéro dépendance externe, adapté au déploiement mono-conteneur). En
conteneur, `SQLITE_PATH` place le fichier SQLite sur un volume persistant — voir
`backend/config/settings.py`.

## Intégration continue & déploiement (CI/CD)

Deux workflows GitHub Actions automatisent la vérification et la livraison. Ils tournent sur chaque
**Pull Request vers `main`** et sur **`main`**.

### `ci.yml` — tests, couverture & build front

- **Backend** : installe les dépendances (Python 3.12), lance la suite de tests Django
  (`manage.py test`) instrumentée par [coverage.py](https://coverage.readthedocs.io/). La
  configuration (source mesurée, exclusions, **seuil minimal**) vit dans
  [`backend/.coveragerc`](backend/.coveragerc).
  - Le **seuil de couverture** (`fail_under = 70`) est appliqué : sous 70 %, le job échoue.
    Relevez-le au fil de l'enrichissement des tests.
  - Un **résumé de couverture** est écrit dans le récapitulatif du job (onglet *Summary* du run),
    et le **rapport HTML** est publié en artefact téléchargeable (`coverage-html`, conservé 14 jours).
- **Frontend** : `npm ci`, puis **lint** (`oxlint`) et **type-check + build** (`tsc -b && vite build`).

Reproduire la mesure de couverture en local :

```bash
cd backend
pip install -r requirements.txt coverage
coverage run manage.py test      # config lue depuis .coveragerc
coverage report                  # résumé + application du seuil
coverage html                    # rapport détaillé dans backend/htmlcov/
```

### `docker.yml` — build & publication de l'image

- Sur **PR** : build de vérification du `Dockerfile` multi-stage (front + back), **sans publication**.
- Sur **`main`** : publie `ghcr.io/ppierre89/cavavin:latest` **et** un tag immuable `sha-xxxx`
  (déploiement continu — le NAS récupère `latest`).
- Sur un **tag de version `vX.Y.Z`** (`git tag v1.2.0 && git push --tags`) : publie les tags
  sémantiques `1.2.0`, `1.2`, `1` pour épingler une release précise.
- Déclenchement **manuel** possible via l'onglet *Actions* (`workflow_dispatch`).

Le récapitulatif du job liste les tags effectivement publiés. La mise à jour du NAS reste
`docker compose pull && docker compose up -d` (voir ci-dessous).

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
   - `WINEAPI_KEY` : optionnel, pour l'identification de vin par texte (US 04).
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

## Prochaines étapes suggérées

1. Endpoint d'import "scan étiquette" (upload image → OCR → pré-remplissage `Cuvee`/`Bouteille`).
2. Endpoint de recommandation mets-vins (entrée: description du menu, sortie: bouteilles suggérées).
3. Modèle `NoteDegustation` (Epic 4) et endpoint de partage en lecture seule d'une `Cave`.
4. Endpoint d'inscription (actuellement les comptes se créent via `manage.py createsuperuser` /
   l'admin Django) pour un vrai onboarding utilisateur.
