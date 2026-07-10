# Frontend — SPA « allée des vins »

Application **React 19 + Vite 8 + TypeScript + Tailwind v4**, mobile-first (optimisée Pixel 9),
direction artistique **« allée des vins »** (bordeaux, crème, or ; verre givré ; serif Cormorant
Garamond). C'est l'interface de la [Cave à Vin API](../README.md) : gestion de cave, ajout de vins
par photo d'étiquette / code-barres / nom, fiche vin enrichie et carnet de dégustation.

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
│   ├── App.tsx            # Shell : navigation par onglets en bas d'écran
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
│   ├── screens/           # Écrans par onglet : Cave, MesVins, Ajouter, Carnet, Journal, Login
│   └── components/        # FicheVin, FiltresSheet, TastingSheet, VinIdentification, bottle
├── e2e/                   # Tests Playwright (app.spec.ts + helpers.ts)
├── vite.config.ts         # Build + proxy de dev vers Django
└── playwright.config.ts   # Build + sert le SPA ; API mockée via page.route
```

## Conventions

- **Toute requête API passe par `api.ts`** (`api()` / `apiAllPages()`) : le token JWT est injecté,
  rafraîchi automatiquement sur `401`, et les erreurs sont normalisées (`ApiError`, `errMsg`).
- **État global dans `data.tsx`** (`DataProvider` / `useData`), authentification dans `auth.tsx`
  (`AuthProvider` / `useAuth`).
- **Tailwind v4** avec des tokens de thème personnalisés (`text-wine-soft`, `text-gold`,
  `border-line`, `glass`, `rounded-card`…) : réutiliser ces tokens plutôt que des valeurs brutes.
- Ajout de vin par défaut = **photo d'étiquette** ; scan code-barres et recherche par nom sont des
  méthodes de repli (`VinIdentification` / `identification.ts`).
- **Version de l'app** : source de vérité unique dans `package.json`, injectée à la compilation par
  Vite (`define: __APP_VERSION__`, exposée via `src/version.ts`) et affichée discrètement dans l'UI
  (en-tête et écran de connexion). Bumper la version = éditer `package.json`.

## Production

Le SPA est **buildé puis servi par Django/WhiteNoise** dans le même conteneur (mono-conteneur) : le
`Dockerfile` build `frontend/` puis copie `dist/` dans `backend/spa/`. Pas de serveur front séparé.
Voir le [README racine](../README.md) pour le déploiement complet.
</content>
