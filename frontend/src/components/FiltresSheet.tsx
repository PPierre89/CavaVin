import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Chip, IconButton, wineFill } from '../ui'
import { COULEUR_LABELS, type Couleur } from '../types'
import {
  cloneFiltres,
  compteFiltres,
  filtresVides,
  matchFiltres,
  type Filtres,
  type Ligne,
} from '../filtres'

/* ------------------------------------------------------------------ *
 *  « Filtres » — panneau plein écran de filtrage de la vinothèque.
 *  Reprend la maquette : type de vin, phase de vieillissement (Oeni+),
 *  valeur (curseur min/max), pays, millésimes, taille et régions, avec
 *  un CTA « Voir les N bouteilles » qui applique la sélection.
 * ------------------------------------------------------------------ */

/* Types de vin proposés (colonne fixe, comme sur la maquette). */
const TYPES: Couleur[] = ['ROUGE', 'BLANC', 'ROSE', 'BULLES', 'AUTRE']

/* Phases de vieillissement — fonctionnalité premium Oeni+ (verrouillée). */
const PHASES = ['Jeunesse', 'Maturité', 'Apogée', 'Déclin']

function FSection({
  emoji,
  title,
  locked,
  children,
}: {
  emoji: string
  title: string
  locked?: boolean
  children: ReactNode
}) {
  return (
    <section className="mt-6">
      <h3 className="flex items-center gap-2 font-serif text-[1.35rem] text-ink mb-3">
        <span className="text-[1.1rem]">{emoji}</span>
        {title}
        {locked && <span className="text-[0.95rem]">👑</span>}
      </h3>
      {children}
    </section>
  )
}

/* Rangée de chips à défilement horizontal. */
function ChipRow({ children }: { children: ReactNode }) {
  return <div className="flex gap-2 overflow-x-auto no-scrollbar pb-1">{children}</div>
}

/* Curseur double min/max. */
function DualRange({
  min,
  max,
  value,
  onChange,
}: {
  min: number
  max: number
  value: [number, number]
  onChange: (v: [number, number]) => void
}) {
  const [lo, hi] = value
  const span = max - min || 1
  const pct = (v: number) => ((v - min) / span) * 100
  return (
    <div className="px-1">
      <div className="relative h-8 flex items-center">
        <div className="absolute left-0 right-0 h-2 rounded-full bg-surface-2" />
        <div
          className="absolute h-2 rounded-full bg-gold"
          style={{ left: `${pct(lo)}%`, right: `${100 - pct(hi)}%` }}
        />
        <input
          type="range"
          className="dual-range"
          aria-label="Valeur minimale"
          min={min}
          max={max}
          value={lo}
          onChange={(e) => onChange([Math.min(Number(e.target.value), hi), hi])}
        />
        <input
          type="range"
          className="dual-range"
          aria-label="Valeur maximale"
          min={min}
          max={max}
          value={hi}
          onChange={(e) => onChange([lo, Math.max(Number(e.target.value), lo)])}
        />
      </div>
      <div className="flex justify-between mt-1.5">
        <span className={`px-3.5 py-1 rounded-full text-sm font-semibold text-ink-bright ${wineFill}`}>
          {lo}
        </span>
        <span className={`px-3.5 py-1 rounded-full text-sm font-semibold text-ink-bright ${wineFill}`}>
          {hi}
        </span>
      </div>
    </div>
  )
}

