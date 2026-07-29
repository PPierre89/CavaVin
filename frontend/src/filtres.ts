import type { Bouteille, Couleur } from './types'

/* ------------------------------------------------------------------ *
 *  Modèle et logique de filtrage de la vinothèque (« Mes vins »).
 *  Séparé du composant pour rester réutilisable et testable.
 * ------------------------------------------------------------------ */

/** Une entrée vinothèque : une cuvée + un millésime, quantités cumulées. */
export interface Ligne {
  key: string
  /** Ligne de stock représentative : elle porte aussi les attributs de la cuvée
   *  (couleur, appellation, région, valeur marché) servis par l'API. */
  ref: Bouteille
  couleur: Couleur
  quantite: number
}

export interface Filtres {
  couleurs: Set<Couleur>
  pays: Set<string>
  millesimes: Set<string>
  regions: Set<string>
  tailles: Set<string>
  /** Plage de valeur retenue, ou null quand aucune contrainte n'est posée. */
  valeur: [number, number] | null
}

/** Valeur marché d'une ligne (prix max, à défaut prix min), ou null. */
export function ligneValeur(l: Ligne): number | null {
  const v = l.ref.prix_max ?? l.ref.prix_min
  if (v == null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** Vrai si la plage de valeur restreint réellement la sélection. */
function valeurNarrowed(f: Filtres, bounds: [number, number]): boolean {
  return !!f.valeur && (f.valeur[0] > bounds[0] || f.valeur[1] < bounds[1])
}

/** Filtre vrai/faux d'une ligne selon la sélection courante. */
export function matchFiltres(l: Ligne, f: Filtres, bounds: [number, number]): boolean {
  if (f.couleurs.size && !f.couleurs.has(l.couleur)) return false
  if (f.pays.size && !(l.ref.pays && f.pays.has(l.ref.pays))) return false
  if (f.millesimes.size && !f.millesimes.has(String(l.ref.millesime ?? 'NM'))) return false
  if (f.regions.size && !(l.ref.region && f.regions.has(l.ref.region))) return false
  if (f.tailles.size && !f.tailles.has('Standard')) return false
  if (valeurNarrowed(f, bounds)) {
    const v = ligneValeur(l)
    if (v != null && (v < f.valeur![0] || v > f.valeur![1])) return false
  }
  return true
}

/** Nombre de critères actifs (pour le badge « N filtre(s) »). */
export function compteFiltres(f: Filtres, bounds: [number, number]): number {
  return (
    f.couleurs.size +
    f.pays.size +
    f.millesimes.size +
    f.regions.size +
    f.tailles.size +
    (valeurNarrowed(f, bounds) ? 1 : 0)
  )
}

/** Filtres vides (aucune sélection), curseur ouvert sur toute la plage. */
export function filtresVides(): Filtres {
  return {
    couleurs: new Set(),
    pays: new Set(),
    millesimes: new Set(),
    regions: new Set(),
    tailles: new Set(),
    valeur: null,
  }
}

export function cloneFiltres(f: Filtres): Filtres {
  return {
    couleurs: new Set(f.couleurs),
    pays: new Set(f.pays),
    millesimes: new Set(f.millesimes),
    regions: new Set(f.regions),
    tailles: new Set(f.tailles),
    valeur: f.valeur ? [f.valeur[0], f.valeur[1]] : null,
  }
}
