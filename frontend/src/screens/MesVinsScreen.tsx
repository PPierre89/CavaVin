import { useMemo, useState } from 'react'
import { useData } from '../data'
import { BottleSheet } from '../components/bottle'
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
import { StatutBadge, wineGrad } from '../ui'
import { COULEUR_LABELS, type Bouteille, type Couleur, type Cuvee } from '../types'

/* ------------------------------------------------------------------ *
 *  « Mes vins » — vue vinothèque : la liste à plat de tous les vins
 *  scannés/ajoutés à la cave, indépendamment de leur emplacement
 *  physique. Chaque carte regroupe un couple cuvée + millésime et
 *  cumule les quantités dispersées dans plusieurs emplacements.
 * ------------------------------------------------------------------ */

/* Dégradé de la vignette bouteille, selon la couleur du vin. */
const CARD_BG: Record<Couleur, string> = {
  ROUGE: 'linear-gradient(150deg, #8e2f45, #c15571)',
  BLANC: 'linear-gradient(150deg, #9c8a3d, #e6d491)',
  ROSE: 'linear-gradient(150deg, #b5627a, #e8a0b4)',
  BULLES: 'linear-gradient(150deg, #a8863d, #d9b653)',
  AUTRE: 'linear-gradient(150deg, #52585f, #9aa0ab)',
}

/** Formate un prix « 25.00 » -> « 25,00 € ». */
function formatPrix(prix: string | null): string {
  if (prix == null) return '--€'
  return `${Number(prix).toFixed(2).replace('.', ',')} €`
}

/** Symbole monétaire à partir d'un code ISO. */
function devise(code: string): string {
  return { EUR: '€', USD: '$', GBP: '£' }[code] ?? code
}

/* Petit badge « Oeni+ 👑 » pour les infos premium verrouillées. */
function OeniLock() {
  return (
    <span className="inline-flex items-center gap-1 text-gold font-semibold">
      Oeni+ <span className="text-[0.85rem]">👑</span>
    </span>
  )
}

export default function MesVinsScreen() {
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

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3 px-0.5">
        <h1 className="font-serif text-[1.7rem] text-ink m-0">Mes vins</h1>
        <span className="text-xs text-muted">
          {lignes.length} réf. · {totalBouteilles} btl
        </span>
      </div>

      {/* ---------- Recherche + filtre ---------- */}
      <div className="flex gap-2 mb-3">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Cherchez un vin dans votre cave"
          className="flex-1 text-[16px] px-4 py-3 rounded-2xl glass text-ink outline-none placeholder:text-placeholder focus:border-gold/35"
        />
        <button
          onClick={() => setShowFilters(true)}
          aria-label="Filtrer"
          className={`shrink-0 w-12 rounded-2xl grid place-items-center text-lg transition ${
            nbFiltres ? `${wineGrad} text-white` : 'glass text-muted'
          }`}
        >
          <span className="relative">
            🔽
            {nbFiltres > 0 && (
              <span className="absolute -top-1.5 -right-2 min-w-4 h-4 px-1 rounded-full bg-gold text-[0.6rem] font-bold text-bg grid place-items-center">
                {nbFiltres}
              </span>
            )}
          </span>
        </button>
      </div>

      {/* ---------- Liste vinothèque ---------- */}
      {lignes.length === 0 ? (
        <div className="glass rounded-card p-4 text-muted text-sm">
          Aucun vin dans votre cave pour le moment. Scannez une étiquette depuis l'onglet Ajouter
          pour commencer votre vinothèque.
        </div>
      ) : filtered.length === 0 ? (
        <div className="glass rounded-card p-4 text-muted text-sm">
          Aucun vin ne correspond à votre recherche.
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {filtered.map((l) => {
            const c = l.cuvee
            const valeur =
              c?.prix_min && c?.prix_max
                ? `${Math.round(Number(c.prix_min))}–${Math.round(Number(c.prix_max))} ${devise(
                    c.devise || 'EUR',
                  )}`
                : null
            const lieu = [c?.region || c?.appellation, c?.pays].filter(Boolean).join(', ')
            return (
              <button
                key={l.key}
                onClick={() => setFiche(l.ref)}
                className="glass rounded-card p-2.5 flex gap-3 text-left active:scale-[0.99] transition"
              >
                {/* Vignette bouteille */}
                <div
                  className="relative shrink-0 w-[104px] h-[132px] rounded-2xl grid place-items-center overflow-hidden"
                  style={{ background: CARD_BG[l.couleur] }}
                >
                  <span className="absolute top-1.5 left-2 text-white font-serif text-[1.05rem] font-semibold drop-shadow">
                    {l.ref.millesime ?? 'N.M.'}
                  </span>
                  <span className="absolute top-1.5 right-2 text-white font-serif text-[1.05rem] font-semibold drop-shadow">
                    x{l.quantite}
                  </span>
                  {c?.image_url ? (
                    <img
                      src={c.image_url}
                      alt={l.ref.cuvee_nom}
                      loading="lazy"
                      className="h-[104px] object-contain drop-shadow-[0_6px_14px_rgba(0,0,0,0.45)]"
                    />
                  ) : (
                    <span className="text-[3.4rem] leading-none drop-shadow-[0_6px_14px_rgba(0,0,0,0.45)]">
                      🍷
                    </span>
                  )}
                </div>

                {/* Détails */}
                <div className="flex-1 min-w-0 py-0.5">
                  <div className="text-muted text-[0.82rem] truncate">{l.ref.domaine_nom}</div>
                  <div className="font-serif text-[1.15rem] text-ink font-semibold leading-tight line-clamp-2">
                    {c?.classification || l.ref.cuvee_nom}
                  </div>
                  {lieu && <div className="text-muted text-[0.8rem] mt-1.5 truncate">{lieu}</div>}
                  <div className="flex items-center justify-between gap-2 mt-2">
                    <StatutBadge statut={l.ref.statut} className="text-[0.78rem] px-2.5 py-1 font-medium" />
                    <span className="text-muted text-[0.78rem] whitespace-nowrap">75cl</span>
                  </div>
                  <div className="flex items-center gap-3 mt-2 text-[0.78rem] text-muted">
                    <span>
                      Prix d'achat :{' '}
                      <span className="text-ink">{formatPrix(l.ref.prix_achat)}</span>
                    </span>
                    <span>
                      Valeur : {valeur ? <span className="text-ink">{valeur}</span> : <OeniLock />}
                    </span>
                  </div>
                  <span className="sr-only">{COULEUR_LABELS[l.couleur]}</span>
                </div>
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
