import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, errMsg } from '../api'
import { useData } from '../data'
import { useToast } from '../toast'
import { COULEUR_LABELS, type Bouteille, type Couleur, type FicheCuvee, type PrixMarche } from '../types'
import { TastingSheet } from './TastingSheet'
import { formatDate, formatDateTime } from '../dates'

/* ------------------------------------------------------------------ *
 *  Fiche vin plein écran — vue détaillée d'un vin.
 *
 *  Toutes les données affichées viennent du backend :
 *   - référentiel + conseil de dégustation + profil gustatif + accords
 *     mets-vins + cépages + prix d'achat moyen via GET /cuvees/{id}/fiche/ ;
 *   - millésimes en stock, apogée et emplacement via la cave de l'utilisateur.
 *
 *  Les cartes « Oeni+ » (maturité, valeur actuelle) et l'historique de prix
 *  restent des emplacements premium à débloquer, comme sur les maquettes.
 * ------------------------------------------------------------------ */

const HERO_BG: Record<Couleur, string> = {
  ROUGE: 'linear-gradient(160deg, #3a1622, #6d2740 60%, #1b1512)',
  BLANC: 'linear-gradient(160deg, #4a4327, #7c6f3d 60%, #1b1512)',
  ROSE: 'linear-gradient(160deg, #4a2b34, #8e5266 60%, #1b1512)',
  BULLES: 'linear-gradient(160deg, #4a4027, #8a733d 60%, #1b1512)',
  AUTRE: 'linear-gradient(160deg, #2f333a, #52585f 60%, #1b1512)',
}

/* Classes réutilisées : CTA doré plein et bouton « ajouter » en pointillés. */
const goldBtnCls = 'w-full py-3.5 rounded-2xl font-bold text-white bg-gradient-to-b from-gold to-gold-soft'
const addBtnCls = 'w-full py-3.5 rounded-2xl border border-dashed border-gold/25 text-muted text-sm'
const roundBtnCls = 'w-11 h-11 grid place-items-center rounded-full glass'

/** Formate une fenêtre d'apogée (« 2024-2035 », « dès 2024 », « avant 2035 »). */
function formatApogee(debut: number | null, fin: number | null): string | null {
  if (debut && fin) return `${debut}-${fin}`
  if (debut) return `dès ${debut}`
  if (fin) return `avant ${fin}`
  return null
}

/** Formate un prix « 25.00 » -> « 25,00 € ». */
function formatPrix(prix: string | null): string {
  if (prix == null) return '-- €'
  return `${Number(prix).toFixed(2).replace('.', ',')} €`
}

/** Symbole monétaire à partir d'un code ISO. */
function devise(code: string): string {
  return { EUR: '€', USD: '$', GBP: '£' }[code] ?? code
}

/** Formate une fourchette de prix marché « 38–65 € ». */
function formatFourchette(p: PrixMarche): string {
  return `${Math.round(p.min)}–${Math.round(p.max)} ${devise(p.devise)}`
}

/* Carte du bandeau de valeur : soit une vraie valeur, soit un verrou « Oeni+ ». */
function InfoCard({ label, value, locked }: { label: string; value?: string; locked?: boolean }) {
  return (
    <div className="glass rounded-2xl px-3.5 py-3">
      {locked || !value ? (
        <div className="flex items-center gap-1.5 font-serif text-[1.15rem] text-gold">
          Oeni+ <span className="text-[0.9rem]">👑</span>
        </div>
      ) : (
        <div className="font-serif text-[1.15rem] text-ink">{value}</div>
      )}
      <div className="text-[0.72rem] text-muted mt-1">{label}</div>
    </div>
  )
}

function Section({ title, emoji, action, children }: {
  title: string
  emoji?: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="mt-6">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-serif text-[1.35rem] text-ink m-0">
          {title} {emoji && <span className="text-[1.05rem]">{emoji}</span>}
        </h2>
        {action}
      </div>
      {children}
    </section>
  )
}

