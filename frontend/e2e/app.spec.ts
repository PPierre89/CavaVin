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
})

test("l'onglet Carnet est accessible", async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Carnet' }).click()
  await expect(page.getByText('Carnet de dégustation')).toBeVisible()
})

test('ajout par recherche texte pré-remplit le vin identifié', async ({ page }) => {
  await seedAuth(page)
  await mockApi(page)
  await page.goto('/')

  await page.getByRole('button', { name: 'Ajouter' }).click()
  await page.getByPlaceholder(/rechercher par nom/).fill('Cantemerle')
  await page.getByRole('button', { name: '🔎' }).click()

  // Un toast confirme l'identification.
  await expect(page.getByText(/Identifié/)).toBeVisible()
})
