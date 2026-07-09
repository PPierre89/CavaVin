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

/** Normalise une chaîne pour la recherche : minuscules, sans accents ni espaces superflus. */
function normalize(s: string): string {
  return s
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '') // retire les diacritiques (é -> e, ô -> o…)
    .trim()
}

/**
 * Recherche dynamique, en mémoire, dans le catalogue déjà chargé côté client —
 * aucun appel réseau, donc AUCUN quota wineapi consommé. C'est la brique de la
 * recherche « au fil de la frappe » : elle propose d'abord les cuvées déjà
 * connues (évitant un appel externe pour un vin déjà en base). Tous les mots de
 * la requête doivent apparaître dans le domaine, le nom, l'appellation ou la
 * région de la cuvée.
 */
export function searchLocalCuvees(cuvees: Cuvee[], query: string, limit = 6): Cuvee[] {
  const mots = normalize(query).split(/\s+/).filter(Boolean)
  if (mots.length === 0) return []
  const matches = cuvees.filter((c) => {
    const foin = normalize(`${c.domaine_nom} ${c.nom} ${c.appellation ?? ''} ${c.region ?? ''}`)
    return mots.every((m) => foin.includes(m))
  })
  return matches.slice(0, limit)
}
