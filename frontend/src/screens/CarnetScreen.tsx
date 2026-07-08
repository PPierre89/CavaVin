import { useEffect, useState } from 'react'
import { apiAllPages } from '../api'
import { Card, CardTitle } from '../ui'
import { COULEUR_LABELS, type Couleur, type NoteDegustation } from '../types'

const DOT: Record<Couleur, string> = {
  ROUGE: 'var(--color-rouge)',
  BLANC: 'var(--color-blanc)',
  ROSE: 'var(--color-rose)',
  BULLES: 'var(--color-bulles)',
  AUTRE: 'var(--color-autre)',
}

function Stars({ note }: { note: number }) {
  return (
    <span className="text-gold text-sm">
      {'★'.repeat(Math.round(note))}
      <span className="text-gold/25">{'★'.repeat(5 - Math.round(note))}</span>
    </span>
  )
}

export default function CarnetScreen() {
  const [notes, setNotes] = useState<NoteDegustation[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiAllPages<NoteDegustation>('/api/notes-degustation/')
      .then(setNotes)
      .finally(() => setLoading(false))
  }, [])

  return (
    <Card>
      <CardTitle>Carnet de dégustation</CardTitle>
      {loading ? (
        <p className="text-muted text-sm">Chargement du carnet…</p>
      ) : notes.length === 0 ? (
        <p className="text-muted text-sm">
          Aucune dégustation pour le moment. Ouvre une fiche vin et lance « Commencer une
          dégustation » pour la consigner ici.
        </p>
      ) : (
        notes.map((n) => (
          <div key={n.id} className="py-3 border-b border-gold/10 last:border-0">
            <div className="flex items-center gap-2.5">
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ background: DOT[n.couleur] ?? DOT.AUTRE }}
                title={COULEUR_LABELS[n.couleur]}
              />
              <span className="flex-1 min-w-0 truncate text-sm">
                {n.domaine_nom} — {n.cuvee_nom}
                {n.millesime ? ` ${n.millesime}` : ''}
              </span>
              <Stars note={Number(n.note)} />
            </div>
            {n.commentaire && <p className="text-sm text-ink/90 mt-1.5 ml-5">« {n.commentaire} »</p>}
            <div className="text-xs text-muted mt-1 ml-5">
              {new Date(n.date_degustation).toLocaleDateString('fr-FR', {
                day: 'numeric',
                month: 'long',
                year: 'numeric',
              })}
            </div>
          </div>
        ))
      )}
    </Card>
  )
}
