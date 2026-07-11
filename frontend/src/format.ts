/* ------------------------------------------------------------------ *
 *  Formatage partagé (prix, devises, fenêtres d'apogée, dates d'axe).
 *  Centralise les helpers auparavant dupliqués entre la fiche vin,
 *  la vinothèque et les feuilles bouteille.
 * ------------------------------------------------------------------ */

/** Symbole monétaire à partir d'un code ISO. */
export function devise(code: string): string {
  return { EUR: '€', USD: '$', GBP: '£' }[code] ?? code
}

/** Formate un prix « 25.00 » -> « 25,00 € ». */
export function formatPrix(prix: string | null): string {
  if (prix == null) return '-- €'
  return `${Number(prix).toFixed(2).replace('.', ',')} €`
}

/** Formate un prix « 25.00 » -> « 25 € » (compact, cartes de liste). */
export function formatPrixCourt(prix: string | null): string | null {
  if (prix == null || prix === '') return null
  return `${Math.round(Number(prix))} €`
}

/** Formate un montant « 42,50 € ». */
export function formatMontant(n: number, code: string): string {
  return `${n.toFixed(2).replace('.', ',')} ${devise(code)}`
}

/** Formate une fourchette « 38–65 € ». */
export function formatFourchette(min: number, max: number, code: string): string {
  return `${Math.round(min)}–${Math.round(max)} ${devise(code)}`
}

/** Formate une fenêtre d'apogée (« 2024-2035 », « dès 2024 », « avant 2035 »). */
export function formatApogee(debut: number | null, fin: number | null): string | null {
  if (debut && fin) return `${debut}-${fin}`
  if (debut) return `dès ${debut}`
  if (fin) return `avant ${fin}`
  return null
}

/** Mois + année compacts pour un axe, ex. « mai 26 ». Sûr pour les dates seules. */
export function moisCourt(iso: string): string {
  const anchor = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00Z` : iso
  return new Date(anchor).toLocaleDateString('fr-FR', {
    month: 'short',
    year: '2-digit',
    timeZone: 'Europe/Paris',
  })
}
