export type Couleur = 'ROUGE' | 'BLANC' | 'ROSE' | 'BULLES' | 'AUTRE'
export type Statut = 'A_GARDER' | 'A_BOIRE' | 'DEPASSE'
export type TypeEmplacement = 'ARMOIRE' | 'CASIER' | 'CLAYETTE' | 'CAISSE' | 'CASE'
export type Disposition = 'ALIGNE' | 'DECALE_GAUCHE' | 'DECALE_DROITE'

export interface Cave {
  id: number
  nom: string
  description: string
}

export interface Domaine {
  id: number
  nom: string
}

export interface Emplacement {
  id: number
  cave: number
  parent: number | null
  nom: string
  type_emplacement: TypeEmplacement
  capacite: number | null
  nb_colonnes: number | null
  nb_rangees: number | null
  disposition: Disposition
  chemin: string
  occupation_actuelle: number
}

export interface Cuvee {
  id: number
  domaine: number
  domaine_nom: string
  nom: string
  appellation: string
  couleur: Couleur
  cepages_noms: string[]
  region?: string
  pays?: string
  classification?: string
  image_url?: string
  prix_min?: string | null
  prix_max?: string | null
  devise?: string
}

export interface Bouteille {
  id: number
  cuvee: number
  cuvee_nom: string
  domaine_nom: string
  millesime: number | null
  emplacement: number | null
  emplacement_chemin: string | null
  quantite: number
  statut: Statut
  prix_achat: string | null
  apogee_debut: number | null
  apogee_fin: number | null
  // Fenêtre d'apogée effective : saisie manuelle si présente, sinon estimée
  // côté serveur depuis la couleur + le millésime.
  apogee_debut_effectif: number | null
  apogee_fin_effectif: number | null
}

/** Placement case par case : une bouteille physique dans une case d'une grille. */
export interface Rangement {
  id: number
  bouteille: number
  emplacement: number
  case: number
}

/* ---------- Fiche vin consolidée (GET /api/cuvees/{id}/fiche/) ---------- */
export interface AxeGustatif {
  gauche: string
  droite: string
  valeur: number
}
export interface Accord {
  nom: string
  emoji: string
  confiance: number | null
}
export interface AvisCritique {
  reviewer: string
  score: number | null
  score_text: string | null
  date: string | null
}
export interface PrixMarche {
  min: number
  max: number
  devise: string
}
export interface PrixMarchand {
  marchand: string
  prix: number
  devise: string
  url: string
  // Date de relevé du tarif (« prix relevé le… »), « YYYY-MM-DD » ou '' si inconnue.
  releve_le?: string
}
/** Un point de l'historique de prix (une observation par jour de relevé). */
export interface PointPrix {
  date: string
  prix_min: number
  prix_max: number
  devise: string
}
export interface FicheMillesime {
  millesime: number | null
  quantite: number
  apogee_debut: number | null
  apogee_fin: number | null
  statut: Statut
}
export interface FicheCuvee {
  cuvee: {
    id: number
    nom: string
    appellation: string
    couleur: Couleur
    domaine_nom: string
    cepages: string[]
    region: string
    pays: string
    classification: string
    description: string
    elaborate: string
    degre_alcool: number | null
    image_url: string
  }
  conseil_degustation: { temperature: string; carafage: string }
  profil_gustatif: AxeGustatif[]
  accords_mets: Accord[]
  note_communaute: { note: number; nb: number } | null
  avis: AvisCritique[]
  prix_marche: PrixMarche | null
  prix_marchands: PrixMarchand[]
  historique_prix: PointPrix[]
  ma_note: { note: string; commentaire: string; millesime: number | null; date: string } | null
  prix_achat_moyen: string | null
  millesimes: FicheMillesime[]
  stock_total: number
  enrichissable: boolean
  enrichi_le: string | null
}

export interface Mouvement {
  id: number
  bouteille: number
  type_mouvement: 'ENTREE' | 'SORTIE' | 'CONSOMMATION' | 'AJUSTEMENT'
  quantite: number
  date: string
  occasion: string
}

export interface NoteDegustation {
  id: number
  cuvee: number
  cuvee_nom: string
  domaine_nom: string
  couleur: Couleur
  millesime: number | null
  note: string
  commentaire: string
  acidite: number | null
  tanin: number | null
  fruit: number | null
  date_degustation: string
  cree_le: string
}

export const COULEUR_LABELS: Record<Couleur, string> = {
  ROUGE: 'Rouge',
  BLANC: 'Blanc',
  ROSE: 'Rosé',
  BULLES: 'Bulles',
  AUTRE: 'Autre',
}
export const STATUT_LABELS: Record<Statut, string> = {
  A_GARDER: 'À garder',
  A_BOIRE: 'À boire',
  DEPASSE: 'Dépassé',
}
/* Couleur du code statut de dégustation (fenêtre d'apogée) : « à garder »
   neutre, « à boire » signalé en or, « dépassé » en alerte. */
export const STATUT_COLORS: Record<Statut, string> = {
  A_GARDER: 'var(--color-muted-strong)',
  A_BOIRE: 'var(--color-gold)',
  DEPASSE: 'var(--color-alerte)',
}
/* Aplat CSS de chaque couleur de vin (silhouettes, alvéoles, légendes). */
export const COULEUR_VARS: Record<Couleur, string> = {
  ROUGE: 'var(--color-rouge)',
  BLANC: 'var(--color-blanc)',
  ROSE: 'var(--color-rose)',
  BULLES: 'var(--color-bulles)',
  AUTRE: 'var(--color-autre)',
}
export const TYPE_LABELS: Record<TypeEmplacement, string> = {
  ARMOIRE: 'Armoire',
  CASIER: 'Casier',
  CLAYETTE: 'Clayette',
  CAISSE: 'Caisse bois',
  CASE: 'Case',
}
export const DISPOSITION_LABELS: Record<Disposition, string> = {
  ALIGNE: 'Aligné',
  DECALE_GAUCHE: 'Décalé à gauche',
  DECALE_DROITE: 'Décalé à droite',
}
