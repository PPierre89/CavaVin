import { useMemo, useState } from 'react'
import { useData } from '../data'
import { Card, Chip } from '../ui'
import { BottleSheet, RackGrid, Slot } from '../components/bottle'
import { FicheVin } from '../components/FicheVin'
import { TYPE_LABELS, type Bouteille, type Emplacement } from '../types'

const MAX_SLOTS = 96

export default function CaveScreen({ onAdd }: { onAdd: (seg: 'cave' | 'emplacement') => void }) {
  const { caves, caveId, setCaveId, emplacements, bouteilles, cuveeColor } = useData()
  const [selected, setSelected] = useState<Bouteille | null>(null)
  const [fiche, setFiche] = useState<Bouteille | null>(null)

  const active = bouteilles.filter((b) => b.quantite > 0)
  const stats = useMemo(() => {
    const parCouleur: Record<string, number> = {}
    let total = 0
    active.forEach((b) => {
      total += b.quantite
      const c = cuveeColor(b)
      parCouleur[c] = (parCouleur[c] || 0) + b.quantite
    })
    return {
      total,
      rouge: parCouleur.ROUGE || 0,
      blanc: parCouleur.BLANC || 0,
      autres: (parCouleur.BULLES || 0) + (parCouleur.ROSE || 0) + (parCouleur.AUTRE || 0),
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bouteilles, cuveeColor])

  const roots = emplacements.filter((e) => e.parent === null)
  const unplaced = active.filter((b) => !b.emplacement)
  const bottlesFor = (id: number) => active.filter((b) => b.emplacement === id)

  const StatBox = ({ n, l, color }: { n: number; l: string; color?: string }) => (
    <div className="glass rounded-2xl py-3 px-1.5 text-center">
      <div className="font-serif font-semibold text-[1.55rem]" style={color ? { color } : undefined}>
        {n}
      </div>
      <div className="text-[0.62rem] text-muted uppercase tracking-wide mt-0.5">{l}</div>
    </div>
  )

  const Node = ({ emp }: { emp: Emplacement }) => {
    const bottles = bottlesFor(emp.id)
    const children = emplacements.filter((e) => e.parent === emp.id)
    const isGrid = !!(emp.nb_colonnes && emp.nb_rangees)
    const cap = emp.capacite ?? (isGrid ? emp.nb_colonnes! * emp.nb_rangees! : null)
    // Développe le stock (quantité) en emplacements individuels, plafonnés pour
    // l'affichage. Chaque case pleine reste cliquable vers la fiche du vin.
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
    return (
      <div className="glass rounded-card px-3.5 py-3 mb-3">
        <div className="flex justify-between items-baseline gap-2">
          <div>
            <span className="block text-gold text-[0.68rem] uppercase tracking-wider mb-0.5">
              {TYPE_LABELS[emp.type_emplacement]}
              {isGrid && ` · ${emp.nb_colonnes}×${emp.nb_rangees}`}
            </span>
            <span className="text-[0.92rem] font-semibold">{emp.nom}</span>
          </div>
          <span className="text-muted text-xs whitespace-nowrap">
            {emp.occupation_actuelle}
            {cap ? ` / ${cap}` : ''} btl
          </span>
        </div>
        {slots.length > 0 &&
          (isGrid ? (
            <div className="mt-3">
              <RackGrid
                cols={emp.nb_colonnes!}
                rows={emp.nb_rangees!}
                disposition={emp.disposition}
                renderSlot={(i) => slots[i] ?? <Slot key={`pad-${i}`} empty />}
              />
            </div>
          ) : (
            <div className="flex flex-wrap gap-2.5 mt-3">{slots}</div>
          ))}
        {children.map((c) => (
          <div key={c.id} className="mt-2.5">
            <Node emp={c} />
          </div>
        ))}
      </div>
    )
  }

  const Legend = () => (
    <div className="flex flex-wrap gap-x-3.5 gap-y-2.5 text-[0.72rem] text-muted mx-0.5 my-2">
      {[
        ['Rouge', 'var(--color-rouge)'],
        ['Blanc', 'var(--color-blanc)'],
        ['Rosé', 'var(--color-rose)'],
        ['Bulles', 'var(--color-bulles)'],
      ].map(([l, c]) => (
        <span key={l} className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full" style={{ background: c }} /> {l}
        </span>
      ))}
      <span className="flex items-center gap-1">
        <span className="w-2.5 h-2.5 rounded-full border-2 border-ok" /> À boire
      </span>
      <span className="flex items-center gap-1">
        <span className="w-2.5 h-2.5 rounded-full border-2 border-alerte" /> Dépassé
      </span>
    </div>
  )

  return (
    <div>
      <div className="grid grid-cols-4 gap-2 mb-3.5">
        <StatBox n={stats.total} l="Bouteilles" />
        <StatBox n={stats.rouge} l="Rouges" color="var(--color-rougevif)" />
        <StatBox n={stats.blanc} l="Blancs" color="var(--color-blanc)" />
        <StatBox n={stats.autres} l="Autres" color="var(--color-bulles)" />
      </div>

      <div className="flex gap-2 overflow-x-auto no-scrollbar pb-2.5">
        {caves.map((c) => (
          <Chip key={c.id} active={c.id === caveId} onClick={() => setCaveId(c.id)}>
            {c.nom}
          </Chip>
        ))}
        <Chip onClick={() => onAdd('cave')}>＋ cave</Chip>
      </div>

      <Legend />

      {caves.length === 0 ? (
        <Card>
          <p className="text-muted text-sm">
            Aucune cave. Créez votre première cave depuis l'onglet Ajouter.
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
          <h2 className="font-serif text-[1.12rem] text-gold m-0 mb-3">En attente de placement</h2>
          {unplaced.map((b) => (
            <div
              key={b.id}
              className="flex items-center gap-2.5 py-2.5 border-b border-gold/10 last:border-0 text-sm"
            >
              <Slot couleur={cuveeColor(b)} statut={b.statut} size={30} onClick={() => setFiche(b)} />
              <span className="flex-1 min-w-0 truncate">
                {b.domaine_nom} — {b.cuvee_nom} {b.millesime || ''} × {b.quantite}
              </span>
              <button
                onClick={() => setSelected(b)}
                className="px-3.5 py-2 rounded-xl border border-gold/15 text-muted text-sm"
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

      <BottleSheet b={selected} onClose={() => setSelected(null)} />
    </div>
  )
}
