import { useMemo, useState } from 'react'
import { useData } from '../data'
import { BottleBar, BottleSheet } from '../components/bottle'
import { ChoixVinSheet } from '../components/ChoixVinSheet'
import { FicheVin } from '../components/FicheVin'
import { TastingSheet } from '../components/TastingSheet'
import { formatDate } from '../dates'
import type { Bouteille } from '../types'

/* ------------------------------------------------------------------ *
 *  Accueil — tableau de bord de la cave (maquette « CavaVin Écrans ») :
 *  compteur de bouteilles, actions rapides (ajouter / déguster /
 *  retirer), vins à boire bientôt et derniers mouvements.
 * ------------------------------------------------------------------ */

const sectionCls = 'text-xs uppercase tracking-[0.1em] text-muted mt-5 mb-2.5'

/* Libellé signé d'un mouvement (« +2 ajoutées », « −1 retirée »). */
function mouvementLabel(type: string, quantite: number): { txt: string; gold: boolean } {
  if (type === 'ENTREE') return { txt: `+${quantite} ajoutée${quantite > 1 ? 's' : ''}`, gold: true }
  if (type === 'AJUSTEMENT') return { txt: `±${quantite} ajustée${quantite > 1 ? 's' : ''}`, gold: false }
  return { txt: `−${quantite} retirée${quantite > 1 ? 's' : ''}`, gold: false }
}

export default function AccueilScreen({ onAjouter }: { onAjouter: () => void }) {
  const { bouteilles, cuveeColor, mouvements, cuvees } = useData()

  const [fiche, setFiche] = useState<Bouteille | null>(null)
  const [options, setOptions] = useState<Bouteille | null>(null)
  const [choix, setChoix] = useState<'degustation' | 'retrait' | null>(null)
  const [tastingFor, setTastingFor] = useState<Bouteille | null>(null)

  const actives = useMemo(() => bouteilles.filter((b) => b.quantite > 0), [bouteilles])
  const total = actives.reduce((n, b) => n + b.quantite, 0)
  const aBoire = useMemo(
    () =>
      actives
        .filter((b) => b.statut === 'A_BOIRE')
        .sort((a, b) => (a.apogee_fin_effectif ?? 9999) - (b.apogee_fin_effectif ?? 9999)),
    [actives],
  )
  const nbABoire = aBoire.reduce((n, b) => n + b.quantite, 0)

  const regionOf = (b: Bouteille) => {
    const c = cuvees.find((c) => c.id === b.cuvee)
    return c?.region || c?.appellation || ''
  }

  const bottleName = (id: number) => {
    const b = bouteilles.find((x) => x.id === id)
    return b ? `${b.cuvee_nom}${b.millesime ? ' ' + b.millesime : ''}` : `Bouteille #${id}`
  }

  return (
    <div>
      {/* ---------- Compteur ---------- */}
      <div className="glass rounded-card p-5 flex flex-col gap-1">
        <span className="font-serif text-[2.6rem] leading-none text-ink-bright">{total}</span>
        <span className="text-[13px] text-muted">
          bouteille{total > 1 ? 's' : ''} en cave
        </span>
        {nbABoire > 0 && (
          <span className="text-xs text-gold-soft mt-1">
            {nbABoire} à boire bientôt
          </span>
        )}
      </div>

      {/* ---------- Actions rapides ---------- */}
      <div className="flex gap-2.5 mt-4">
        <button
          onClick={onAjouter}
          className="flex-1 text-center py-3 px-2 rounded-xl bg-wine text-ink-bright text-[13px] font-semibold active:scale-[0.985] transition"
        >
          Ajouter&nbsp;une&nbsp;bouteille
        </button>
        <button
          onClick={() => setChoix('degustation')}
          className="flex-1 text-center py-3 px-2 rounded-xl border text-[13px] font-semibold active:scale-[0.985] transition"
          style={{ borderColor: 'oklch(45% 0.13 85)', color: 'oklch(75% 0.1 85)' }}
        >
          Nouvelle&nbsp;dégustation
        </button>
      </div>
      <button
        onClick={() => setChoix('retrait')}
        className="w-full text-center py-3 px-2 mt-2.5 rounded-xl border border-line text-muted-strong text-[13px] font-semibold active:scale-[0.985] transition"
      >
        Retirer&nbsp;une&nbsp;bouteille
      </button>

      {/* ---------- À boire bientôt ---------- */}
      <div className={sectionCls}>À boire bientôt</div>
      {aBoire.length === 0 ? (
        <div className="glass rounded-[10px] p-3.5 text-muted text-sm">
          Rien à ouvrir en urgence — votre cave patiente sagement.
        </div>
      ) : (
        <div className="flex flex-col gap-2.5">
          {aBoire.slice(0, 6).map((b) => (
            <button
              key={b.id}
              onClick={() => setFiche(b)}
              className="glass rounded-[10px] p-3 flex items-center gap-3 text-left active:scale-[0.99] transition"
            >
              <BottleBar couleur={cuveeColor(b)} />
              <span className="flex-1 min-w-0">
                <span className="block text-[13px] truncate">{b.cuvee_nom}</span>
                <span className="block text-[11px] text-muted truncate">
                  {[b.millesime, regionOf(b)].filter(Boolean).join(' · ') || b.domaine_nom}
                </span>
              </span>
              <span className="text-[11px] px-2 py-0.5 rounded-[10px] bg-surface-2 text-muted-strong">
                ×{b.quantite}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* ---------- Derniers mouvements (journal) ---------- */}
      <div className={sectionCls}>Derniers mouvements</div>
      {mouvements.length === 0 ? (
        <div className="glass rounded-[10px] p-3.5 text-muted text-sm">
          Aucun mouvement pour le moment. Les ajouts et consommations apparaîtront ici.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {mouvements.slice(0, 8).map((m) => {
            const lbl = mouvementLabel(m.type_mouvement, m.quantite)
            return (
              <div
                key={m.id}
                className="glass rounded-[10px] px-3 py-2.5 flex items-center justify-between gap-3"
              >
                <span className="min-w-0">
                  <span className="block text-[13px] truncate">{bottleName(m.bouteille)}</span>
                  <span className="block text-[11px] text-muted">
                    {formatDate(m.date)}
                    {m.occasion ? ` — ${m.occasion}` : ''}
                  </span>
                </span>
                <span
                  className={`text-[13px] font-semibold whitespace-nowrap ${lbl.gold ? 'text-gold-soft' : ''}`}
                >
                  {lbl.txt}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* ---------- Feuilles & fiche ---------- */}
      <ChoixVinSheet
        open={choix !== null}
        title={choix === 'degustation' ? 'Nouvelle dégustation' : 'Retirer une bouteille'}
        onClose={() => setChoix(null)}
        onPick={(b) => {
          const mode = choix
          setChoix(null)
          if (mode === 'degustation') setTastingFor(b)
          else setOptions(b)
        }}
      />

      {tastingFor && (
        <TastingSheet
          open
          onClose={() => setTastingFor(null)}
          cuveeId={tastingFor.cuvee}
          cuveeNom={tastingFor.cuvee_nom}
          millesime={tastingFor.millesime}
          onSaved={() => {}}
        />
      )}

      {fiche && (
        <FicheVin
          bouteille={fiche}
          onClose={() => setFiche(null)}
          onOptions={(b) => setOptions(b)}
          onRetirer={(b) => setOptions(b)}
        />
      )}

      <BottleSheet b={options} onClose={() => setOptions(null)} />
    </div>
  )
}
