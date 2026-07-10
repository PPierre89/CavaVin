import { test, expect } from '@playwright/test'
import { mockApi, seedAuth } from './helpers'

test('connexion puis affichage de la cave', async ({ page }) => {
  await mockApi(page)
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'Connexion' })).toBeVisible()
  await page.getByPlaceholder('Identifiant').fill('tester')
  await page.getByPlaceholder('Mot de passe').fill('secret')
  await page.getByRole('button', { name: 'Entrer' }).click()

  // On arrive sur la cave : les stats et l'emplacement sont affichés.
  await expect(page.getByText('Bouteilles')).toBeVisible()
  await expect(page.getByText('Armoire 1')).toBeVisible()
})

test('la cave affiche la cave et le stock', async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await expect(page.getByRole('button', { name: 'Ma cave' })).toBeVisible()
  await expect(page.getByText('Armoire 1')).toBeVisible()
  // 12 bouteilles au total.
  await expect(page.getByText('12', { exact: true }).first()).toBeVisible()
})

test('ouverture de la fiche vin depuis une alvéole', async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByTitle('Château Cantemerle — Grand Cru Classé 2019').first().click()

  await expect(page.getByRole('heading', { name: 'Grand Cru Classé', level: 1 })).toBeVisible()
  await expect(page.getByText('Conseil de dégustation')).toBeVisible()
  await expect(page.getByText('3.9/5')).toBeVisible()
  await expect(page.getByText('Un Médoc élégant et structuré.')).toBeVisible()
  // Statut de dégustation calculé + fenêtre d'apogée effective.
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

test("l'onglet Mes vins affiche la vinothèque et filtre la recherche", async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Mes vins' }).click()

  // La carte vinothèque du vin scanné apparaît (domaine + millésime + quantité).
  await expect(page.getByText('Château Cantemerle').first()).toBeVisible()
  await expect(page.getByText('2019', { exact: true })).toBeVisible()
  await expect(page.getByText('x12')).toBeVisible()
  // Le statut de dégustation calculé apparaît sur la carte vinothèque.
  await expect(page.getByText('À boire').first()).toBeVisible()

  // La recherche filtre la liste : un terme absent vide la vinothèque.
  await page.getByPlaceholder('Cherchez un vin dans votre cave').fill('introuvable')
  await expect(page.getByText('Aucun vin ne correspond à votre recherche.')).toBeVisible()
})

test('le panneau Filtres restreint la vinothèque par type de vin', async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Mes vins' }).click()
  await page.getByRole('button', { name: 'Filtrer' }).click()

  // Le panneau s'ouvre et le CTA affiche le total des bouteilles.
  await expect(page.getByRole('heading', { name: 'Filtres' })).toBeVisible()
  await expect(page.getByRole('button', { name: /Voir les 12 bouteilles/ })).toBeVisible()

  // Filtrer sur « Blanc » exclut le rouge scanné : le compteur tombe à 0.
  await page.getByRole('button', { name: 'Blanc' }).click()
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

  await page.getByRole('button', { name: 'Ajouter' }).click()
  // La recherche par nom est un repli : on déplie « Autre méthode » d'abord.
  await page.getByRole('button', { name: /Autre méthode/ }).click()
  // Au fil de la frappe, la cuvée du catalogue local remonte en suggestion.
  await page.getByPlaceholder(/rechercher par nom/).fill('cantemerle')
  await page.getByText('Grand Cru Classé · Haut-Médoc').click()

  // Le vin est pré-rempli depuis la base, sans passer par wineapi.
  await expect(page.getByText(/Déjà en base/)).toBeVisible()
})

test('ajout par recherche texte en ligne pré-remplit le vin identifié', async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Ajouter' }).click()
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
