import { useMemo, useState } from 'react'
import { useData } from '../data'
import { Sheet } from '../ui'
import { BottleBar } from './bottle'
import type { Bouteille } from '../types'

/* ------------------------------------------------------------------ *
 *  Sélecteur de vin : feuille listant les bouteilles en stock, avec
 *  recherche. Sert aux actions rapides de l'accueil (« Nouvelle
 *  dégustation », « Retirer une bouteille ») pour choisir la cible.
 * ------------------------------------------------------------------ */

export function ChoixVinSheet({
  open,
  title,
  onClose,
  onPick,
}: {
  open: boolean
  title: string
  onClose: () => void
  onPick: (b: Bouteille) => void
}) {
  const { bouteilles, cuveeColor } = useData()
  const [query, setQuery] = useState('')

  const actives = useMemo(
    () =>
      bouteilles
        .filter((b) => b.quantite > 0)
        .sort(
          (a, b) =>
            a.domaine_nom.localeCompare(b.domaine_nom) ||
            (b.millesime ?? 0) - (a.millesime ?? 0),
        ),
    [bouteilles],
  )

  const q = query.trim().toLowerCase()
  const filtered = q
    ? actives.filter((b) =>
        `${b.domaine_nom} ${b.cuvee_nom} ${b.millesime ?? ''}`.toLowerCase().includes(q),
      )
    : actives

  return (
    <Sheet open={open} onClose={onClose}>
      {open && (
        <>
          <h3 className="font-serif text-[1.35rem] m-0">{title}</h3>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Rechercher un vin…"
            className="w-full text-[16px] px-3.5 py-3 mt-3 rounded-xl bg-surface border border-line-strong text-ink outline-none placeholder:text-placeholder focus:border-gold/60"
          />
          <div className="flex flex-col gap-2.5 mt-3">
            {filtered.length === 0 ? (
              <p className="text-muted text-sm">Aucun vin en stock ne correspond.</p>
            ) : (
              filtered.map((b) => (
                <button
                  key={b.id}
                  onClick={() => onPick(b)}
                  className="glass rounded-[10px] p-3 flex items-center gap-3 text-left active:scale-[0.99] transition"
                >
                  <BottleBar couleur={cuveeColor(b)} />
                  <span className="flex-1 min-w-0">
                    <span className="block text-[13px] truncate">{b.cuvee_nom}</span>
                    <span className="block text-[11px] text-muted truncate">
                      {b.domaine_nom}
                      {b.millesime ? ` · ${b.millesime}` : ''}
                    </span>
                  </span>
                  <span className="text-[11px] px-2 py-0.5 rounded-[10px] bg-surface-2 text-muted-strong">
                    ×{b.quantite}
                  </span>
                </button>
              ))
            )}
          </div>
        </>
      )}
    </Sheet>
  )
}
