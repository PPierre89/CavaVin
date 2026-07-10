import { useMemo } from 'react'
import { api, errMsg } from '../api'
import { useData } from '../data'
import { useToast } from '../toast'
import { Sheet, labelCls } from '../ui'
import { Slot } from './bottle'
import type { Bouteille, Emplacement } from '../types'

/** Case ciblée dans une grille : l'emplacement + l'index 0-based de la case. */
export interface CellRef {
  emp: Emplacement
  index: number
}

/**
 * Feuille de placement « case par case ». Sur une case vide, propose les
 * bouteilles encore à ranger ; sur une case occupée, permet d'ouvrir la fiche
 * ou de libérer la case.
 */
export function PlacementSheet({
  cell,
  onClose,
  onOpenFiche,
}: {
  cell: CellRef | null
  onClose: () => void
  onOpenFiche: (b: Bouteille) => void
}) {
  const { bouteilles, rangements, emplacements, cuveeColor, refresh } = useData()
  const toast = useToast()

  const placed = useMemo(() => {
    const m = new Map<number, number>()
    rangements.forEach((r) => m.set(r.bouteille, (m.get(r.bouteille) || 0) + 1))
    return m
  }, [rangements])

  const isGridEmp = (id: number | null) => {
    if (id == null) return false
    const e = emplacements.find((x) => x.id === id)
    return !!(e?.nb_colonnes && e?.nb_rangees)
  }

  // Unités d'une ligne qui restent à ranger dans une case précise.
  const aRanger = (b: Bouteille) => {
    if (!b.emplacement) return b.quantite
    if (isGridEmp(b.emplacement)) return b.quantite - (placed.get(b.id) || 0)
    return 0
  }

  const rang = cell
    ? rangements.find((r) => r.emplacement === cell.emp.id && r.case === cell.index)
    : undefined
  const occupant = rang ? bouteilles.find((b) => b.id === rang.bouteille) : null

  // Candidates : lignes non entièrement rangées, libres ou déjà dans cette grille.
  const candidates = cell
    ? bouteilles.filter(
        (b) =>
          b.quantite > 0 &&
          aRanger(b) > 0 &&
          (!b.emplacement || b.emplacement === cell.emp.id),
      )
    : []

  async function place(b: Bouteille) {
    if (!cell) return
    try {
      await api('POST', '/api/rangements/', {
        bouteille: b.id,
        emplacement: cell.emp.id,
        case: cell.index,
      })
      onClose()
      toast('Bouteille rangée. 🍷', 'ok')
      await refresh()
    } catch (e) {
      toast(errMsg(e, 'Impossible de ranger la bouteille.'), 'err')
    }
  }

  async function liberer() {
    if (!rang) return
    try {
      await api('DELETE', `/api/rangements/${rang.id}/`)
      onClose()
      toast('Case libérée.', 'ok')
      await refresh()
    } catch (e) {
      toast(errMsg(e, 'Impossible de libérer la case.'), 'err')
    }
  }

  let caseLabel = ''
  if (cell) {
    const cols = cell.emp.nb_colonnes
    caseLabel = cols
      ? `Rangée ${Math.floor(cell.index / cols) + 1}, place ${(cell.index % cols) + 1}`
      : `Case ${cell.index + 1}`
  }

  const ghost =
    'w-full mt-4 py-3 rounded-xl border border-gold/15 text-muted active:scale-[0.985] transition'
  const danger =
    'w-full mt-2.5 py-3 rounded-xl border border-alerte/40 text-alerte active:scale-[0.985] transition'

  return (
    <Sheet open={!!cell} onClose={onClose}>
      {cell && (
        <>
          <h3 className="font-serif text-[1.35rem] m-0">{cell.emp.nom}</h3>
          <div className="text-muted text-sm mt-1">{caseLabel}</div>

          {occupant ? (
            <>
              <div className="flex items-center gap-2.5 mt-4">
                <Slot couleur={cuveeColor(occupant)} statut={occupant.statut} size={34} />
                <span className="flex-1 min-w-0">
                  <span className="block truncate">
                    {occupant.domaine_nom} — {occupant.cuvee_nom}
                  </span>
                  {occupant.millesime && (
                    <span className="text-muted text-xs">{occupant.millesime}</span>
                  )}
                </span>
              </div>
              <button onClick={() => onOpenFiche(occupant)} className={ghost}>
                Voir la fiche
              </button>
              <button onClick={liberer} className={danger}>
                Libérer la case
              </button>
            </>
          ) : candidates.length ? (
            <>
              <label className={labelCls}>Ranger ici</label>
              <div className="max-h-[46vh] overflow-y-auto -mx-1 px-1">
                {candidates.map((b) => (
                  <button
                    key={b.id}
                    onClick={() => place(b)}
                    className="w-full flex items-center gap-2.5 py-2.5 border-b border-gold/10 last:border-0 text-left text-sm"
                  >
                    <Slot couleur={cuveeColor(b)} statut={b.statut} size={30} />
                    <span className="flex-1 min-w-0 truncate">
                      {b.domaine_nom} — {b.cuvee_nom} {b.millesime || ''}
                    </span>
                    <span className="text-muted text-xs whitespace-nowrap">
                      {aRanger(b)} à ranger
                    </span>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <p className="text-muted text-sm mt-4">
              Aucune bouteille en attente de placement. Ajoutez des bouteilles, ou libérez une
              case déjà occupée.
            </p>
          )}
        </>
      )}
    </Sheet>
  )
}
