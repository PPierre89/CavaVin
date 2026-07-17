import { test, expect } from '@playwright/test'
import { mockApi, seedAuth } from './helpers'

test("connexion puis affichage de l'accueil", async ({ page }) => {
  await mockApi(page)
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'Connexion' })).toBeVisible()
  await page.getByPlaceholder('Identifiant').fill('tester')
  await page.getByPlaceholder('Mot de passe').fill('secret')
  await page.getByRole('button', { name: 'Entrer' }).click()

  // On arrive sur l'accueil : compteur de bouteilles + actions rapides.
  await expect(page.getByText('bouteilles en cave')).toBeVisible()
  await expect(page.getByText('12', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Ajouter une bouteille' })).toBeVisible()
})

test("l'accueil liste les vins à boire bientôt", async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  // La bouteille mockée est « À boire » : elle remonte dans la section dédiée.
  await expect(page.getByText('À boire bientôt', { exact: true })).toBeVisible()
  await expect(page.getByText('Grand Cru Classé').first()).toBeVisible()
})

test("l'onglet Cave affiche la jauge et le stock", async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Cave', exact: true }).click()

  // Jauge de remplissage de la cave courante (12 bouteilles / 12 places).
  await expect(page.getByText('12 / 12', { exact: true })).toBeVisible()
  await expect(page.getByText('Armoire 1')).toBeVisible()
})

test('ouverture de la fiche vin depuis une alvéole', async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Cave', exact: true }).click()
  await page.getByTitle('Château Cantemerle — Grand Cru Classé 2019').first().click()

  await expect(page.getByRole('heading', { name: 'Grand Cru Classé', level: 1 })).toBeVisible()
  await expect(page.getByText('Conseil de dégustation')).toBeVisible()
  await expect(page.getByText('3.9/5')).toBeVisible()
  await expect(page.getByText('Un Médoc élégant et structuré.')).toBeVisible()
  // Statut de dégustation calculé (jauge de maturité) + fenêtre de service.
  await expect(page.getByText('À boire').first()).toBeVisible()
  await expect(page.getByText('2022-2031')).toBeVisible()

  // Historique de prix (accumulé côté serveur) : section, meilleur prix courant,
  // fraîcheur du tarif et graphe SVG.
  const histo = page.getByRole('heading', { name: 'Historique de prix' })
  await histo.scrollIntoViewIfNeeded()
  await expect(histo).toBeVisible()
  await expect(page.getByText('42,50 €').first()).toBeVisible()
  await expect(page.getByText('Meilleur prix', { exact: true })).toBeVisible()
  await expect(page.getByText(/relevé le/).first()).toBeVisible()
  await expect(page.locator('svg[role="img"]')).toBeVisible()
})

test("l'onglet Carnet est accessible", async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Carnet' }).click()
  await expect(page.getByText('Carnet de dégustation')).toBeVisible()
})

test("l'onglet Vin affiche la vinothèque et filtre la recherche", async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Vin', exact: true }).click()

  // La carte vinothèque du vin scanné apparaît (cuvée + lieu · millésime + quantité).
  await expect(page.getByText('Grand Cru Classé').first()).toBeVisible()
  await expect(page.getByText('Haut-Médoc · 2019')).toBeVisible()
  await expect(page.getByText('×12')).toBeVisible()
  // Le statut de dégustation calculé apparaît sur la carte vinothèque.
  await expect(page.getByText('À boire').first()).toBeVisible()

  // La recherche filtre la liste : un terme absent vide la vinothèque.
  await page.getByPlaceholder('Cherchez un vin dans votre cave').fill('introuvable')
  await expect(page.getByText('Aucun vin ne correspond à votre recherche.')).toBeVisible()
})

test('les chips couleur restreignent la vinothèque', async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Vin', exact: true }).click()

  // Filtrer sur « Blanc » exclut le rouge scanné.
  await page.getByRole('button', { name: 'Blanc' }).click()
  await expect(page.getByText('Aucun vin ne correspond à votre recherche.')).toBeVisible()
  // « Tous » réaffiche tout.
  await page.getByRole('button', { name: 'Tous' }).click()
  await expect(page.getByText('Grand Cru Classé').first()).toBeVisible()
})

test('le panneau Filtres restreint la vinothèque par type de vin', async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Vin', exact: true }).click()
  await page.getByRole('button', { name: 'Filtrer' }).click()

  // Le panneau s'ouvre et le CTA affiche le total des bouteilles.
  await expect(page.getByRole('heading', { name: 'Filtres' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Voir les 12 bouteilles/ })).toBeVisible()

  // Filtrer sur « Blanc » (chip du panneau, la dernière du DOM) exclut le rouge
  // scanné : le compteur tombe à 0.
  await page.getByRole('button', { name: 'Blanc' }).last().click()
  await page.getByRole('button', { name: /Voir les 0 bouteille/ }).click()
  await expect(page.getByText('Aucun vin ne correspond à votre recherche.')).toBeVisible()

  // Le badge du bouton filtre reflète le nombre de critères actifs.
  await expect(page.getByLabel('Filtrer').getByText('1')).toBeVisible()
})