export function FiltresSheet({
  open,
  onClose,
  onApply,
  initial,
  lignes,
  bounds,
}: {
  open: boolean
  onClose: () => void
  onApply: (f: Filtres) => void
  initial: Filtres
  lignes: Ligne[]
  bounds: [number, number]
}) {
  const [draft, setDraft] = useState<Filtres>(() => cloneFiltres(initial))

  // À l'ouverture, on repart de la sélection déjà appliquée.
  useEffect(() => {
    if (open) setDraft(cloneFiltres(initial))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [open, onClose])

  // Options disponibles, dérivées des vins réellement présents.
  const options = useMemo(() => {
    const pays = new Set<string>()
    const regions = new Set<string>()
    const millesimes = new Set<string>()
    for (const l of lignes) {
      if (l.ref.pays) pays.add(l.ref.pays)
      if (l.ref.region) regions.add(l.ref.region)
      millesimes.add(String(l.ref.millesime ?? 'NM'))
    }
    const millListe = [...millesimes].sort((a, b) => {
      if (a === 'NM') return 1
      if (b === 'NM') return -1
      return Number(b) - Number(a)
    })
    return {
      pays: [...pays].sort(),
      regions: [...regions].sort(),
      millesimes: millListe,
    }
  }, [lignes])

  const hasValeur = bounds[1] > bounds[0]

  const nbBouteilles = useMemo(
    () => lignes.filter((l) => matchFiltres(l, draft, bounds)).reduce((n, l) => n + l.quantite, 0),
    [lignes, draft, bounds],
  )
  const nbFiltres = compteFiltres(draft, bounds)

  const toggle = (cle: keyof Filtres, v: string) =>
    setDraft((prev) => {
      const set = new Set(prev[cle] as Set<string>)
      if (set.has(v)) set.delete(v)
      else set.add(v)
      return { ...prev, [cle]: set }
    })

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-bg">
      {/* ---------- En-tête ---------- */}
      <div
        className="bar-top border-b border-line sticky top-0 z-10 flex items-center justify-between px-4 pb-3"
        style={{ paddingTop: 'calc(12px + env(safe-area-inset-top))' }}
      >
        <h1 className="font-serif text-[1.9rem] text-ink m-0">Filtres</h1>
        <div className="flex items-center gap-3">
          {nbFiltres > 0 && (
            <button
              onClick={() => setDraft(filtresVides())}
              className="flex items-center gap-1.5 text-sm text-muted"
            >
              {nbFiltres} filtre{nbFiltres > 1 ? 's' : ''} <span className="text-alerte">🚫</span>
            </button>
          )}
          <IconButton round={false} onClick={onClose} aria-label="Fermer" className="text-ink text-xl">
            ✕
          </IconButton>
        </div>
      </div>

      <div
        className="max-w-[640px] mx-auto px-4"
        style={{ paddingBottom: 'calc(104px + env(safe-area-inset-bottom))' }}
      >
        {/* ---------- Type de vin ---------- */}
        <FSection emoji="🍷" title="Type de vin">
          <ChipRow>
            {TYPES.map((c) => (
              <Chip
                key={c}
                active={draft.couleurs.has(c)}
                onClick={() => toggle('couleurs', c)}
              >
                {COULEUR_LABELS[c]}
              </Chip>
            ))}
          </ChipRow>
        </FSection>

        {/* ---------- Phase de vieillissement (Oeni+) ---------- */}
        <FSection emoji="🍾" title="Phase de vieillissement" locked>
          <ChipRow>
            {PHASES.map((p) => (
              <Chip key={p} disabled>
                {p}
              </Chip>
            ))}
          </ChipRow>
        </FSection>

        {/* ---------- Valeur des vins ---------- */}
        {hasValeur && (
          <FSection emoji="📈" title="Valeur des vins">
            <div className="text-muted text-sm -mt-2 mb-3">(en EUR)</div>
            <DualRange
              min={bounds[0]}
              max={bounds[1]}
              value={draft.valeur ?? bounds}
              onChange={(v) => setDraft((prev) => ({ ...prev, valeur: v }))}
            />
          </FSection>
        )}

        {/* ---------- Pays ---------- */}
        {options.pays.length > 0 && (
          <FSection emoji="🌍" title="Pays">
            <ChipRow>
              {options.pays.map((p) => (
                <Chip key={p} active={draft.pays.has(p)} onClick={() => toggle('pays', p)}>
                  {p}
                </Chip>
              ))}
            </ChipRow>
          </FSection>
        )}

        {/* ---------- Millésimes ---------- */}
        <FSection emoji="🕐" title="Millésimes">
          <ChipRow>
            {options.millesimes.map((m) => (
              <Chip key={m} active={draft.millesimes.has(m)} onClick={() => toggle('millesimes', m)}>
                {m === 'NM' ? 'N.M.' : m}
              </Chip>
            ))}
          </ChipRow>
        </FSection>

        {/* ---------- Taille de bouteilles ---------- */}
        <FSection emoji="📏" title="Taille de bouteilles">
          <ChipRow>
            <Chip
              active={draft.tailles.has('Standard')}
              onClick={() => toggle('tailles', 'Standard')}
            >
              Standard
            </Chip>
          </ChipRow>
        </FSection>

        {/* ---------- Régions ---------- */}
        {options.regions.length > 0 && (
          <FSection emoji="📍" title="Régions">
            <ChipRow>
              {options.regions.map((r) => (
                <Chip key={r} active={draft.regions.has(r)} onClick={() => toggle('regions', r)}>
                  {r}
                </Chip>
              ))}
            </ChipRow>
          </FSection>
        )}
      </div>

      {/* ---------- CTA appliquer ---------- */}
      <div
        className="bar-bottom fixed bottom-0 left-0 right-0 z-10 px-4 pt-3"
        style={{ paddingBottom: 'calc(16px + env(safe-area-inset-bottom))' }}
      >
        <button
          onClick={() => onApply(draft)}
          className={`w-full py-3.5 rounded-full font-bold text-ink-bright ${wineFill} active:scale-[0.985] transition`}
        >
          Voir les {nbBouteilles} bouteille{nbBouteilles > 1 ? 's' : ''}
        </button>
      </div>
    </div>
  )
}
