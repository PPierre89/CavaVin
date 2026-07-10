import { useMemo, useState } from 'react'
import { useData } from '../data'
import { BottleBar, BottleSheet } from '../components/bottle'
import { FicheVin } from '../components/FicheVin'
import { FiltresSheet } from '../components/FiltresSheet'
import {
  compteFiltres,
  filtresVides,
  ligneValeur,
  matchFiltres,
  type Filtres,
  type Ligne,
} from '../filtres'
import { StatutBadge, wineFill } from '../ui'
import { COULEUR_LABELS, type Bouteille, type Couleur, type Cuvee } from '../types'

/* ------------------------------------------------------------------ *
 *  « Mes vins » — vue vinothèque : la liste à plat de tous les vins
 *  scannés/ajoutés à la cave, indépendamment de leur emplacement
 *  physique. Chaque carte regroupe un couple cuvée + millésime et
 *  cumule les quantités dispersées dans plusieurs emplacements.
 * ------------------------------------------------------------------ */

/* Chips de filtre rapide par couleur, dans l'ordre de la maquette. */
const CHIPS: [Couleur, string][] = [
  ['ROUGE', 'Rouge'],
  ['BLANC', 'Blanc'],
  ['ROSE', 'Rosé'],
  ['BULLES', 'Pétillant'],
]

/** Formate un prix « 25.00 » -> « 25 € » (compact, comme la maquette). */
function formatPrixCourt(prix: string | null): string | null {
  if (prix == null || prix === '') return null
  return `${Math.round(Number(prix))} €`
}

/** Symbole monétaire à partir d'un code ISO. */
function devise(code: string): string {
  return { EUR: '€', USD: '$', GBP: '£' }[code] ?? code
}

