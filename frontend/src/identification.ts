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

export function identifyByText(query: string) {
  return api<IdentifiedWine>('POST', '/api/identifier-vin/', { query })
}

export function identifyByLabel(file: File) {
  const fd = new FormData()
  fd.append('image', file, file.name || 'etiquette.jpg')
  return api<IdentifiedWine>('POST', '/api/scan-etiquette/', fd)
}
