// Profil œnologique et avis *indicatifs* affichés dans la fiche vin.
//
// Ce sont des données de démonstration (profil type d'un grand cru classé du
// Médoc) : le backend n'expose pas encore ces informations. À remplacer par
// l'API d'enrichissement quand cépages, accords mets-vins, cote et avis
// communautaires seront disponibles côté serveur.

export interface ProfilVin {
  /** Température de service conseillée, ex. « 16-18 ». */
  temperature: string
  /** Durée de carafage conseillée, ex. « 1h-2h ». */
  carafage: string
  /** Qualité du millésime : index de 0 à 4 + libellé affiché. */
  qualite: { niveau: number; label: string }
  /** Axes du profil gustatif (valeur de 0 à 1, du pôle gauche au pôle droit). */
  gustatif: { gauche: string; droite: string; valeur: number }[]
  /** Assemblage des cépages (pourcentages). */
  cepages: { nom: string; pct: number }[]
  /** Accords mets-vins suggérés avec leur score de compatibilité. */
  mets: { nom: string; score: number; emoji: string }[]
  /** Note communautaire moyenne et nombre de votes. */
  communaute: { note: number; nb: number }
}

export interface AvisVin {
  auteur: string
  date: string
  note: number
  millesime: number
  texte: string
  likes: number
  commentaires: number
}

export const PROFIL_INDICATIF: ProfilVin = {
  temperature: '16-18',
  carafage: '1h-2h',
  qualite: { niveau: 3, label: 'Très bon' },
  gustatif: [
    { gauche: 'Léger', droite: 'Puissant', valeur: 0.78 },
    { gauche: 'Souple', droite: 'Tannique', valeur: 0.86 },
    { gauche: 'Doux', droite: 'Acide', valeur: 0.62 },
  ],
  cepages: [
    { nom: 'Cabernet Sauvignon', pct: 55 },
    { nom: 'Merlot', pct: 35 },
    { nom: 'Cabernet Franc', pct: 6 },
    { nom: 'Petit Verdot', pct: 4 },
  ],
  mets: [
    { nom: 'Bœuf', score: 95, emoji: '🥩' },
    { nom: 'Agneau', score: 90, emoji: '🍖' },
    { nom: 'Fromage affiné', score: 82, emoji: '🧀' },
  ],
  communaute: { note: 3.9, nb: 26 },
}

export const AVIS_INDICATIFS: AvisVin[] = [
  {
    auteur: 'Laurent',
    date: '16/11/2025',
    note: 4.1,
    millesime: 2016,
    texte:
      "Médoc classique sans plus. Attention aux différents millésimes, trop de disparité à mon goût. Le 2016 est sans aucun doute l'un des meilleurs.",
    likes: 0,
    commentaires: 1,
  },
]

/** Palette des 5 crans de qualité du millésime (du moins bon au meilleur). */
export const QUALITE_COULEURS = ['#d8695f', '#e0a06a', '#cdd6a8', '#6cae82', '#a9c2a0']
