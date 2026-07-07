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
   basculera sur le scan d'étiquette (US 03, à venir).

Côté mobile, le bouton **📷 Scanner le code-barres** utilise l'API native `BarcodeDetector`
(caméra arrière) quand elle est disponible, et bascule sinon sur une **saisie manuelle**. ⚠️ La caméra
exige un contexte sécurisé : elle fonctionne sur `http://localhost` mais nécessite **HTTPS** sur un
vrai téléphone via IP LAN.

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

### Pas encore fait (roadmap)

- Scan d'étiquette (US 02/03) : deux voies possibles —
  (a) OCR souverain sur conteneur (ex. Tesseract sur NAS via `OCR_ENDPOINT_URL`) → texte → `POST
  /api/identifier-vin/` déjà en place ; (b) **plus simple** : `POST /identify/image` de wineapi.io
  (photo d'étiquette JPEG/PNG ≤10 Mo) identifie directement, sans conteneur OCR à héberger. Note : le
  dataset X-Wines est **tabulaire** (pas d'images) — il ne sert pas à *entraîner* l'OCR mais peut
  servir de **corpus de rapprochement flou**.
- Mode hors-ligne avec synchronisation asynchrone.
- Moteur de recommandation mets-vins (LLM) et calcul algorithmique d'apogée.
- Valorisation financière (cote en temps réel) et tableaux de bord statistiques.
- Carnet de dégustation (notes, curseurs acidité/tanin/fruit) et partage de cave en lecture seule.

## Mini frontend (mobile-first)

Une page unique (`backend/templates/index.html`, servie sur `/`), pensée pour un usage à 90 %
mobile avec une direction artistique inspirée d'Oeni (thème sombre aubergine, accents dorés,
typographie serif pour les titres) :

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
