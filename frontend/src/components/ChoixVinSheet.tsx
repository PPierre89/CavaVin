import { useMemo, useState } from 'react'
import { useData } from '../data'
import { Sheet, inputCls } from '../ui'
import { LigneVin, QtyBadge } from './bottle'
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
            className={`${inputCls} mt-3`}
          />
          <div className="flex flex-col gap-2.5 mt-3">
            {filtered.length === 0 ? (
              <p className="text-muted text-sm">Aucun vin en stock ne correspond.</p>
            ) : (
              filtered.map((b) => (
                <LigneVin
                  key={b.id}
                  couleur={cuveeColor(b)}
                  titre={b.cuvee_nom}
                  sousTitre={`${b.domaine_nom}${b.millesime ? ` · ${b.millesime}` : ''}`}
                  droite={<QtyBadge n={b.quantite} />}
                  onClick={() => onPick(b)}
                />
              ))
            )}
          </div>
        </>
      )}
    </Sheet>
  )
}