export function FicheVin({
  bouteille,
  onClose,
  onOptions,
  onRetirer,
}: {
  bouteille: Bouteille
  onClose: () => void
  onOptions: (b: Bouteille) => void
  onRetirer: (b: Bouteille) => void
}) {
  const { bouteilles, cuveeColor, mouvements } = useData()
  const toast = useToast()
  const couleur = cuveeColor(bouteille)

  const [fiche, setFiche] = useState<FicheCuvee | null>(null)
  const [selId, setSelId] = useState(bouteille.id)
  const [tab, setTab] = useState<'millesimes' | 'historique'>('millesimes')
  const [tasting, setTasting] = useState(false)
  const [syncing, setSyncing] = useState(false)

  // Millésimes en stock de cette cuvée (données réelles de la cave), triés du
  // plus récent au plus ancien. On garde les Bouteille (avec id + apogée) pour
  // le sélecteur et les actions bas de page.
  const millesimes = useMemo(
    () =>
      bouteilles
        .filter((b) => b.cuvee === bouteille.cuvee && b.quantite > 0)
        .sort((a, b) => (b.millesime ?? 0) - (a.millesime ?? 0)),
    [bouteilles, bouteille.cuvee],
  )
  const selected = millesimes.find((b) => b.id === selId) ?? bouteille
  const totalStock = millesimes.reduce((sum, b) => sum + b.quantite, 0)
  const maxStock = Math.max(1, ...millesimes.map((b) => b.quantite))
  const apogee = formatApogee(selected.apogee_debut, selected.apogee_fin)

  // Mouvements de stock concernant cette cuvée (onglet Historique).
  const idsCuvee = useMemo(() => new Set(millesimes.map((b) => b.id)), [millesimes])
  const historique = mouvements.filter((m) => idsCuvee.has(m.bouteille))

  const reloadFiche = useCallback(() => {
    api<FicheCuvee>('GET', `/api/cuvees/${bouteille.cuvee}/fiche/`)
      .then(setFiche)
      .catch(() => {})
  }, [bouteille.cuvee])

  useEffect(() => {
    reloadFiche()
  }, [reloadFiche])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [onClose])

  const share = async () => {
    if (!navigator.share) return
    const text = `${bouteille.cuvee_nom} — ${bouteille.domaine_nom}`
    try {
      await navigator.share({ title: text, text })
    } catch {
      /* partage annulé par l'utilisateur */
    }
  }

  // Synchro à la demande des données wineapi (bouton 🔄). Le garde-fou anti-quota
  // (cooldown) est côté serveur : un 429 signale simplement d'attendre.
  const syncFiche = async () => {
    if (syncing) return
    setSyncing(true)
    try {
      const data = await api<FicheCuvee>('POST', `/api/cuvees/${bouteille.cuvee}/rafraichir/`)
      setFiche(data)
      toast('Fiche synchronisée depuis WineAPI. 🔄', 'ok')
    } catch (e) {
      const httpStatus = (e as { status?: number }).status
      if (httpStatus === 429)
        toast('Fiche déjà synchronisée récemment — réessaie plus tard.', 'err')
      else if (httpStatus === 400) toast('Aucune source externe pour ce vin.', 'err')
      else toast(errMsg(e, 'Échec de la synchronisation.'), 'err')
    } finally {
      setSyncing(false)
    }
  }

  const appellation = fiche?.cuvee.appellation || 'Appellation inconnue'

  return (
    <div className="fixed inset-0 z-40 overflow-y-auto bg-bg">
      {/* ---------- Hero ---------- */}
      <div
        className="relative"
        style={{ background: HERO_BG[couleur], paddingTop: 'calc(12px + env(safe-area-inset-top))' }}
      >
        <div className="flex items-center justify-between px-4">
          <button onClick={onClose} aria-label="Retour" className={`${roundBtnCls} text-ink text-xl`}>
            ‹
          </button>
          <div className="flex gap-2.5">
            {fiche?.enrichissable && (
              <button
                onClick={syncFiche}
                disabled={syncing}
                aria-label="Synchroniser la fiche"
                title="Synchroniser depuis WineAPI"
                className={`${roundBtnCls} text-ink text-lg disabled:opacity-60`}
              >
                <span className={`inline-block ${syncing ? 'animate-spin' : ''}`}>🔄</span>
              </button>
            )}
            <button onClick={share} aria-label="Partager" className={`${roundBtnCls} text-ink text-lg`}>
              ⤴
            </button>
          </div>
        </div>

        <div className="flex flex-col items-center pt-3 pb-8">
          {fiche?.cuvee.image_url ? (
            <img
              src={fiche.cuvee.image_url}
              alt={bouteille.cuvee_nom}
              className="h-40 object-contain drop-shadow-[0_10px_24px_rgba(0,0,0,0.5)]"
            />
          ) : (
            <div className="text-[5.5rem] leading-none drop-shadow-[0_10px_24px_rgba(0,0,0,0.5)]">🍷</div>
          )}
          <div className="flex flex-wrap justify-center gap-2 mt-3">
            <span className="px-4 py-1.5 rounded-full text-white text-sm font-semibold bg-gradient-to-b from-wine-soft to-wine-deep">
              {COULEUR_LABELS[couleur]}
            </span>
            {fiche?.cuvee.classification && (
              <span className="px-4 py-1.5 rounded-full text-sm font-semibold border border-gold/40 text-gold">
                {fiche.cuvee.classification}
              </span>
            )}
          </div>
          <div className="text-muted text-sm mt-3">{appellation}</div>
          {(fiche?.cuvee.region || fiche?.cuvee.pays) && (
            <div className="text-muted text-xs mt-0.5">
              {[fiche?.cuvee.region, fiche?.cuvee.pays].filter(Boolean).join(', ')}
            </div>
          )}
          <h1 className="font-serif text-[1.9rem] font-semibold text-ink mt-1 text-center px-6">
            {bouteille.cuvee_nom}
          </h1>
          <div className="text-muted text-[0.95rem] mt-0.5">{bouteille.domaine_nom}</div>
        </div>
      </div>

      <div className="max-w-[640px] mx-auto px-4" style={{ paddingBottom: 'calc(96px + env(safe-area-inset-bottom))' }}>
        {/* ---------- Onglets ---------- */}
        <div className="grid grid-cols-2 gap-2 mt-4 p-1 rounded-full glass">
          {(['millesimes', 'historique'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`py-2.5 rounded-full text-sm font-semibold transition ${
                tab === t ? 'bg-gradient-to-b from-wine-soft to-wine-deep text-white' : 'text-muted'
              }`}
            >
              {t === 'millesimes' ? 'Millésimes' : 'Historique'}
            </button>
          ))}
        </div>

        {tab === 'historique' ? (
          <Section title="Historique">
            {historique.length === 0 ? (
              <p className="text-muted text-sm glass rounded-2xl p-4">
                Aucun mouvement enregistré pour ce vin.
              </p>
            ) : (
              <div className="glass rounded-2xl divide-y divide-gold/10">
                {historique.map((m) => (
                  <div key={m.id} className="flex items-center justify-between px-4 py-3 text-sm">
                    <div>
                      <div className="text-ink capitalize">{m.type_mouvement.toLowerCase()}</div>
                      {m.occasion && <div className="text-muted text-xs">{m.occasion}</div>}
                    </div>
                    <div className="text-right">
                      <div className="text-ink">× {m.quantite}</div>
                      <div className="text-muted text-xs">{formatDateTime(m.date)}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Section>
        ) : (
          <>
            {/* ---------- Vos millésimes ---------- */}
            <Section title="Vos millésimes">
              <div className="flex gap-2 overflow-x-auto no-scrollbar pb-1">
                {millesimes.map((b) => (
                  <button
                    key={b.id}
                    onClick={() => setSelId(b.id)}
                    className={`shrink-0 px-5 py-3 rounded-2xl text-left transition border ${
                      b.id === selId
                        ? 'bg-gradient-to-b from-wine-soft to-wine-deep border-wine text-white'
                        : 'glass border-transparent text-muted'
                    }`}
                  >
                    <div className="font-serif text-[1.25rem] leading-none">
                      {b.millesime ?? 'N.M.'} <span className="text-[0.8rem] opacity-80">(x{b.quantite})</span>
                    </div>
                  </button>
                ))}
              </div>
              <div className="text-wine-soft text-sm mt-2 font-medium">Standard 75cl</div>
            </Section>

            {/* ---------- Cartes valeur ---------- */}
            <div className="grid grid-cols-2 gap-2.5 mt-4">
              <InfoCard label="Maturité" locked />
              <InfoCard label="Date d'apogée" value={apogee ?? undefined} />
              <InfoCard label="Prix d'achat moyen" value={fiche ? formatPrix(fiche.prix_achat_moyen) : undefined} />
              <InfoCard
                label="Valeur actuelle"
                value={fiche?.prix_marche ? formatFourchette(fiche.prix_marche) : undefined}
              />
            </div>

            {/* ---------- Note et avis du millésime ---------- */}
            {fiche && (
              <Section title="Note et avis du millésime" emoji="⭐">
                <div className="grid grid-cols-2 gap-2.5">
                  <button
                    onClick={() => setTasting(true)}
                    className="glass rounded-2xl py-4 text-center active:scale-[0.98] transition"
                  >
                    <div className="font-serif text-[1.6rem] text-ink flex items-center justify-center gap-1">
                      {fiche.ma_note ? `${fiche.ma_note.note}/5` : '--/5'}{' '}
                      <span className="text-gold text-[1.1rem]">★</span>
                    </div>
                    <div className="text-[0.72rem] text-muted mt-1">
                      {fiche.ma_note ? 'Ma note · modifier' : 'Noter ce vin'}
                    </div>
                  </button>
                  <div className="glass rounded-2xl py-4 text-center">
                    <div className="font-serif text-[1.6rem] text-gold flex items-center justify-center gap-1">
                      {fiche.note_communaute ? `${fiche.note_communaute.note}/5` : '—'}{' '}
                      <span className="text-[1.1rem]">★</span>
                    </div>
                    <div className="text-[0.72rem] text-muted mt-1">
                      {fiche.note_communaute ? `${fiche.note_communaute.nb} notes` : 'Communauté'}
                    </div>
                  </div>
                </div>
                {fiche.ma_note?.commentaire && (
                  <p className="text-sm text-ink/90 mt-2.5 glass rounded-2xl p-3">
                    « {fiche.ma_note.commentaire} »
                  </p>
                )}
              </Section>
            )}

            {/* ---------- Conseil de dégustation ---------- */}
            <Section title="Conseil de dégustation" emoji="🍷">
              <div className="grid grid-cols-2 gap-2.5">
                <div className="glass rounded-2xl px-4 py-3.5 flex items-center gap-3">
                  <span className="text-2xl">🌡️</span>
                  <div>
                    <div className="font-serif text-[1.3rem] text-ink leading-none">
                      {fiche?.conseil_degustation.temperature ?? '…'} <span className="text-xs text-muted">°C</span>
                    </div>
                    <div className="text-[0.72rem] text-muted mt-1">Température</div>
                  </div>
                </div>
                <div className="glass rounded-2xl px-4 py-3.5 flex items-center gap-3">
                  <span className="text-2xl">⏳</span>
                  <div>
                    <div className="font-serif text-[1.3rem] text-ink leading-none">
                      {fiche?.conseil_degustation.carafage ?? '…'}
                    </div>
                    <div className="text-[0.72rem] text-muted mt-1">Carafage</div>
                  </div>
                </div>
              </div>
              <button
                onClick={() => setTasting(true)}
                className={`${goldBtnCls} mt-3 flex items-center justify-center gap-2`}
              >
                🍷 Commencer une dégustation
              </button>
            </Section>

            {/* ---------- Tags ---------- */}
            <Section title="Tags" emoji="🏷️" action={<span className="text-gold text-sm">Ajouter</span>}>
              <button className={addBtnCls}>🏷️ Ajouter un tag</button>
            </Section>

            {/* ---------- Cave ---------- */}
            <Section title="Cave" emoji="🔎" action={<span className="text-gold text-sm">Modifier</span>}>
              <span className="inline-block px-4 py-2.5 rounded-full glass text-muted text-sm">
                {selected.emplacement_chemin || 'Ma cave'} ({totalStock})
              </span>
            </Section>

            {/* ---------- Notes personnelles ---------- */}
            <Section title="Vos notes personnelles" emoji="✍️">
              <button onClick={() => setTasting(true)} className={addBtnCls}>
                ✏️ Ajouter une note personnelle
              </button>
            </Section>

            {/* ---------- À propos ---------- */}
            {fiche && (fiche.cuvee.description || fiche.cuvee.elaborate || fiche.cuvee.degre_alcool != null) && (
              <Section title="À propos" emoji="📝">
                <div className="glass rounded-2xl p-4">
                  {fiche.cuvee.description && (
                    <p className="text-sm text-ink/90 leading-relaxed">{fiche.cuvee.description}</p>
                  )}
                  {fiche.cuvee.elaborate && (
                    <p className="text-sm text-muted leading-relaxed mt-2">{fiche.cuvee.elaborate}</p>
                  )}
                  {fiche.cuvee.degre_alcool != null && (
                    <div className="text-sm text-muted mt-3">
                      Degré d'alcool : <span className="text-ink font-medium">{fiche.cuvee.degre_alcool}°</span>
                    </div>
                  )}
                </div>
              </Section>
            )}

            {/* ---------- Caractéristique gustative ---------- */}
            {fiche && fiche.profil_gustatif.length > 0 && (
              <Section title="Caractéristique gustative" emoji="🍷">
                <div className="glass rounded-2xl p-4 flex flex-col gap-3.5">
                  {fiche.profil_gustatif.map((row) => (
                    <div key={row.gauche} className="flex items-center gap-3 text-sm">
                      <span className="w-16 text-muted text-right shrink-0">{row.gauche}</span>
                      <div className="flex-1 h-2.5 rounded-full bg-black/30 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-gold-soft to-gold"
                          style={{ width: `${row.valeur * 100}%` }}
                        />
                      </div>
                      <span className="w-16 text-ink shrink-0">{row.droite}</span>
                    </div>
                  ))}
                </div>
              </Section>
            )}

            {/* ---------- Cépages ---------- */}
            {fiche && fiche.cuvee.cepages.length > 0 && (
              <Section title="Cépages" emoji="🍇">
                <div className="flex gap-2 overflow-x-auto no-scrollbar pb-1">
                  {fiche.cuvee.cepages.map((nom) => (
                    <span key={nom} className="shrink-0 px-4 py-2.5 rounded-full glass text-sm text-ink">
                      {nom}
                    </span>
                  ))}
                </div>
              </Section>
            )}

            {/* ---------- Mets adaptés ---------- */}
            {fiche && fiche.accords_mets.length > 0 && (
              <Section title="Mets adaptés">
                <div className="flex gap-3 overflow-x-auto no-scrollbar pb-1">
                  {fiche.accords_mets.map((m) => (
                    <div key={m.nom} className="shrink-0 w-32">
                      <div className="relative h-32 rounded-2xl glass grid place-items-center text-6xl">
                        {m.emoji}
                        {m.confiance != null && (
                          <span className="absolute top-2 right-2 px-2 py-0.5 rounded-full bg-black/55 text-ok text-sm font-bold">
                            {Math.round(m.confiance * 100)}%
                          </span>
                        )}
                      </div>
                      <div className="text-center text-sm mt-2 text-ink">{m.nom}</div>
                    </div>
                  ))}
                </div>
              </Section>
            )}

            {/* ---------- Sections premium ---------- */}
            <Section title="Phase de vieillissement">
              <button className={goldBtnCls}>Essayer Oeni+</button>
            </Section>

            <Section title="Prix de la bouteille">
              <div className="text-muted text-sm mb-3">Historique de prix : France</div>
              <button className={goldBtnCls}>Essayer Oeni+</button>
            </Section>

            {/* ---------- Stock par millésimes ---------- */}
            <Section title="Stock par millésimes">
              <div className="glass rounded-2xl p-4">
                <div className="flex items-end justify-around gap-3 h-40">
                  {millesimes.map((b) => (
                    <div key={b.id} className="flex flex-col items-center justify-end h-full flex-1 max-w-16">
                      <div className="text-sm text-ink mb-1">{b.quantite}</div>
                      <div
                        className="w-8 rounded-t-md bg-gradient-to-b from-gold to-gold-soft"
                        style={{ height: `${(b.quantite / maxStock) * 100}%` }}
                      />
                      <div className="text-xs text-muted mt-2">{b.millesime ?? 'N.M.'}</div>
                    </div>
                  ))}
                </div>
              </div>
            </Section>

            {/* ---------- Avis (scores critiques wineapi) ---------- */}
            {fiche && fiche.avis.length > 0 && (
              <Section title="Avis" emoji="👥">
                {fiche.avis.map((a) => (
                  <div key={`${a.reviewer}-${a.date ?? ''}`} className="glass rounded-2xl p-4 mb-2.5">
                    <div className="flex items-center justify-between">
                      <span className="text-ink font-medium">{a.reviewer}</span>
                      {a.score != null && (
                        <span className="font-serif text-[1.25rem] text-gold">{a.score}</span>
                      )}
                    </div>
                    {(a.score_text || a.date) && (
                      <div className="text-muted text-sm mt-1">
                        {a.score_text}
                        {a.score_text && a.date ? ' · ' : ''}
                        {a.date ? formatDate(a.date) : ''}
                      </div>
                    )}
                  </div>
                ))}
              </Section>
            )}
          </>
        )}
      </div>

      {/* ---------- Barre d'action fixe ---------- */}
      <div
        className="fixed bottom-0 left-0 right-0 z-10 flex gap-3 px-4 pt-3"
        style={{
          paddingBottom: 'calc(14px + env(safe-area-inset-bottom))',
          background: 'linear-gradient(180deg, rgba(27,21,18,0.4), rgba(20,15,12,0.97))',
          backdropFilter: 'saturate(1.2) blur(14px)',
          WebkitBackdropFilter: 'saturate(1.2) blur(14px)',
        }}
      >
        <button onClick={() => onOptions(selected)} className={`${goldBtnCls} flex-1 rounded-full`}>
          Autres options
        </button>
        <button
          onClick={() => onRetirer(selected)}
          className="flex-1 py-3.5 rounded-full font-semibold text-muted glass"
        >
          Retirer
        </button>
      </div>

      <TastingSheet
        open={tasting}
        onClose={() => setTasting(false)}
        cuveeId={bouteille.cuvee}
        cuveeNom={bouteille.cuvee_nom}
        millesime={selected.millesime}
        onSaved={reloadFiche}
      />
    </div>
  )
}
