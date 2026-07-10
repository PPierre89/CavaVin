import { useCallback, useMemo, useRef, useState } from 'react'
import { api, errMsg } from '../api'
import { useData } from '../data'
import { useToast } from '../toast'
import { Card, Chip, ConfirmSheet } from '../ui'
import { BottleSheet, RackGrid, Slot } from '../components/bottle'
import { PlacementSheet, type CellRef } from '../components/PlacementSheet'
import { FicheVin } from '../components/FicheVin'
import { TYPE_LABELS, type Bouteille, type Emplacement } from '../types'

const MAX_SLOTS = 96
// Seuil (px) au-delà duquel un appui devient un glisser — en deçà, c'est un tap.
const DRAG_SEUIL = 7

function TrashIcon({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m2 0-.7 12a2 2 0 0 1-2 1.9H8.7a2 2 0 0 1-2-1.9L6 7"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** Origine d'un glisser : une case déjà rangée (pour la déplacer). */
interface DragOrigin {
  rangementId: number
  empId: number
  case: number
}
interface Pending {
  bouteille: Bouteille
  origin?: DragOrigin
  startX: number
  startY: number
  active: boolean
}

type Suppression =
  | { kind: 'cave'; id: number; nom: string }
  | { kind: 'emplacement'; id: number; nom: string }

export default function CaveScreen({ onAdd }: { onAdd: (seg: 'cave' | 'emplacement') => void }) {
  const {
    caves,
    caveId,
    setCaveId,
    emplacements,
    bouteilles,
    rangements,
    cuveeColor,
    refresh,
    loadCaves,
  } = useData()
  const toast = useToast()
  const [selected, setSelected] = useState<Bouteille | null>(null)
  const [fiche, setFiche] = useState<Bouteille | null>(null)
  const [cell, setCell] = useState<CellRef | null>(null)
  const [aSupprimer, setASupprimer] = useState<Suppression | null>(null)
  // Glisser en cours : bouteille fantôme suivant le pointeur + case survolée.
  const [drag, setDrag] = useState<{ bouteille: Bouteille; x: number; y: number } | null>(null)
  const [hover, setHover] = useState<{ empId: number; case: number } | null>(null)

  const active = bouteilles.filter((b) => b.quantite > 0)
  // Jauge de remplissage de la cave courante : bouteilles rangées / capacité
  // totale (occupation_actuelle ne compte que les bouteilles directes d'un
  // emplacement, la somme ne double-compte donc rien).
  const remplissage = useMemo(() => {
    const occ = emplacements.reduce((n, e) => n + e.occupation_actuelle, 0)
    const cap = emplacements.reduce(
      (n, e) =>
        n + (e.nb_colonnes && e.nb_rangees ? e.nb_colonnes * e.nb_rangees : (e.capacite ?? 0)),
      0,
    )
    return { occ, cap }
  }, [emplacements])

  const bottleById = useMemo(() => new Map(bouteilles.map((b) => [b.id, b])), [bouteilles])
  // Nombre d'unités déjà rangées dans une case précise, par ligne de stock.
  const placed = useMemo(() => {
    const m = new Map<number, number>()
    rangements.forEach((r) => m.set(r.bouteille, (m.get(r.bouteille) || 0) + 1))
    return m
  }, [rangements])
  // Table case → rangement, par emplacement, pour peupler chaque grille.
  const rangByCell = useMemo(() => {
    const m = new Map<number, Map<number, (typeof rangements)[number]>>()
    rangements.forEach((r) => {
      let cases = m.get(r.emplacement)
      if (!cases) m.set(r.emplacement, (cases = new Map()))
      cases.set(r.case, r)
    })
    return m
  }, [rangements])

  const isGridEmp = (emp?: Emplacement) => !!(emp?.nb_colonnes && emp?.nb_rangees)
  const empById = useMemo(() => new Map(emplacements.map((e) => [e.id, e])), [emplacements])
  // Unités restant à ranger dans une case : tout le stock si non placé, le
  // reliquat si la ligne est dans une grille, rien si posée dans un bac sans grille.
  const aRanger = (b: Bouteille) => {
    if (!b.emplacement) return b.quantite
    if (isGridEmp(empById.get(b.emplacement))) return b.quantite - (placed.get(b.id) || 0)
    return 0
  }

  // ---- Glisser-déposer (pointer events : compatible tactile + souris) ----
  // Les handlers restent stables ; ils lisent l'état courant via des refs.
  const pending = useRef<Pending | null>(null)
  const suppressClick = useRef(false)
  const dropData = useRef({ rangByCell, empById })
  dropData.current = { rangByCell, empById }
  const doRefresh = useRef(refresh)
  doRefresh.current = refresh
  const doToast = useRef(toast)
  doToast.current = toast

  const finishDrop = useCallback(async (p: Pending, empId: number, caseIdx: number) => {
    const { rangByCell: rbc, empById: ebi } = dropData.current
    const emp = ebi.get(empId)
    if (!emp) return
    const occupant = rbc.get(empId)?.get(caseIdx)
    // Reposée sur sa propre case : rien à faire.
    if (p.origin && p.origin.empId === empId && p.origin.case === caseIdx) return
    if (occupant && !(p.origin && occupant.id === p.origin.rangementId)) {
      doToast.current('Cette case est déjà occupée.', 'err')
      return
    }
    try {
      if (!p.origin) {
        await api('POST', '/api/rangements/', {
          bouteille: p.bouteille.id,
          emplacement: empId,
          case: caseIdx,
        })
      } else if (p.origin.empId === empId) {
        await api('PATCH', `/api/rangements/${p.origin.rangementId}/`, { case: caseIdx })
      } else {
        doToast.current("Déplacement d'une grille à l'autre non pris en charge.", 'err')
        return
      }
      doToast.current('Bouteille rangée. 🍷', 'ok')
      await doRefresh.current()
    } catch (e) {
      doToast.current(errMsg(e, 'Placement impossible.'), 'err')
    }
  }, [])

  const cellUnder = (x: number, y: number) => {
    const el = document.elementFromPoint(x, y) as HTMLElement | null
    return el?.closest<HTMLElement>('[data-cell]') ?? null
  }

  const onMove = useCallback((e: PointerEvent) => {
    const p = pending.current
    if (!p) return
    if (!p.active) {
      if (Math.hypot(e.clientX - p.startX, e.clientY - p.startY) < DRAG_SEUIL) return
      p.active = true
    }
    const target = cellUnder(e.clientX, e.clientY)
    setHover(
      target
        ? { empId: Number(target.dataset.emp), case: Number(target.dataset.case) }
        : null,
    )
    setDrag({ bouteille: p.bouteille, x: e.clientX, y: e.clientY })
  }, [])

  const onUp = useCallback(
    (e: PointerEvent) => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      const p = pending.current
      pending.current = null
      setDrag(null)
      setHover(null)
      if (!p || !p.active) return
      // Un vrai glisser vient d'avoir lieu : neutraliser le clic qui suit.
      suppressClick.current = true
      setTimeout(() => (suppressClick.current = false), 150)
      const target = cellUnder(e.clientX, e.clientY)
      if (target) {
        void finishDrop(p, Number(target.dataset.emp), Number(target.dataset.case))
      }
    },
    [onMove, finishDrop],
  )

  const startDrag = useCallback(
    (e: React.PointerEvent, bouteille: Bouteille, origin?: DragOrigin) => {
      if (e.button !== 0 && e.pointerType === 'mouse') return
      pending.current = { bouteille, origin, startX: e.clientX, startY: e.clientY, active: false }
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
    },
    [onMove, onUp],
  )

  // Un clic (tap) qui suit immédiatement un glisser est ignoré.
  const tap = (fn: () => void) => () => {
    if (suppressClick.current) {
      suppressClick.current = false
      return
    }
    fn()
  }

  async function confirmerSuppression() {
    if (!aSupprimer) return
    try {
      if (aSupprimer.kind === 'cave') {
        await api('DELETE', `/api/caves/${aSupprimer.id}/`)
        await loadCaves()
        toast('Cave supprimée.', 'ok')
      } else {
        await api('DELETE', `/api/emplacements/${aSupprimer.id}/`)
        await refresh()
        toast('Emplacement supprimé.', 'ok')
      }
    } catch (e) {
      toast(errMsg(e, 'Suppression impossible.'), 'err')
    } finally {
      setASupprimer(null)
    }
  }

  const roots = emplacements.filter((e) => e.parent === null)
  const unplaced = active.filter((b) => aRanger(b) > 0)
  const bottlesFor = (id: number) => active.filter((b) => b.emplacement === id)
  const caveCourante = caves.find((c) => c.id === caveId)

  const Node = ({ emp }: { emp: Emplacement }) => {
    const children = emplacements.filter((e) => e.parent === emp.id)
    const isGrid = isGridEmp(emp)
    const cap = emp.capacite ?? (isGrid ? emp.nb_colonnes! * emp.nb_rangees! : null)

    let body: React.ReactNode = null
    if (isGrid) {
      // Grille interactive : chaque case est une cible de dépôt (data-cell) ;
      // on peut y taper (feuille de placement) ou y glisser une bouteille.
      const byCase = rangByCell.get(emp.id)
      body = (
        <div className="mt-3">
          <RackGrid
            cols={emp.nb_colonnes!}
            rows={emp.nb_rangees!}
            disposition={emp.disposition}
            renderSlot={(i) => {
              const r = byCase?.get(i)
              const b = r ? bottleById.get(r.bouteille) : undefined
              const cible = hover?.empId === emp.id && hover?.case === i
              return (
                <span
                  key={i}
                  data-cell="1"
                  data-emp={emp.id}
                  data-case={i}
                  style={{ touchAction: 'none' }}
                  onPointerDown={
                    b && r
                      ? (e) => startDrag(e, b, { rangementId: r.id, empId: emp.id, case: i })
                      : undefined
                  }
                  className={`rounded-[6px] transition ${
                    cible ? 'ring-2 ring-gold ring-offset-2 ring-offset-transparent' : ''
                  }`}
                >
                  {b ? (
                    <Slot
                      couleur={cuveeColor(b)}
                      statut={b.statut}
                      title={`${b.domaine_nom} — ${b.cuvee_nom} ${b.millesime || ''}`}
                      onClick={tap(() => setCell({ emp, index: i }))}
                    />
                  ) : (
                    <Slot empty onClick={tap(() => setCell({ emp, index: i }))} />
                  )}
                </span>
              )
            }}
          />
        </div>
      )
    } else {
      // Emplacement sans grille : rendu séquentiel simple (pas de case adressable).
      const bottles = bottlesFor(emp.id)
      const slots: React.ReactNode[] = []
      let rendus = 0
      for (const b of bottles) {
        for (let i = 0; i < b.quantite && rendus < MAX_SLOTS; i++, rendus++) {
          slots.push(
            <Slot
              key={`${b.id}-${i}`}
              couleur={cuveeColor(b)}
              statut={b.statut}
              title={`${b.domaine_nom} — ${b.cuvee_nom} ${b.millesime || ''}`}
              onClick={() => setFiche(b)}
            />,
          )
        }
      }
      if (cap) {
        for (let i = rendus; i < cap && rendus < MAX_SLOTS; i++, rendus++) {
          slots.push(<Slot key={`v-${i}`} empty />)
        }
      }
      body = slots.length > 0 ? <div className="flex flex-wrap gap-2.5 mt-3">{slots}</div> : null
    }

    return (
      <div className="glass rounded-card px-3.5 py-3 mb-3">
        <div className="flex justify-between items-start gap-2">
          <div>
            <span className="block text-muted text-[0.68rem] uppercase tracking-[0.1em] mb-0.5">
              {TYPE_LABELS[emp.type_emplacement]}
              {isGrid && ` · ${emp.nb_colonnes}×${emp.nb_rangees}`}
            </span>
            <span className="text-[0.92rem] font-semibold">{emp.nom}</span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-muted text-xs whitespace-nowrap">
              {emp.occupation_actuelle}
              {cap ? ` / ${cap}` : ''} btl
            </span>
            <button
              onClick={() => setASupprimer({ kind: 'emplacement', id: emp.id, nom: emp.nom })}
              className="text-muted/60 hover:text-alerte transition p-1 -mr-1"
              title="Supprimer l'emplacement"
              aria-label={`Supprimer l'emplacement ${emp.nom}`}
            >
              <TrashIcon />
            </button>
          </div>
        </div>
        {body}
        {children.map((c) => (
          <div key={c.id} className="mt-2.5">
            <Node emp={c} />
          </div>
        ))}
      </div>
    )
  }

  const Legend = () => (
    <div className="flex flex-wrap gap-x-3 gap-y-2 text-[0.68rem] text-muted mx-0.5 my-2">
      {[
        ['Rouge', 'var(--color-rouge)'],
        ['Blanc', 'var(--color-blanc)'],
        ['Rosé', 'var(--color-rose)'],
        ['Bulles', 'var(--color-bulles)'],
      ].map(([l, c]) => (
        <span key={l} className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-[2px]" style={{ background: c }} /> {l}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="w-2.5 h-2.5 rounded-[2px] border-[1.5px] border-dashed border-line" /> Vide
      </span>
      <span className="flex items-center gap-1.5">
        <span className="w-2.5 h-2.5 rounded-[2px] border-2 border-ok" /> À boire
      </span>
      <span className="flex items-center gap-1.5">
        <span className="w-2.5 h-2.5 rounded-[2px] border-2 border-alerte" /> Dépassé
      </span>
    </div>
  )

  return (
    <div>
      <h1 className="font-serif text-[1.75rem] text-ink-bright m-0 mb-3 px-0.5 font-medium">Cave</h1>

      <div className="flex gap-2 overflow-x-auto no-scrollbar pb-2.5">
        {caves.map((c) => (
          <Chip key={c.id} active={c.id === caveId} onClick={() => setCaveId(c.id)}>
            {c.nom}
          </Chip>
        ))}
        <Chip onClick={() => onAdd('cave')}>＋ cave</Chip>
        {caveId != null && (
          <button
            onClick={() => {
              const c = caves.find((x) => x.id === caveId)
              if (c) setASupprimer({ kind: 'cave', id: c.id, nom: c.nom })
            }}
            className="shrink-0 grid place-items-center w-10 h-10 rounded-full glass text-muted hover:text-alerte transition"
            title="Supprimer la cave courante"
            aria-label="Supprimer la cave courante"
          >
            <TrashIcon />
          </button>
        )}
      </div>

      {/* ---------- Jauge de remplissage de la cave courante ---------- */}
      {caveCourante && (
        <div className="glass rounded-[10px] px-3 py-2.5 mb-1 flex flex-col gap-1.5">
          <div className="flex justify-between items-center">
            <span className="text-[13px]">{caveCourante.nom}</span>
            <span className="text-xs text-muted">
              {remplissage.cap > 0
                ? `${remplissage.occ} / ${remplissage.cap}`
                : `${remplissage.occ} rangée${remplissage.occ > 1 ? 's' : ''}`}
            </span>
          </div>
          {remplissage.cap > 0 && (
            <div className="h-1.5 rounded-[3px]" style={{ background: 'oklch(38% 0.02 40)' }}>
              <div
                className="h-full rounded-[3px] bg-wine"
                style={{ width: `${Math.min(100, (remplissage.occ / remplissage.cap) * 100)}%` }}
              />
            </div>
          )}
        </div>
      )}

      <Legend />

      {caves.length === 0 ? (
        <Card>
          <p className="text-muted text-sm">
            Aucune cave —{' '}
            <button className="text-gold underline" onClick={() => onAdd('cave')}>
              créez votre première cave
            </button>
            .
          </p>
        </Card>
      ) : roots.length === 0 ? (
        <Card>
          <p className="text-muted text-sm">
            Aucun emplacement dans cette cave —{' '}
            <button className="text-gold underline" onClick={() => onAdd('emplacement')}>
              ajoutez une armoire ou un casier
            </button>
            .
          </p>
        </Card>
      ) : (
        roots.map((r) => <Node key={r.id} emp={r} />)
      )}

      {unplaced.length > 0 && (
        <Card>
          <h2 className="text-xs uppercase tracking-[0.1em] text-muted font-sans font-medium m-0 mb-1">
            En attente de placement
          </h2>
          <p className="text-muted text-xs mb-3">
            Glissez une bouteille sur une case, ou tapez une case vide d'une grille.
          </p>
          {unplaced.map((b) => (
            <div
              key={b.id}
              className="flex items-center gap-2.5 py-2.5 border-b border-line/40 last:border-0 text-sm"
            >
              <span
                style={{ touchAction: 'none' }}
                onPointerDown={(e) => startDrag(e, b)}
                className="cursor-grab active:cursor-grabbing"
                title="Glisser vers une case"
              >
                <Slot
                  couleur={cuveeColor(b)}
                  statut={b.statut}
                  size={30}
                  onClick={tap(() => setFiche(b))}
                />
              </span>
              <span className="flex-1 min-w-0 truncate">
                {b.domaine_nom} — {b.cuvee_nom} {b.millesime || ''} × {aRanger(b)}
              </span>
              <button
                onClick={() => setSelected(b)}
                className="px-3.5 py-2 rounded-xl border border-line text-muted text-sm"
              >
                Placer
              </button>
            </div>
          ))}
        </Card>
      )}

      {fiche && (
        <FicheVin
          bouteille={fiche}
          onClose={() => setFiche(null)}
          onOptions={(b) => setSelected(b)}
          onRetirer={(b) => setSelected(b)}
        />
      )}

      <PlacementSheet
        cell={cell}
        onClose={() => setCell(null)}
        onOpenFiche={(b) => {
          setCell(null)
          setFiche(b)
        }}
      />

      <BottleSheet b={selected} onClose={() => setSelected(null)} />

      <ConfirmSheet
        open={!!aSupprimer}
        title={
          aSupprimer?.kind === 'cave'
            ? `Supprimer « ${aSupprimer.nom} » ?`
            : `Supprimer « ${aSupprimer?.nom} » ?`
        }
        message={
          aSupprimer?.kind === 'cave'
            ? 'Tous ses emplacements et rangements seront supprimés. Les bouteilles concernées repasseront « non rangées » (le stock n’est pas supprimé).'
            : 'Ses sous-emplacements et rangements seront supprimés. Les bouteilles repasseront « non rangées » (le stock n’est pas supprimé).'
        }
        onConfirm={confirmerSuppression}
        onClose={() => setASupprimer(null)}
      />

      {/* Bouteille fantôme qui suit le pointeur pendant le glisser. */}
      {drag && (
        <div
          className="fixed z-[60] pointer-events-none opacity-90"
          style={{ left: drag.x, top: drag.y, transform: 'translate(-50%, -50%)' }}
        >
          <Slot couleur={cuveeColor(drag.bouteille)} statut={drag.bouteille.statut} size={42} />
        </div>
      )}
    </div>
  )
}