test('recherche dynamique : une cuvée déjà en base est suggérée sans appel réseau', async ({
  page,
}) => {
  await seedAuth(page)
  await mockApi(page)
  // Filet anti-quota : on fait échouer explicitement l'endpoint consommateur de
  // quota. Enregistré après mockApi, il est prioritaire (Playwright évalue les
  // routes dans l'ordre inverse d'enregistrement) — le test échouerait donc si
  // un appel externe partait pendant la recherche locale.
  await page.route('**/api/identifier-vin/', (route) => route.abort())
  await page.goto('/')

  // L'ajout se lance depuis l'action rapide de l'accueil.
  await page.getByRole('button', { name: 'Ajouter une bouteille' }).click()
  // La recherche par nom est un repli : on déplie « Autre méthode » d'abord.
  await page.getByRole('button', { name: /Autre méthode/ }).click()
  // Au fil de la frappe, la cuvée du catalogue local remonte en suggestion.
  await page.getByPlaceholder(/rechercher par nom/).fill('cantemerle')
  await page.getByText('Grand Cru Classé · Haut-Médoc').click()

  // Le vin est pré-rempli depuis la base, sans passer par wineapi.
  await expect(page.getByText(/Déjà en base/)).toBeVisible()
})

test('recherche dynamique : quand l’algorithme hésite, la liste sollicite une vérification', async ({
  page,
}) => {
  await seedAuth(page)
  await mockApi(page, {
    // Deux candidats plausibles : l'appli demande une vérification manuelle.
    'GET /api/recherche-vins/': {
      evaluation: 'hesitant',
      resultats: [
        {
          lwin: '1011248',
          libelle: 'Château Palmer (Margaux)',
          producteur: 'Château Palmer',
          vin: '',
          appellation: 'Margaux',
          region: 'Bordeaux',
          pays: 'France',
          couleur: 'ROUGE',
          score: 0.72,
          millesime: null,
          en_base: null,
        },
        {
          lwin: '1011249',
          libelle: 'Château Palmer - Alter Ego',
          producteur: 'Château Palmer',
          vin: 'Alter Ego',
          appellation: 'Margaux',
          region: 'Bordeaux',
          pays: 'France',
          couleur: 'ROUGE',
          score: 0.65,
          millesime: null,
          en_base: null,
        },
      ],
    },
  })
  await page.goto('/')

  await page.getByRole('button', { name: 'Ajouter une bouteille' }).click()
  await page.getByRole('button', { name: /Autre méthode/ }).click()
  await page.getByPlaceholder(/rechercher par nom/).fill('palmer')

  // L'hésitation est explicite (après le debounce) et les candidats listés.
  await expect(page.getByText(/Plusieurs correspondances/)).toBeVisible()
  await expect(page.getByText('Alter Ego · Margaux')).toBeVisible()
  await page.getByText('Château Palmer · Margaux').click()

  // La sélection identifie via le code LWIN (résolution locale côté serveur,
  // sans cascade externe) puis pré-remplit le formulaire.
  await expect(page.getByText(/Identifié \(référentiel\)/)).toBeVisible()
})

test('saisie guidée : quand l’algorithme est sûr, la fiche pré-remplie est proposée', async ({
  page,
}) => {
  await seedAuth(page)
  await mockApi(page, {
    // Le meilleur candidat domine : fiche proposée directement, étoffée par
    // l'enrichissement communautaire (cuvée déjà au catalogue partagé).
    'GET /api/recherche-vins/': {
      evaluation: 'sur',
      resultats: [
        {
          lwin: '1011250',
          libelle: 'Petrus (Pomerol)',
          producteur: 'Petrus',
          vin: '',
          appellation: 'Pomerol',
          region: 'Bordeaux',
          pays: 'France',
          couleur: 'ROUGE',
          score: 1.0,
          millesime: 2015,
          en_base: {
            cuvee_id: 9,
            cepages: ['Merlot'],
            note: 4.8,
            nb_notes: 212,
            accords: [{ nom: 'Bœuf', emoji: '🥩' }],
            image_url: '',
          },
        },
      ],
    },
  })
  await page.goto('/')

  await page.getByRole('button', { name: 'Ajouter une bouteille' }).click()
  await page.getByRole('button', { name: /Autre méthode/ }).click()
  await page.getByPlaceholder(/rechercher par nom/).fill('petrus 2015')

  // La fiche proposée affiche millésime, région et données communautaires.
  await expect(page.getByText('Meilleure correspondance')).toBeVisible()
  await expect(page.getByText(/Merlot · ★ 4\.8 \(212 avis\) · 🥩 Bœuf/)).toBeVisible()
  await page.getByText('✓ Utiliser cette fiche').click()

  await expect(page.getByText(/Identifié \(référentiel\)/)).toBeVisible()
})

