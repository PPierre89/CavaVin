// Formatage des dates de l'app. Deux garde-fous :
//  1. On force le fuseau Europe/Paris pour un affichage cohérent quel que soit
//     le fuseau de l'appareil (les DateTime de l'API sont en UTC, USE_TZ=True).
//  2. Les dates *seules* (« YYYY-MM-DD ») sont ancrées à midi UTC avant parsing,
//     pour éviter le décalage d'un jour (new Date('YYYY-MM-DD') = minuit UTC).

const TZ = 'Europe/Paris'

/** Date + heure, ex. « 8 juil. 10:53 ». */
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('fr-FR', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: TZ,
  })
}

/** Date seule, ex. « 8 juillet 2026 ». Sûre pour les valeurs date-only. */
export function formatDate(iso: string): string {
  const anchor = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00Z` : iso
  return new Date(anchor).toLocaleDateString('fr-FR', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    timeZone: TZ,
  })
}
