import { useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import { useData } from '../data'
import { useToast } from '../toast'
import { Sheet, StatutBadge, inputCls, labelCls, primaryCls } from '../ui'
import {
  COULEUR_LABELS,
  type Bouteille,
  type Couleur,
  type Disposition,
  type Statut,
} from '../types'

/** Formate une fenêtre d'apogée (« 2024-2035 », « dès 2024 », « avant 2035 »). */
function formatApogee(debut: number | null, fin: number | null): string | null {
  if (debut && fin) return `${debut}–${fin}`
  if (debut) return `dès ${debut}`
  if (fin) return `avant ${fin}`
  return null
}

const SLOT_BG: Record<Couleur, string> = {
  ROUGE: 'radial-gradient(circle at 32% 30%, #a84a62, #8e2f45)',
  BLANC: 'radial-gradient(circle at 32% 30%, #f2e6b8, #e6d491)',
  ROSE: 'radial-gradient(circle at 32% 30%, #f5c3d1, #e8a0b4)',
  BULLES: 'radial-gradient(circle at 32% 30%, #ecd07a, #d9b653)',
  AUTRE: 'radial-gradient(circle at 32% 30%, #a6acb8, #9aa0ab)',
}

export function Slot({
  couleur,
  statut,
  empty,
  size = 38,
  title,
  onClick,
}: {
  couleur?: Couleur
  statut?: Statut
  empty?: boolean
  size?: number
  title?: string
  onClick?: () => void
}) {
  if (empty || !couleur) {
    const emptyCls = 'rounded-full border-2 border-dashed border-gold/15'
    // Une case vide devient cliquable dès qu'on fournit un onClick (placement
    // case par case) ; sinon elle reste un simple repère visuel.
    if (onClick) {
      return (
        <button
          onClick={onClick}
          title={title}
          style={{ width: size, height: size }}
          className={`${emptyCls} active:scale-95 hover:border-gold/40 transition`}
        />
      )
    }
    return <div style={{ width: size, height: size }} className={emptyCls} />
  }
  const ring =
    statut === 'A_BOIRE' ? 'var(--color-ok)' : statut === 'DEPASSE' ? 'var(--color-alerte)' : 'transparent'
  const light = couleur === 'BLANC' || couleur === 'ROSE' || couleur === 'BULLES'
  return (
    <button
      onClick={onClick}
      title={title}
      style={{ width: size, height: size, background: SLOT_BG[couleur], borderColor: ring, color: light ? '#5b4b1f' : '#fff' }}
      className="rounded-full border-[3px] shadow-[inset_0_-3px_6px_rgba(0,0,0,0.35)] active:scale-95 transition"
    />
  )
}

/**
 * Rendu d'une rangée en grille : `cols` bouteilles de large sur `rows` niveaux.
 * La disposition décalée fait glisser une rangée sur deux d'une demi-bouteille,
 * pour reproduire l'empilement en quinconce d'un vrai casier à vin.
 */
export function RackGrid({
  cols,
  rows,
  disposition = 'ALIGNE',
  size = 38,
  gap = 10,
  renderSlot,
}: {
  cols: number
  rows: number
  disposition?: Disposition
  size?: number
  gap?: number
  renderSlot: (index: number) => React.ReactNode
}) {
  const half = (size + gap) / 2
  const rowEls: React.ReactNode[] = []
  for (let r = 0; r < rows; r++) {
    const slots: React.ReactNode[] = []
    for (let c = 0; c < cols; c++) slots.push(renderSlot(r * cols + c))
    let marginLeft = 0
    if (disposition === 'DECALE_DROITE') marginLeft = r % 2 === 1 ? half : 0
    else if (disposition === 'DECALE_GAUCHE') marginLeft = r % 2 === 0 ? half : 0
    rowEls.push(
      <div key={r} className="flex w-max" style={{ gap, marginLeft }}>
        {slots}
      </div>,
    )
  }
  return (
    <div className="overflow-x-auto no-scrollbar">
      <div className="flex flex-col w-max" style={{ gap }}>
        {rowEls}
      </div>
    </div>
  )
}

export function BottleSheet({ b, onClose }: { b: Bouteille | null; onClose: () => void }) {
  const { emplacements, refresh, cuveeColor } = useData()
  const toast = useToast()
  const [qte, setQte] = useState(1)
  const [occasion, setOccasion] = useState('')
  const [target, setTarget] = useState('')

  useEffect(() => {
    if (b) {
      setQte(1)
      setOccasion('')
      setTarget(b.emplacement ? String(b.emplacement) : '')
    }
  }, [b])

  async function consume() {
    if (!b) return
    try {
      await api('POST', `/api/bouteilles/${b.id}/consommer/`, { quantite: qte, occasion })
      onClose()
      toast(`Santé ! ${qte} bouteille(s) consommée(s) 🥂`, 'ok')
      await refresh()
    } catch (e) {
      toast(errMsg(e, 'Impossible de consommer.'), 'err')
    }
  }

  async function move() {
    if (!b) return
    try {
      await api('PATCH', `/api/bouteilles/${b.id}/`, { emplacement: target ? parseInt(target, 10) : null })
      onClose()
      toast('Bouteille déplacée.', 'ok')
      await refresh()
    } catch (e) {
      toast(errMsg(e, 'Impossible de déplacer.'), 'err')
    }
  }

  return (
    <Sheet open={!!b} onClose={onClose}>
      {b && (
        <>
          <h3 className="font-serif text-[1.35rem] m-0">{b.cuvee_nom}</h3>
          <div className="text-muted text-sm mt-1">
            {b.domaine_nom}
            {b.millesime ? ` · ${b.millesime}` : ''}
          </div>
          <div className="flex flex-wrap gap-2 mt-3 mb-1">
            <span className="text-xs px-2.5 py-1 rounded-full border border-gold-soft text-gold">
              {COULEUR_LABELS[cuveeColor(b)]}
            </span>
            <StatutBadge statut={b.statut} />
            {formatApogee(b.apogee_debut_effectif, b.apogee_fin_effectif) && (
              <span className="text-xs px-2.5 py-1 rounded-full border border-gold/15 text-muted">
                🍷 {formatApogee(b.apogee_debut_effectif, b.apogee_fin_effectif)}
              </span>
            )}
            <span className="text-xs px-2.5 py-1 rounded-full border border-gold/15 text-muted">
              × {b.quantite} en stock
            </span>
            <span className="text-xs px-2.5 py-1 rounded-full border border-gold/15 text-muted">
              {b.emplacement_chemin ? `📍 ${b.emplacement_chemin}` : 'non placée'}
            </span>
          </div>

          <label className={labelCls}>Consommer</label>
          <div className="flex items-center gap-3.5">
            <button
              onClick={() => setQte((q) => Math.max(1, q - 1))}
              className="w-11 h-11 rounded-full border border-gold/15 bg-black/30 text-2xl"
            >
              −
            </button>
            <span className="font-serif text-2xl min-w-8 text-center">{qte}</span>
            <button
              onClick={() => setQte((q) => Math.min(b.quantite, q + 1))}
              className="w-11 h-11 rounded-full border border-gold/15 bg-black/30 text-2xl"
            >
              ＋
            </button>
          </div>
          <input
            className={`${inputCls} mt-3`}
            placeholder="Occasion (dîner, anniversaire…)"
            value={occasion}
            onChange={(e) => setOccasion(e.target.value)}
          />
          <button
            onClick={consume}
            className={`${primaryCls} from-rougevif to-wine-deep`}
          >
            🥂 Consommer
          </button>

          <label className={`${labelCls} mt-5`}>Déplacer vers</label>
          <select className={inputCls} value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">— non placée —</option>
            {emplacements.map((e) => (
              <option key={e.id} value={e.id}>
                {e.chemin}
              </option>
            ))}
          </select>
          <button onClick={move} className={primaryCls}>
            Déplacer
          </button>
        </>
      )}
    </Sheet>
  )
}
