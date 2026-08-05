import type { Page, Route } from '@playwright/test'

// Jeu de données mocké, cohérent avec les types de src/types.ts.
export const TOKEN = 'test-access-token'

const CUVEE = {
  id: 5,
  domaine: 1,
  domaine_nom: 'Château Cantemerle',
  nom: 'Grand Cru Classé',
  appellation: 'Haut-Médoc',
  couleur: 'ROUGE',
  cepages_noms: ['Merlot', 'Cabernet Sauvignon'],
  code_barres: '',
  reference_externe_id: 'w1',
}

const CAVE = { id: 1, nom: 'Ma cave', description: '' }

const EMPLACEMENT = {
  id: 10,
  cave: 1,
  parent: null,
  nom: 'Armoire 1',
  type_emplacement: 'ARMOIRE',
  capacite: 12,
  chemin: 'Armoire 1',
  occupation_actuelle: 12,
}

const BOUTEILLE = {
  id: 100,
  cuvee: 5,
  cuvee_nom: 'Grand Cru Classé',
  domaine_nom: 'Château Cantemerle',
  // Attributs de la cuvée servis avec la ligne de stock (cf. BouteilleSerializer).
  couleur: 'ROUGE',
  appellation: 'Haut-Médoc',
  region: 'Haut-Médoc',
  pays: 'France',
  classification: 'Grand Cru Classé',
  prix_min: '38.00',
  prix_max: '65.00',
  devise: 'EUR',
  millesime: 2019,
  emplacement: 10,
  emplacement_chemin: 'Armoire 1',
  quantite: 12,
  statut: 'A_BOIRE',
  prix_achat: '40.00',
  apogee_debut: null,
  apogee_fin: null,
  apogee_debut_effectif: 2022,
  apogee_fin_effectif: 2031,
}

const FICHE = {
  cuvee: {
    id: 5,
    nom: 'Grand Cru Classé',
    appellation: 'Haut-Médoc',
    couleur: 'ROUGE',
    domaine_nom: 'Château Cantemerle',
    cepages: ['Merlot', 'Cabernet Sauvignon'],
    region: 'Haut-Médoc',
    pays: 'France',
    classification: 'Grand Cru Classé',
    description: 'Un Médoc élégant et structuré.',
    elaborate: '',
    degre_alcool: 13.5,
    image_url: '',
    photo_etiquette_url: null,
  },
  conseil_degustation: { temperature: '16-18', carafage: '1h-2h' },
  profil_gustatif: [{ gauche: 'Léger', droite: 'Puissant', valeur: 0.8 }],
  accords_mets: [{ nom: 'Bœuf', emoji: '🥩', confiance: 0.95 }],
  note_communaute: { note: 3.9, nb: 26 },
  avis: [],
  prix_marche: { min: 38, max: 65, devise: 'EUR' },
  prix_marchands: [
    { marchand: 'Cave A', prix: 42.5, devise: 'EUR', url: 'https://example.test/a', releve_le: '2026-06-15' },
    { marchand: 'Cave B', prix: 59.9, devise: 'EUR', url: '', releve_le: '2026-06-15' },
  ],
  historique_prix: [
    { date: '2026-05-01', prix_min: 45, prix_max: 62, devise: 'EUR' },
    { date: '2026-06-01', prix_min: 43, prix_max: 60, devise: 'EUR' },
    { date: '2026-06-15', prix_min: 42.5, prix_max: 59.9, devise: 'EUR' },
  ],
  ma_note: null,
  prix_achat_moyen: '40.00',
  millesimes: [
    { millesime: 2019, quantite: 12, apogee_debut: 2022, apogee_fin: 2031, statut: 'A_BOIRE' },
  ],
  stock_total: 12,
  enrichissable: true,
  enrichi_le: '2026-07-08T10:00:00Z',
}

const IDENTIFY = {
  source: 'wineapi',
  created: true,
  cuvee: CUVEE,
  millesime: 2019,
  confidence: 0.92,
  infos: { region: 'Haut-Médoc' },
  suggestions: [],
}

/** Réponses par défaut, indexées par « MÉTHODE /chemin/ ». Surchargeables par test. */
function defaultRoutes(): Record<string, unknown> {
  return {
    'POST /api/auth/token/': { access: TOKEN, refresh: 'r' },
    'POST /api/auth/register/': { access: TOKEN, refresh: 'r', username: 'tester' },
    'GET /api/auth/me/': { username: 'tester', is_staff: false, is_superuser: false },
    'GET /api/caves/': [CAVE],
    'GET /api/emplacements/': [EMPLACEMENT],
    'GET /api/bouteilles/': [BOUTEILLE],
    'GET /api/cuvees/': [CUVEE],
    'GET /api/mouvements/': [],
    'GET /api/cuvees/5/fiche/': FICHE,
    'GET /api/notes-degustation/': [],
    'POST /api/identifier-vin/': IDENTIFY,
    'POST /api/scan-etiquette/': IDENTIFY,
    'POST /api/scan-code-barres/': IDENTIFY,
    // Création manuelle d'un vin (dernier recours de la recherche).
    'GET /api/domaines/': [],
    'POST /api/domaines/': { id: 42, nom: 'Domaine du Pré Vert' },
    'POST /api/cuvees/': { ...CUVEE, id: 77, domaine: 42 },
    // Recherche dynamique (autocomplétion référentiel) : vide par défaut.
    'GET /api/recherche-vins/': { evaluation: null, resultats: [] },
  }
}

/** Intercepte tous les appels /api/ et répond avec le jeu mocké (200 par défaut). */
export async function mockApi(page: Page, overrides: Record<string, unknown> = {}) {
  const routes = { ...defaultRoutes(), ...overrides }
  await page.route('**/api/**', async (route: Route) => {
    const url = new URL(route.request().url())
    const key = `${route.request().method()} ${url.pathname}`
    const body = key in routes ? routes[key] : []
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })
  })
}

/** Pré-remplit un token en localStorage pour arriver directement connecté. */
export async function seedAuth(page: Page) {
  await page.addInitScript((token) => {
    localStorage.setItem('adv_access', token as string)
    localStorage.setItem('adv_refresh', 'r')
    localStorage.setItem('adv_user', 'tester')
  }, TOKEN)
}
