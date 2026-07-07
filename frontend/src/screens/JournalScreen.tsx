import { useData } from '../data'
import { Card, CardTitle } from '../ui'

const MVT_ICONS: Record<string, string> = {
  CONSOMMATION: '🥂',
  ENTREE: '📥',
  SORTIE: '📤',
  AJUSTEMENT: '🔧',
}

export default function JournalScreen() {
  const { mouvements, bouteilles } = useData()

  return (
    <Card>
      <CardTitle>Derniers mouvements</CardTitle>
      {mouvements.length === 0 ? (
        <p className="text-muted text-sm">
          Aucun mouvement pour le moment. Les consommations apparaîtront ici.
        </p>
      ) : (
        mouvements.map((m) => {
          const b = bouteilles.find((x) => x.id === m.bouteille)
          const nom = b ? `${b.cuvee_nom}${b.millesime ? ' ' + b.millesime : ''}` : `Bouteille #${m.bouteille}`
          const date = new Date(m.date).toLocaleDateString('fr-FR', {
            day: 'numeric',
            month: 'short',
            hour: '2-digit',
            minute: '2-digit',
          })
          return (
            <div
              key={m.id}
              className="flex items-center gap-3 py-3 border-b border-gold/10 last:border-0"
            >
              <div className="shrink-0 w-9 h-9 rounded-full grid place-items-center bg-black/30 border border-gold/15">
                {MVT_ICONS[m.type_mouvement] || '•'}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm truncate">
                  {nom} · × {m.quantite}
                </div>
                <div className="text-xs text-muted mt-0.5">
                  {date}
                  {m.occasion ? ` — ${m.occasion}` : ''}
                </div>
              </div>
            </div>
          )
        })
      )}
    </Card>
  )
}
