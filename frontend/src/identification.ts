// Identification d'un vin (couche logique, sans React) : les trois méthodes
// exposées par l'API — code-barres (US 01), photo d'étiquette (US 02/03) et
// recherche texte (US 04) — renvoient toutes la même forme normalisée.
import { api } from './api'
import type { Cuvee } from './types'

export interface IdentifiedWine {
  source: string
  cuvee: Cuvee
  millesime?: number | null
  confidence?: number | null
  infos?: { region?: string | null }
}

/** Taille maximale d'une photo d'étiquette acceptée par l'API (10 Mo). */
export const MAX_LABEL_SIZE = 10 * 1024 * 1024

/** Valide un code-barres EAN/UPC (8 à 14 chiffres). */
export const isValidEan = (value: string) => /^\d{8,14}$/.test(value)

export function identifyByBarcode(ean: string) {
  return api<IdentifiedWine>('POST', '/api/scan-code-barres/', { code_barres: ean })
}

export function identifyByText(query: string, lwin?: string) {
  // `lwin` (sélection d'une suggestion du référentiel) court-circuite la
  // cascade externe côté serveur : résolution locale, aucun quota consommé.
  return api<IdentifiedWine>('POST', '/api/identifier-vin/', lwin ? { query, lwin } : { query })
}

/** Enrichissement communautaire d'une suggestion déjà au catalogue partagé. */
export interface CuveeConnue {
  cuvee_id: number
  cepages: string[]
  note: number | null
  nb_notes: number | null
  accords: { nom: string; emoji: string }[]
  image_url: string
}

/** Suggestion du référentiel LWIN local (recherche dynamique). */
export interface SuggestionReferentiel {
  lwin: string
  libelle: string
  producteur: string
  vin: string
  appellation: string
  region: string
  pays: string
  couleur: string
  score: number
  millesime: number | null
  en_base: CuveeConnue | null
}

/** Réponse de la recherche dynamique : suggestions + décision d'affichage.
 *  `evaluation` « sur » = le meilleur candidat domine, l'appli propose sa
 *  fiche pré-remplie ; « hesitant » = plusieurs candidats plausibles, la
 *  liste sollicite une vérification manuelle. */
export interface RechercheReferentiel {
  evaluation: 'sur' | 'hesitant' | null
  resultats: SuggestionReferentiel[]
}

/**
 * Recherche dynamique (autocomplétion) dans le référentiel LWIN local — appel
 * léger et 100 % local côté serveur (aucun quota externe), pensé pour être
 * déclenché au fil de la frappe avec un debounce.
 */
export async function searchReferentiel(query: string): Promise<RechercheReferentiel> {
  const data = await api<Partial<RechercheReferentiel>>(
    'GET',
    `/api/recherche-vins/?q=${encodeURIComponent(query)}`,
  )
  return { evaluation: data?.evaluation ?? null, resultats: data?.resultats ?? [] }
}

export function identifyByLabel(file: File) {
  const fd = new FormData()
  fd.append('image', file, file.name || 'etiquette.jpg')
  return api<IdentifiedWine>('POST', '/api/scan-etiquette/', fd)
}

/**
 * Décode un code-barres présent sur une PHOTO (prise avec l'appareil photo natif).
 * Contrairement au scan « live » (getUserMedia), la capture par input fichier
 * fonctionne même hors contexte sécurisé (HTTP), donc sur iPhone/NAS. Import
 * dynamique de ZXing pour ne pas alourdir le bundle initial.
 */
export async function decodeBarcodeFromImage(file: File): Promise<string> {
  const { BrowserMultiFormatReader } = await import('@zxing/browser')
  const reader = new BrowserMultiFormatReader()
  const url = URL.createObjectURL(file)
  try {
    const result = await reader.decodeFromImageUrl(url)
    return result.getText()
  } finally {
    URL.revokeObjectURL(url)
  }
}

/**
 * Recherche dans le catalogue mutualisé — requête serveur, donc AUCUN quota
 * externe consommé. C'est la brique de la recherche « au fil de la frappe » :
 * elle propose d'abord les cuvées déjà connues, évitant un appel externe pour un
 * vin déjà en base. Tous les mots de la requête doivent apparaître dans le
 * domaine, le nom, l'appellation ou la région de la cuvée (filtre `search` de
 * l'API). Elle interroge tout le catalogue, là où l'ancienne version filtrait la
 * copie chargée côté client — copie plafonnée, donc muette dès que le catalogue
 * communautaire dépassait quelques centaines de cuvées.
 */
export async function searchCatalogue(query: string, limit = 6): Promise<Cuvee[]> {
  const data = await api<{ results?: Cuvee[] } | Cuvee[]>(
    'GET',
    `/api/cuvees/?search=${encodeURIComponent(query)}`,
  )
  const cuvees = Array.isArray(data) ? data : (data?.results ?? [])
  return cuvees.slice(0, limit)
}
