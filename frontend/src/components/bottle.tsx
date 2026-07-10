import { useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import { useData } from '../data'
import { useToast } from '../toast'
import { Sheet, StatutBadge, inputCls, labelCls, primaryCls } from '../ui'
import {
  COULEUR_LABELS,
  COULEUR_VARS,
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

/* Silhouette de bouteille colorée (liste « Mes vins », accueil, panneaux). */
export function BottleBar({
  couleur,
  className = 'w-3 h-9',
}: {
  couleur: Couleur
  className?: string
}) {
  return (
    <span
      aria-hidden="true"
      className={`shrink-0 rounded-[2px_2px_5px_5px] ${className}`}
      style={{ background: COULEUR_VARS[couleur] }}
    />
  )
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
    const emptyCls = 'rounded-[6px] border-[1.5px] border-dashed border-line'
    // Une case vide devient cliquable dès qu'on fournit un onClick (placement
    // case par case) ; sinon elle reste un simple repère visuel.
    if (onClick) {
      return (
        <button
          onClick={onClick}
          title={title}
          style={{ width: size, height: size }}
          className={`${emptyCls} active:scale-95 hover:border-gold/60 transition`}
        />
      )
    }
    return <div style={{ width: size, height: size }} className={emptyCls} />
  }
  // Alvéole pleine : aplat de la couleur du vin ; le statut se signale par un
  // liseré (or « à boire », alerte « dépassé »), comme le code couleur global.
  const ring =
    statut === 'A_BOIRE' ? 'var(--color-ok)' : statut === 'DEPASSE' ? 'var(--color-alerte)' : 'transparent'
  return (
    <button
      onClick={onClick}
      title={title}
      style={{ width: size, height: size, background: COULEUR_VARS[couleur], borderColor: ring }}
      className="rounded-[6px] border-2 active:scale-95 transition"
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
            <span className="text-xs px-2.5 py-1 rounded-full border border-line text-ink inline-flex items-center gap-1.5">
              <BottleBar couleur={cuveeColor(b)} className="w-1.5 h-3.5" />
              {COULEUR_LABELS[cuveeColor(b)]}
            </span>
            <StatutBadge statut={b.statut} />
            {formatApogee(b.apogee_debut_effectif, b.apogee_fin_effectif) && (
              <span className="text-xs px-2.5 py-1 rounded-full border border-line text-muted">
                {formatApogee(b.apogee_debut_effectif, b.apogee_fin_effectif)}
              </span>
            )}
            <span className="text-xs px-2.5 py-1 rounded-full border border-line text-muted">
              × {b.quantite} en stock
            </span>
            <span className="text-xs px-2.5 py-1 rounded-full border border-line text-muted">
              {b.emplacement_chemin ? `📍 ${b.emplacement_chemin}` : 'non placée'}
            </span>
          </div>

          <label className={labelCls}>Consommer</label>
          <div className="flex items-center gap-3.5">
            <button
              onClick={() => setQte((q) => Math.max(1, q - 1))}
              className="w-11 h-11 rounded-full border border-line bg-surface text-2xl"
            >
              −
            </button>
            <span className="font-serif text-2xl min-w-8 text-center">{qte}</span>
            <button
              onClick={() => setQte((q) => Math.min(b.quantite, q + 1))}
              className="w-11 h-11 rounded-full border border-line bg-surface text-2xl"
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
          <button onClick={consume} className={primaryCls}>
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