test('ajout simplifié : vin identifié, récapitulatif, puis bouteille ajoutée', async ({
  page,
}) => {
  await seedAuth(page)
  await mockApi(page)
  // Filet anti-quota : l'ajout simplifié ne doit déclencher aucun appel
  // d'identification externe une fois la cuvée locale choisie.
  await page.route('**/api/identifier-vin/', (route) => route.abort())
  await page.goto('/')

  await page.getByRole('button', { name: 'Ajouter une bouteille' }).click()
  await page.getByRole('button', { name: /Autre méthode/ }).click()
  await page.getByPlaceholder(/rechercher par nom/).fill('cantemerle')
  await page.getByText('Grand Cru Classé · Haut-Médoc').click()

  // Étape 2 : récapitulatif du vin retenu (sans re-saisie), essentiel seul —
  // les champs secondaires sont repliés sous « Plus de détails ».
  await expect(page.getByText('Château Cantemerle', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'changer' })).toBeVisible()
  await expect(page.getByText('Quantité')).toBeVisible()
  await expect(page.getByPlaceholder('24.90')).toBeHidden()
  await page.getByRole('button', { name: /Plus de détails/ }).click()
  await expect(page.getByPlaceholder('24.90')).toBeVisible()

  await page.getByRole('button', { name: 'Ajouter à la cave' }).click()
  await expect(page.getByText(/Bouteille ajoutée à la cave/)).toBeVisible()
})

test('ajout par recherche texte en ligne pré-remplit le vin identifié', async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Ajouter une bouteille' }).click()
  // La recherche par nom est un repli : on déplie « Autre méthode » d'abord.
  await page.getByRole('button', { name: /Autre méthode/ }).click()
  // « Margaux » n'est pas dans le catalogue local : la recherche en ligne est
  // le seul recours (bouton 🔎), et déclenche l'appel wineapi.
  await page.getByPlaceholder(/rechercher par nom/).fill('Margaux')
  await page.getByRole('button', { name: '🔎' }).click()

  // Un toast confirme l'identification en ligne.
  await expect(page.getByText(/Identifié/)).toBeVisible()
})

test('glisser-déposer range une bouteille dans une case de la grille', async ({ page }) => {
  // Une armoire en grille (3×2) + une bouteille non rangée à glisser.
  const GRILLE = {
    id: 20,
    cave: 1,
    parent: null,
    nom: 'Armoire G',
    type_emplacement: 'ARMOIRE',
    capacite: 6,
    nb_colonnes: 3,
    nb_rangees: 2,
    disposition: 'ALIGNE',
    chemin: 'Armoire G',
    occupation_actuelle: 0,
  }
  const NON_RANGEE = {
    id: 200,
    cuvee: 5,
    cuvee_nom: 'Grand Cru Classé',
    domaine_nom: 'Château Cantemerle',
    millesime: 2019,
    emplacement: null,
    emplacement_chemin: null,
    quantite: 1,
    statut: 'A_BOIRE',
    prix_achat: '40.00',
    apogee_debut: null,
    apogee_fin: null,
    apogee_debut_effectif: 2022,
    apogee_fin_effectif: 2031,
  }

  await seedAuth(page)
  await mockApi(page, {
    'GET /api/emplacements/': [GRILLE],
    'GET /api/bouteilles/': [NON_RANGEE],
  })
  // Capture le POST de rangement (route plus prioritaire car enregistrée après).
  let posted: { emplacement?: number; case?: number; bouteille?: number } | null = null
  await page.route('**/api/rangements/**', async (route) => {
    if (route.request().method() === 'POST') {
      posted = route.request().postDataJSON()
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ id: 999, ...posted }),
      })
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    }
  })
  await page.goto('/')

  await page.getByRole('button', { name: 'Cave', exact: true }).click()

  const source = page.getByTitle('Glisser vers une case')
  const cible = page.locator('[data-emp="20"][data-case="0"]')
  await expect(source).toBeVisible()
  await expect(cible).toBeVisible()

  const s = (await source.boundingBox())!
  const d = (await cible.boundingBox())!
  await page.mouse.move(s.x + s.width / 2, s.y + s.height / 2)
  await page.mouse.down()
  await page.mouse.move(d.x + d.width / 2, d.y + d.height / 2, { steps: 12 })
  await page.mouse.up()

  await expect(page.getByText(/rangée/)).toBeVisible()
  expect(posted).toEqual({ bouteille: 200, emplacement: 20, case: 0 })
})