export default function MesVinsScreen({ onAjouter }: { onAjouter: () => void }) {
  const { cuvees, bouteilles, cuveeColor } = useData()
  const [query, setQuery] = useState('')
  const [showFilters, setShowFilters] = useState(false)
  const [filtres, setFiltres] = useState<Filtres>(() => filtresVides())
  const [fiche, setFiche] = useState<Bouteille | null>(null)
  const [options, setOptions] = useState<Bouteille | null>(null)

  const cuveeMap = useMemo(() => {
    const m = new Map<number, Cuvee>()
    cuvees.forEach((c) => m.set(c.id, c))
    return m
  }, [cuvees])

  // Regroupe les bouteilles actives par cuvée + millésime en cumulant les
  // quantités, puis trie du millésime le plus récent au plus ancien.
  const lignes = useMemo(() => {
    const groupes = new Map<string, Ligne>()
    for (const b of bouteilles) {
      if (b.quantite <= 0) continue
      const key = `${b.cuvee}-${b.millesime ?? 'NM'}`
      const existant = groupes.get(key)
      if (existant) {
        existant.quantite += b.quantite
      } else {
        groupes.set(key, {
          key,
          ref: b,
          cuvee: cuveeMap.get(b.cuvee),
          couleur: cuveeColor(b),
          quantite: b.quantite,
        })
      }
    }
    return [...groupes.values()].sort(
      (a, b) =>
        (b.ref.millesime ?? 0) - (a.ref.millesime ?? 0) ||
        a.ref.domaine_nom.localeCompare(b.ref.domaine_nom),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bouteilles, cuveeMap, cuveeColor])

  // Bornes du curseur « Valeur des vins » : min/max des valeurs marché connues.
  const bounds = useMemo<[number, number]>(() => {
    const valeurs = lignes.map(ligneValeur).filter((v): v is number => v != null)
    if (valeurs.length === 0) return [0, 0]
    return [Math.floor(Math.min(...valeurs)), Math.ceil(Math.max(...valeurs))]
  }, [lignes])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return lignes.filter((l) => {
      if (!matchFiltres(l, filtres, bounds)) return false
      if (!q) return true
      const foin = `${l.ref.domaine_nom} ${l.ref.cuvee_nom} ${l.cuvee?.appellation ?? ''} ${
        l.cuvee?.region ?? ''
      } ${l.ref.millesime ?? ''}`.toLowerCase()
      return foin.includes(q)
    })
  }, [lignes, query, filtres, bounds])

  const nbFiltres = compteFiltres(filtres, bounds)
  const totalBouteilles = lignes.reduce((n, l) => n + l.quantite, 0)

  // Chip rapide couleur : sélection exclusive, « Tous » remet à zéro.
  const chipCouleur = (c: Couleur | null) => {
    setFiltres((f) => ({
      ...f,
      couleurs: c === null ? new Set<Couleur>() : new Set<Couleur>([c]),
    }))
  }
  const chipActive = (c: Couleur | null) =>
    c === null ? filtres.couleurs.size === 0 : filtres.couleurs.size === 1 && filtres.couleurs.has(c)

  const chipCls = (active: boolean) =>
    `shrink-0 px-3.5 py-1.5 rounded-full text-xs transition ${
      active ? `${wineFill} text-ink-bright font-semibold` : 'border border-line text-ink'
    }`

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3 px-0.5">
        <h1 className="font-serif text-[1.75rem] text-ink-bright m-0 font-medium">Mes vins</h1>
        <span className="text-xs text-muted">
          {lignes.length} réf. · {totalBouteilles} btl
        </span>
      </div>

      {/* ---------- Recherche + filtres + ajout ---------- */}
      <div className="flex gap-2.5 mb-3">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Cherchez un vin dans votre cave"
          className="flex-1 min-w-0 text-[16px] px-3.5 py-3 rounded-xl glass text-ink outline-none placeholder:text-placeholder focus:border-gold/60"
        />
        <button
          onClick={() => setShowFilters(true)}
          aria-label="Filtrer"
          className={`shrink-0 w-11 rounded-xl grid place-items-center transition ${
            nbFiltres ? `${wineFill} text-ink-bright` : 'glass text-muted'
          }`}
        >
          <span className="relative">
            <svg width="16" height="14" viewBox="0 0 16 14" aria-hidden="true">
              <path
                d="M1 2h14M3.5 7h9M6 12h4"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
              />
            </svg>
            {nbFiltres > 0 && (
              <span className="absolute -top-2 -right-2.5 min-w-4 h-4 px-1 rounded-full bg-gold text-[0.6rem] font-bold grid place-items-center" style={{ color: 'oklch(15% 0.012 40)' }}>
                {nbFiltres}
              </span>
            )}
          </span>
        </button>
        <button
          onClick={onAjouter}
          aria-label="Ajouter un vin"
          className={`shrink-0 w-11 rounded-xl grid place-items-center ${wineFill} text-ink-bright active:scale-95 transition`}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M7 1v12M1 7h12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      {/* ---------- Chips couleur ---------- */}
      <div className="flex gap-2 overflow-x-auto no-scrollbar pb-1 mb-3">
        <button className={chipCls(chipActive(null))} onClick={() => chipCouleur(null)}>
          Tous
        </button>
        {CHIPS.map(([c, lbl]) => (
          <button key={c} className={chipCls(chipActive(c))} onClick={() => chipCouleur(c)}>
            {lbl}
          </button>
        ))}
      </div>

      {/* ---------- Liste vinothèque ---------- */}
      {lignes.length === 0 ? (
        <div className="glass rounded-card p-4 text-muted text-sm">
          Aucun vin dans votre cave pour le moment. Ajoutez une bouteille (photo d'étiquette,
          code-barres ou recherche) pour commencer votre vinothèque.
        </div>
      ) : filtered.length === 0 ? (
        <div className="glass rounded-card p-4 text-muted text-sm">
          Aucun vin ne correspond à votre recherche.
        </div>
      ) : (
        <div className="flex flex-col gap-2.5">
          {filtered.map((l) => {
            const c = l.cuvee
            const prix = formatPrixCourt(l.ref.prix_achat)
            const valeur =
              c?.prix_min && c?.prix_max
                ? `${Math.round(Number(c.prix_min))}–${Math.round(Number(c.prix_max))} ${devise(
                    c.devise || 'EUR',
                  )}`
                : null
            const lieu = [c?.region || c?.appellation, l.ref.millesime]
              .filter(Boolean)
              .join(' · ')
            return (
              <button
                key={l.key}
                onClick={() => setFiche(l.ref)}
                className="glass rounded-[10px] p-3 flex items-center gap-3 text-left active:scale-[0.99] transition"
              >
                <BottleBar couleur={l.couleur} />
                <span className="flex-1 min-w-0 flex flex-col gap-[3px]">
                  <span className="text-[13px] truncate">
                    {c?.classification || l.ref.cuvee_nom}
                  </span>
                  <span className="text-[11px] text-muted truncate">
                    {lieu || l.ref.domaine_nom}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <StatutBadge
                      statut={l.ref.statut}
                      className="text-[10px] px-1.5 py-0.5 font-medium"
                    />
                    <span className="text-[10px] text-placeholder">
                      75cl{prix ? ` · ${prix}` : valeur ? ` · ${valeur}` : ''}
                    </span>
                  </span>
                  <span className="sr-only">{COULEUR_LABELS[l.couleur]}</span>
                </span>
                <span className="text-[11px] px-2 py-0.5 rounded-[10px] bg-surface-2 text-muted-strong">
                  ×{l.quantite}
                </span>
              </button>
            )
          })}
        </div>
      )}

      {fiche && (
        <FicheVin
          bouteille={fiche}
          onClose={() => setFiche(null)}
          onOptions={(b) => setOptions(b)}
          onRetirer={(b) => setOptions(b)}
        />
      )}

      <BottleSheet b={options} onClose={() => setOptions(null)} />

      <FiltresSheet
        open={showFilters}
        onClose={() => setShowFilters(false)}
        onApply={(f) => {
          setFiltres(f)
          setShowFilters(false)
        }}
        initial={filtres}
        lignes={lignes}
        bounds={bounds}
      />
    </div>
  )
}
