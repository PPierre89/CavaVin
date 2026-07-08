export type Couleur = 'ROUGE' | 'BLANC' | 'ROSE' | 'BULLES' | 'AUTRE'
export type Statut = 'A_GARDER' | 'A_BOIRE' | 'DEPASSE'
export type TypeEmplacement = 'ARMOIRE' | 'CASIER' | 'CLAYETTE' | 'CAISSE' | 'CASE'

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
}
export interface FicheMillesime {
  millesime: number | null
  quantite: number
  apogee_debut: number | null
  apogee_fin: number | null
}
export interface FicheCuvee {
  cuvee: {
    id: number
    nom: string
    appellation: string
    couleur: Couleur
    domaine_nom: string
    cepages: string[]
  }
  conseil_degustation: { temperature: string; carafage: string }
  profil_gustatif: AxeGustatif[]
  accords_mets: Accord[]
  prix_achat_moyen: string | null
  millesimes: FicheMillesime[]
  stock_total: number
}

export interface Mouvement {
  id: number
  bouteille: number
  type_mouvement: 'ENTREE' | 'SORTIE' | 'CONSOMMATION' | 'AJUSTEMENT'
  quantite: number
  date: string
  occasion: string
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
export const TYPE_LABELS: Record<TypeEmplacement, string> = {
  ARMOIRE: 'Armoire',
  CASIER: 'Casier',
  CLAYETTE: 'Clayette',
  CAISSE: 'Caisse bois',
  CASE: 'Case',
}