test('suppression d’un emplacement après confirmation', async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  let deleted = false
  await page.route('**/api/emplacements/**', async (route) => {
    if (route.request().method() === 'DELETE') {
      deleted = true
      await route.fulfill({ status: 204, body: '' })
    } else {
      // Laisse le mock par défaut répondre aux GET (liste des emplacements).
      await route.fallback()
    }
  })
  await page.goto('/')

  await page.getByRole('button', { name: 'Cave', exact: true }).click()
  await page.getByRole('button', { name: /Supprimer l'emplacement/ }).click()
  // La feuille de confirmation nomme l'emplacement, puis on valide.
  await expect(page.getByText(/Supprimer « Armoire 1 » \?/)).toBeVisible()
  await page.getByRole('button', { name: 'Supprimer', exact: true }).click()

  await expect(page.getByText('Emplacement supprimé.')).toBeVisible()
  expect(deleted).toBe(true)
})

test("le panneau d'administration est réservé au staff et liste les comptes", async ({ page }) => {
  await seedAuth(page)
  await mockApi(page, {
    // Compte staff : débloque l'accès au panneau depuis la feuille « compte ».
    'GET /api/auth/me/': { username: 'tester', is_staff: true, is_superuser: true },
    'GET /api/admin-panel/apercu/': {
      utilisateurs: { total: 3, actifs: 2, staff: 1 },
      catalogue: { domaines: 4, cepages: 6, cuvees: 5, references_lwin: 0 },
      stock: { lignes: 2, unites: 12, caves: 1, emplacements: 3 },
      activite: { notes_degustation: 1, mouvements: 7 },
      systeme: {
        version: '1.2.3',
        debug: false,
        providers: [
          { nom: 'openfoodfacts', actif: true },
          { nom: 'claude', actif: false },
        ],
      },
    },
    'GET /api/admin-panel/configuration/': {
      parametres: [
        { cle: 'ANTHROPIC_API_KEY', secret: true, configure: true, source: 'env', apercu: '••••wxyz' },
        { cle: 'WINEAPI_KEY', secret: true, configure: false, source: 'absent', apercu: '' },
        { cle: 'WINEAPI_BASE_URL', secret: false, configure: true, source: 'base', apercu: 'https://api.wineapi.io' },
      ],
    },
    'GET /api/admin-panel/sources/': {
      sources: [
        { source: 'openfoodfacts', actif: true, voulu: null, usage_mois: 3, plafond: null, epuise: false, limite_debit: 'usage raisonnable' },
        { source: 'grapeminds', actif: false, voulu: '0', usage_mois: 0, plafond: 250, epuise: false, limite_debit: '5 req/s · 60 req/min' },
      ],
    },
    'GET /api/admin-panel/utilisateurs/': [
      {
        id: 1,
        username: 'tester',
        email: 'tester@example.com',
        is_active: true,
        is_staff: true,
        is_superuser: true,
        date_joined: '2026-01-01T10:00:00Z',
        last_login: null,
        nb_bouteilles: 12,
        nb_caves: 1,
        nb_degustations: 1,
      },
      {
        id: 2,
        username: 'bob',
        email: '',
        is_active: true,
        is_staff: false,
        is_superuser: false,
        date_joined: '2026-02-01T10:00:00Z',
        last_login: null,
        nb_bouteilles: 0,
        nb_caves: 0,
        nb_degustations: 0,
      },
    ],
  })
  await page.goto('/')

  await page.getByRole('button', { name: 'Mon compte' }).click()
  await page.getByRole('button', { name: "Panneau d'administration" }).click()

  await expect(page.getByRole('heading', { name: 'Administration' })).toBeVisible()
  // Aperçu chiffré + état des sources d'enrichissement.
  await expect(page.getByText('Catalogue mutualisé')).toBeVisible()
  await expect(page.getByText('v1.2.3')).toBeVisible()
  // Sources d'identification : on/off + quota mensuel.
  await expect(page.getByText("Sources d'identification")).toBeVisible()
  await expect(page.getByText('GrapeMinds')).toBeVisible()
  // Configuration des API : les clés sont listées et masquées.
  await expect(page.getByText('Configuration des API')).toBeVisible()
  await expect(page.getByText('Clé API Claude (Anthropic)')).toBeVisible()
  await expect(page.getByText('••••wxyz')).toBeVisible()
  // Section d'import du référentiel LWIN.
  await expect(page.getByText('Référentiel LWIN')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Importer le référentiel' })).toBeVisible()
  // Liste des comptes : l'autre utilisateur y figure et est actionnable.
  await expect(page.getByText('bob', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Promouvoir staff' })).toBeVisible()
})
