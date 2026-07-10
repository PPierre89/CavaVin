# Frontend — SPA « CavaVin »

Application **React 19 + Vite 8 + TypeScript + Tailwind v4**, mobile-first (optimisée Pixel 9),
direction artistique **« CavaVin »** (thème sombre lie-de-vin/or, cartes mates, serif Cormorant
Garamond + Manrope — maquette « CavaVin Écrans »). C'est l'interface de la
[Cave à Vin API](../README.md) : gestion de cave, ajout de vins par photo d'étiquette /
code-barres / nom, fiche vin enrichie et carnet de dégustation.

## Développement

```bash
npm install
npm run dev        # Vite sur http://localhost:5173
```

Le serveur de dev **proxifie** `/api`, `/admin` et `/static` vers Django sur `:8000`
(voir `vite.config.ts`) : lancer le backend en parallèle (`cd ../backend && python manage.py runserver`).

## Scripts

| Commande | Rôle |
| --- | --- |
| `npm run dev` | Serveur de dev Vite (HMR) sur `:5173`, proxy API → Django `:8000`. |
| `npm run build` | Type-check (`tsc -b`) **et** build de production (`vite build`) → `dist/`. |
| `npm run lint` | Lint [oxlint](https://oxc.rs) (exécuté en CI). |
| `npm run preview` | Sert le build `dist/` localement pour vérification. |
| `npm run test:e2e` | Tests bout-en-bout [Playwright](https://playwright.dev) (`e2e/`), **API mockée**. |

## Structure

```
frontend/
├── src/
│   ├── App.tsx            # Shell : navigation 4 onglets (Accueil / Vin / Cave / Carnet)
│   ├── main.tsx           # Point d'entrée (AuthProvider + ToastProvider)
│   ├── api.ts             # Client fetch JWT (Bearer) : refresh auto sur 401, pagination
│   ├── auth.tsx           # AuthProvider — JWT (login/register) stocké en localStorage
│   ├── data.tsx           # DataProvider — état global (caves, emplacements, bouteilles, cuvées)
│   ├── toast.tsx          # ToastProvider — notifications de confirmation/erreur
│   ├── types.ts           # Types TS reflétant les payloads de l'API
│   ├── ui.tsx             # Tokens & primitives UI partagés (Logo, classes de boutons/inputs…)
│   ├── filtres.ts         # Logique de filtrage « Mes vins »
│   ├── identification.ts  # Logique d'identification de vin (photo / code-barres / texte)
│   ├── dates.ts           # Helpers de formatage de dates
│   ├── screens/           # Écrans : Accueil, Cave, MesVins, Ajouter, Carnet, Login
│   └── components/        # FicheVin, FiltresSheet, TastingSheet, ChoixVinSheet, VinIdentification, bottle
├── e2e/                   # Tests Playwright (app.spec.ts + helpers.ts)
├── vite.config.ts         # Build + proxy de dev vers Django
└── playwright.config.ts   # Build + sert le SPA ; API mockée via page.route
```

## Conventions

- **Toute requête API passe par `api.ts`** (`api()` / `apiAllPages()`) : le token JWT est injecté,
  rafraîchi automatiquement sur `401`, et les erreurs sont normalisées (`ApiError`, `errMsg`).
- **État global dans `data.tsx`** (`DataProvider` / `useData`), authentification dans `auth.tsx`
  (`AuthProvider` / `useAuth`).
- **Tailwind v4** avec des tokens de thème personnalisés en oklch (`bg-wine`, `text-gold`,
  `border-line`, `bg-surface`, `glass`, `rounded-card`…) : réutiliser ces tokens plutôt que des
  valeurs brutes.
- L'ajout n'est **pas un onglet** : il s'ouvre depuis l'accueil (« Ajouter une bouteille ») ou le
  bouton « + » de « Mes vins ». Ajout de vin par défaut = **photo d'étiquette** ; scan code-barres
  et recherche par nom sont des méthodes de repli (`VinIdentification` / `identification.ts`).
- **Version de l'app** : injectée à la compilation dans `__APP_VERSION__` (exposée via
  `src/version.ts`), affichée discrètement dans l'UI (en-tête et écran de connexion). En release, la
  CI passe la version calculée (tag semver) via la variable `APP_VERSION` ; en local, on retombe sur
  `package.json`. La version n'est donc **pas éditée à la main** : elle découle des commits
  conventionnels (voir la section CI/CD du [README racine](../README.md)).

## Production

Le SPA est **buildé puis servi par Django/WhiteNoise** dans le même conteneur (mono-conteneur) : le
`Dockerfile` build `frontend/` puis copie `dist/` dans `backend/spa/`. Pas de serveur front séparé.
Voir le [README racine](../README.md) pour le déploiement complet.
</content>
