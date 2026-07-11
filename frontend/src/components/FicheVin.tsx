import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, errMsg } from '../api'
import { useData } from '../data'
import { useToast } from '../toast'
import { IconButton, Logo, pillCls, tileCls, wineFill } from '../ui'
import {
  devise,
  formatApogee,
  formatFourchette,
  formatMontant,
  formatPrix,
  moisCourt,
} from '../format'
import {
  COULEUR_LABELS,
  COULEUR_VARS,
  STATUT_LABELS,
  type Bouteille,
  type FicheCuvee,
  type PointPrix,
  type PrixMarchand,
  type Statut,
} from '../types'
import { TastingSheet } from './TastingSheet'
import { formatDate, formatDateTime } from '../dates'

/* ------------------------------------------------------------------ *
 *  Fiche vin plein écran — vue détaillée d'un vin (maquette « CavaVin
 *  Écrans », colonne « Fiche bouteille »).
 *
 *  Toutes les données affichées viennent du backend :
 *   - référentiel + conseil de dégustation + profil gustatif + accords
 *     mets-vins + cépages + prix d'achat moyen via GET /cuvees/{id}/fiche/ ;
 *   - millésimes en stock, apogée et emplacement via la cave de l'utilisateur.
 * ------------------------------------------------------------------ */

/** Formate un prix marchand « 42,50 € ». */
function formatPrixMarchand(p: PrixMarchand): string {
  return formatMontant(p.prix, p.devise)
}

/* Graphe d'historique de prix : série « meilleur prix » (prix_min) dans le temps,
   avec une bande min–max en fond. SVG maison (aucune dépendance), or sur carte
   sombre. Requiert au moins deux points ; l'axe X est mis à l'échelle sur les
   dates réelles de relevé (fetchedAt). */
function PriceHistoryChart({ points }: { points: PointPrix[] }) {
  const W = 320
  const H = 150
  const padL = 10
  const padR = 44
  const padT = 16
  const padB = 22

  const xs = points.map((p) => Date.parse(p.date))
  const xmin = Math.min(...xs)
  const xmax = Math.max(...xs)
  const vals = points.flatMap((p) => [p.prix_min, p.prix_max])
  const dataMin = Math.min(...vals)
  const dataMax = Math.max(...vals)
  let lo = dataMin
  let hi = dataMax
  if (lo === hi) {
    lo -= 1
    hi += 1
  }
  const marge = (hi - lo) * 0.15
  lo -= marge
  hi += marge

  const X = (t: number) => padL + (xmax === xmin ? 0.5 : (t - xmin) / (xmax - xmin)) * (W - padL - padR)
  const Y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB)

  const ligne = points.map((p, i) => `${i ? 'L' : 'M'}${X(xs[i]).toFixed(1)},${Y(p.prix_min).toFixed(1)}`).join(' ')
  const haut = points.map((p, i) => `${i ? 'L' : 'M'}${X(xs[i]).toFixed(1)},${Y(p.prix_max).toFixed(1)}`)
  const bas = points.map((p, i) => `L${X(xs[i]).toFixed(1)},${Y(p.prix_min).toFixed(1)}`).reverse()
  const bande = [...haut, ...bas, 'Z'].join(' ')

  const last = points[points.length - 1]
  const gold = 'var(--color-gold)'
  const muted = 'var(--color-muted)'
  const resume = `Meilleur prix de ${formatMontant(dataMin, last.devise)} à ${formatMontant(dataMax, last.devise)} entre le ${points[0].date} et le ${last.date}.`

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full h-auto"
      role="img"
      aria-label={resume}
      preserveAspectRatio="xMidYMid meet"
    >
      <title>{resume}</title>
      {/* Repères horizontaux (min / max des données) + libellés de prix à droite. */}
      {[dataMax, dataMin].map((v) => (
        <g key={v}>
          <line x1={padL} y1={Y(v)} x2={W - padR} y2={Y(v)} stroke={muted} strokeOpacity={0.15} strokeWidth={1} />
          <text x={W - padR + 5} y={Y(v)} dy="0.32em" fontSize="10" fill={muted}>
            {Math.round(v)} {devise(last.devise)}
          </text>
        </g>
      ))}
      {/* Bande min–max (étendue des offres) puis ligne du meilleur prix. */}
      <path d={bande} fill={gold} fillOpacity={0.12} />
      <path d={ligne} fill="none" stroke={gold} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {points.map((p, i) => (
        <circle key={p.date} cx={X(xs[i])} cy={Y(p.prix_min)} r={i === points.length - 1 ? 4 : 2.5} fill={gold} />
      ))}
      {/* Dates de relevé aux extrémités. */}
      <text x={padL} y={H - 6} fontSize="10" fill={muted} textAnchor="start">
        {moisCourt(points[0].date)}
      </text>
      <text x={W - padR} y={H - 6} fontSize="10" fill={muted} textAnchor="end">
        {moisCourt(last.date)}
      </text>
    </svg>
  )
}

/* Petite carte info (libellé + valeur), grille 2 colonnes de la maquette. */
function InfoTile({ label, value, gold }: { label: string; value: ReactNode; gold?: boolean }) {
  return (
    <div className={tileCls}>
      <div className="text-[11px] text-muted">{label}</div>
      <div className={`text-[15px] mt-0.5 ${gold ? 'text-gold-soft' : 'text-ink'}`}>{value}</div>
    </div>
  )
}

/* Titre de section : capitales espacées, comme la maquette. */
function Section({ title, action, children }: {
  title: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="mt-5">
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-xs uppercase tracking-[0.1em] text-muted font-sans font-medium m-0">
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  )
}

/* Jauge de maturité : trois segments (à garder / à boire / dépassé) avec un
   curseur pointant le statut calculé de la bouteille sélectionnée. */
const ORDRE_STATUTS: Statut[] = ['A_GARDER', 'A_BOIRE', 'DEPASSE']
const STATUT_TEXTE_CLS: Record<Statut, string> = {
  A_GARDER: 'text-muted-strong',
  A_BOIRE: 'text-gold',
  DEPASSE: 'text-alerte',
}
function MaturiteGauge({ statut }: { statut: Statut }) {
  const idx = ORDRE_STATUTS.indexOf(statut)
  return (
    <div className={`${tileCls} flex flex-col gap-2 py-3`}>
      <div className="flex justify-between items-center">
        <span className="text-[11px] text-muted">Maturité</span>
        <span className={`text-[13px] font-semibold ${STATUT_TEXTE_CLS[statut]}`}>
          {STATUT_LABELS[statut]}
        </span>
      </div>
      <div className="relative flex gap-1 mt-1.5">
        {ORDRE_STATUTS.map((s, i) => (
          <div
            key={s}
            className={`flex-1 h-1.5 rounded-[3px] ${i === idx ? 'bg-wine-bright' : 'bg-track'}`}
          />
        ))}
        <div
          className="absolute -top-[7px] w-0 h-0 border-l-4 border-r-4 border-t-[5px] border-l-transparent border-r-transparent border-t-gold transition-all"
          style={{ left: `calc(${((idx * 2 + 1) / 6) * 100}% - 4px)` }}
        />
      </div>
      <div className="flex justify-between">
        {ORDRE_STATUTS.map((s) => (
          <span key={s} className="text-[10px] text-placeholder">
            {STATUT_LABELS[s]}
          </span>
        ))}
      </div>
    </div>
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
  const { bouteilles, cuveeColor, mouvements, refresh } = useData()
  const toast = useToast()
  const couleur = cuveeColor(bouteille)

  const [fiche, setFiche] = useState<FicheCuvee | null>(null)
  const [selId, setSelId] = useState(bouteille.id)
  const [tasting, setTasting] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [adding, setAdding] = useState(false)

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
  const apogee = formatApogee(selected.apogee_debut_effectif, selected.apogee_fin_effectif)

  // Mouvements de stock concernant cette cuvée (section Historique).
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

  // Bouton « + » de la carte étiquette : une bouteille de plus sur la ligne.
  const addOne = async () => {
    if (adding) return
    setAdding(true)
    try {
      await api('PATCH', `/api/bouteilles/${selected.id}/`, { quantite: selected.quantite + 1 })
      toast('Une bouteille ajoutée au stock. 🍷', 'ok')
      await refresh()
    } catch (e) {
      toast(errMsg(e, "Impossible d'ajuster le stock."), 'err')
    } finally {
      setAdding(false)
    }
  }

  const region = fiche?.cuvee.region || fiche?.cuvee.appellation || ''
  const sousTitre = [region, fiche?.cuvee.cepages[0], selected.millesime]
    .filter(Boolean)
    .join(' · ')

  // Historique de prix accumulé côté serveur (une observation par jour de relevé).
  const histPrix = fiche?.historique_prix ?? []
  const dernierPrix = histPrix.length ? histPrix[histPrix.length - 1] : null

  return (
    <div className="fixed inset-0 z-40 overflow-y-auto bg-bg">
      <div
        className="max-w-[640px] mx-auto px-4"
        style={{
          paddingTop: 'calc(12px + env(safe-area-inset-top))',
          paddingBottom: 'calc(96px + env(safe-area-inset-bottom))',
        }}
      >
        {/* ---------- En-tête ---------- */}
        <div className="flex items-center gap-2.5">
          <button onClick={onClose} aria-label="Retour" className="p-2 -ml-2 text-muted">
            <svg width="9" height="15" viewBox="0 0 9 15" aria-hidden="true">
              <path
                d="M8 1L1 7.5l7 6.5"
                stroke="currentColor"
                strokeWidth="1.6"
                fill="none"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
          <span className="font-serif text-[1.25rem] text-ink-bright">Fiche vin</span>
          <div className="ml-auto flex gap-2">
            {fiche?.enrichissable && (
              <IconButton
                onClick={syncFiche}
                disabled={syncing}
                aria-label="Synchroniser la fiche"
                title="Synchroniser depuis WineAPI"
                className="text-ink text-lg disabled:opacity-60 w-9! h-9!"
              >
                <span className={`inline-block ${syncing ? 'animate-spin' : ''}`}>🔄</span>
              </IconButton>
            )}
            <IconButton onClick={share} aria-label="Partager" className="text-ink text-lg w-9! h-9!">
              ⤴
            </IconButton>
          </div>
        </div>

        {/* ---------- Carte étiquette + ajustement de quantité ---------- */}
        <div className="glass rounded-card p-3.5 mt-3 relative flex justify-center">
          <button
            onClick={() => onRetirer(selected)}
            aria-label="Retirer une bouteille"
            className="absolute top-2.5 left-2.5 w-8 h-8 rounded-full border border-line-strong bg-sheet text-muted-strong grid place-items-center text-lg leading-none active:scale-95 transition"
          >
            −
          </button>
          <button
            onClick={addOne}
            disabled={adding}
            aria-label="Ajouter une bouteille"
            className={`absolute top-2.5 right-2.5 w-8 h-8 rounded-full ${wineFill} text-ink-bright grid place-items-center text-lg leading-none font-semibold active:scale-95 transition disabled:opacity-60`}
          >
            +
          </button>

          {fiche?.cuvee.image_url ? (
            <img
              src={fiche.cuvee.image_url}
              alt={bouteille.cuvee_nom}
              className="h-44 object-contain drop-shadow-[0_8px_20px_rgba(0,0,0,0.35)]"
            />
          ) : (
            /* Étiquette stylisée (à défaut de photo) : crème, liseré or. */
            <div className="w-32 rounded border-[1.5px] border-gold bg-cream px-3.5 py-4 flex flex-col items-center gap-1.5 shadow-[0_8px_20px_rgba(0,0,0,0.35)]">
              <Logo className="w-5 h-5" />
              <div className="w-7 h-px bg-gold" />
              <span className="font-serif text-base text-center leading-tight text-etiquette-ink">
                {bouteille.cuvee_nom}
              </span>
              <span className="font-serif text-[11px] tracking-wide text-etiquette-sub">
                {selected.millesime ?? 'N.M.'}
              </span>
              <div className="w-7 h-px bg-gold" />
              <span className="flex items-center gap-1.5">
                <span
                  className="w-2 h-2 rounded-full border border-gold-soft"
                  style={{ background: COULEUR_VARS[couleur] }}
                />
                <span className="text-[8.5px] uppercase tracking-[0.06em] text-etiquette-sub">
                  {[COULEUR_LABELS[couleur], region].filter(Boolean).join(' · ')}
                </span>
              </span>
            </div>
          )}
        </div>

        {/* ---------- Nom ---------- */}
        <div className="mt-4">
          <h1 className="font-serif text-[1.6rem] font-medium text-ink-bright m-0 leading-tight">
            {bouteille.cuvee_nom}
          </h1>
          <div className="text-[13px] text-muted mt-1">{sousTitre || bouteille.domaine_nom}</div>
        </div>

        {/* ---------- Millésimes en stock ---------- */}
        {millesimes.length > 1 && (
          <div className="flex gap-2 overflow-x-auto no-scrollbar mt-3 pb-1">
            {millesimes.map((b) => (
              <button key={b.id} onClick={() => setSelId(b.id)} className={pillCls(b.id === selId)}>
                {b.millesime ?? 'N.M.'} <span className="opacity-75">×{b.quantite}</span>
              </button>
            ))}
          </div>
        )}

        {/* ---------- Quantité / fenêtre de service ---------- */}
        <div className="grid grid-cols-2 gap-2.5 mt-3.5">
          <InfoTile
            label="Quantité"
            value={`${totalStock} bouteille${totalStock > 1 ? 's' : ''}`}
          />
          <InfoTile label="Fenêtre de service" value={apogee ?? '—'} gold />
        </div>

        {/* ---------- Maturité ---------- */}
        <div className="mt-2.5">
          <MaturiteGauge statut={selected.statut} />
        </div>

        {/* ---------- Prix d'achat / valeur marché ---------- */}
        <div className="grid grid-cols-2 gap-2.5 mt-2.5">
          <InfoTile
            label="Prix d'achat"
            value={formatPrix(selected.prix_achat ?? fiche?.prix_achat_moyen ?? null)}
          />
          <InfoTile
            label="Valeur marché"
            value={
              fiche?.prix_marche
                ? formatFourchette(fiche.prix_marche.min, fiche.prix_marche.max, fiche.prix_marche.devise)
                : '—'
            }
            gold
          />
        </div>

        {/* ---------- Emplacement ---------- */}
        <button
          onClick={() => onOptions(selected)}
          className={`${tileCls} w-full mt-2.5 text-left active:scale-[0.99] transition`}
        >
          <div className="text-[11px] text-muted">Emplacement</div>
          <div className="text-[14px] text-ink mt-0.5">
            {selected.emplacement_chemin || 'Non placée — toucher pour déplacer'}
          </div>
        </button>

        {/* ---------- Conseil de dégustation ---------- */}
        <Section title="Conseil de dégustation">
          <div className="grid grid-cols-2 gap-2.5">
            <InfoTile
              label="Température"
              value={
                <>
                  {fiche?.conseil_degustation.temperature ?? '…'}{' '}
                  <span className="text-xs text-muted">°C</span>
                </>
              }
            />
            <InfoTile label="Carafage" value={fiche?.conseil_degustation.carafage ?? '…'} />
          </div>
        </Section>

        {/* ---------- Caractéristiques gustatives ---------- */}
        {fiche && fiche.profil_gustatif.length > 0 && (
          <Section title="Caractéristiques gustatives">
            <div className={`${tileCls} flex flex-col gap-2.5 py-3`}>
              {fiche.profil_gustatif.map((row) => (
                <div key={row.gauche} className="flex items-center gap-2.5 text-xs">
                  <span className="w-16 text-muted-strong text-right shrink-0">{row.gauche}</span>
                  <div className="flex-1 h-1.5 rounded-[3px] bg-track">
                    <div
                      className={`h-full rounded-[3px] ${wineFill}`}
                      style={{ width: `${row.valeur * 100}%` }}
                    />
                  </div>
                  <span className="w-16 text-ink shrink-0">{row.droite}</span>
                </div>
              ))}
            </div>
          </Section>
        )}

        {/* ---------- Description ---------- */}
        {fiche && (fiche.cuvee.description || fiche.cuvee.elaborate || fiche.cuvee.degre_alcool != null) && (
          <Section title="Description du vin">
            {fiche.cuvee.description && (
              <p className="m-0 text-[13px] leading-relaxed text-ink/90">{fiche.cuvee.description}</p>
            )}
            {fiche.cuvee.elaborate && (
              <p className="m-0 text-[13px] leading-relaxed text-muted mt-2">{fiche.cuvee.elaborate}</p>
            )}
            {fiche.cuvee.degre_alcool != null && (
              <div className="text-[13px] text-muted mt-2">
                Degré d'alcool : <span className="text-ink font-medium">{fiche.cuvee.degre_alcool}°</span>
              </div>
            )}
          </Section>
        )}

        {/* ---------- Mets et vins ---------- */}
        {fiche && fiche.accords_mets.length > 0 && (
          <Section title="Mets et vins">
            <div className="flex gap-2 flex-wrap">
              {fiche.accords_mets.map((m) => (
                <span
                  key={m.nom}
                  className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-full border border-line text-xs"
                >
                  <span aria-hidden="true">{m.emoji}</span>
                  {m.nom}
                  {m.confiance != null && (
                    <span className="text-gold-soft font-semibold">{Math.round(m.confiance * 100)}%</span>
                  )}
                </span>
              ))}
            </div>
          </Section>
        )}

        {/* ---------- Cépages ---------- */}
        {fiche && fiche.cuvee.cepages.length > 0 && (
          <Section title="Cépages">
            <div className="flex gap-2 flex-wrap">
              {fiche.cuvee.cepages.map((nom) => (
                <span key={nom} className="px-3.5 py-1.5 rounded-full border border-line text-xs text-ink">
                  {nom}
                </span>
              ))}
            </div>
          </Section>
        )}

        {/* ---------- Stock par millésime ---------- */}
        <Section title="Stock par millésime">
          <div className={`${tileCls} pt-3.5`}>
            <div className="flex items-end justify-around gap-4 h-28">
              {millesimes.map((b) => (
                <div key={b.id} className="flex flex-col items-center justify-end h-full flex-1 max-w-14">
                  <div className="text-[11px] text-gold-soft mb-1.5">×{b.quantite}</div>
                  <div
                    className={`w-full max-w-9 rounded-[6px_6px_2px_2px] ${wineFill}`}
                    style={{ height: `${(b.quantite / maxStock) * 100}%`, minHeight: 8 }}
                  />
                  <div className="text-[11px] text-muted-strong mt-1.5">{b.millesime ?? 'N.M.'}</div>
                </div>
              ))}
            </div>
          </div>
        </Section>

        {/* ---------- Note perso / communauté ---------- */}
        {fiche && (
          <Section title="Note perso / communauté">
            <div className="grid grid-cols-2 gap-2.5">
              <button onClick={() => setTasting(true)} className={`${tileCls} text-left active:scale-[0.99] transition`}>
                <div className="text-[11px] text-muted">Ma note</div>
                <div className="text-[18px] mt-0.5 font-semibold text-gold-soft">
                  {fiche.ma_note ? `${fiche.ma_note.note}/5 ★` : 'Noter ce vin'}
                </div>
              </button>
              <div className={tileCls}>
                <div className="text-[11px] text-muted">Communauté</div>
                <div className="text-[18px] mt-0.5 font-semibold text-ink">
                  {fiche.note_communaute ? `${fiche.note_communaute.note}/5` : '—'}
                  {fiche.note_communaute && (
                    <span className="text-[11px] text-muted font-normal"> · {fiche.note_communaute.nb} notes</span>
                  )}
                </div>
              </div>
            </div>
          </Section>
        )}

        {/* ---------- Historique de prix (wineapi, accumulé au fil des synchros) ---------- */}
        <Section title="Historique de prix">
          {dernierPrix ? (
            <div className={`${tileCls} py-3.5`}>
              <div className="flex items-baseline justify-between mb-2">
                <div>
                  <div className="font-serif text-[1.5rem] text-gold leading-none">
                    {formatMontant(dernierPrix.prix_min, dernierPrix.devise)}
                  </div>
                  <div className="text-[11px] text-muted mt-1">Meilleur prix</div>
                </div>
                <div className="text-[11px] text-muted">relevé le {formatDate(dernierPrix.date)}</div>
              </div>
              {histPrix.length >= 2 && <PriceHistoryChart points={histPrix} />}
            </div>
          ) : (
            <p className={`${tileCls} m-0 text-muted text-sm py-3.5`}>
              Pas encore d'historique de prix. Synchronisez la fiche (🔄) pour le construire au fil des relevés.
            </p>
          )}
        </Section>

        {/* ---------- Prix par marchand (wineapi) ---------- */}
        {fiche && fiche.prix_marchands.length > 0 && (
          <Section title="Prix par marchand">
            <div className="glass rounded-[10px] divide-y divide-line/40">
              {fiche.prix_marchands.map((p, i) => {
                const label = p.marchand || 'Marchand'
                const inner = (
                  <>
                    <span className="text-ink text-sm">
                      {label}
                      {p.releve_le && (
                        <span className="block text-muted text-[0.7rem]">relevé le {formatDate(p.releve_le)}</span>
                      )}
                    </span>
                    <span className="font-serif text-gold shrink-0">{formatPrixMarchand(p)}</span>
                  </>
                )
                return p.url ? (
                  <a
                    key={`${label}-${i}`}
                    href={p.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-between px-3.5 py-3 active:bg-white/5"
                  >
                    {inner}
                  </a>
                ) : (
                  <div key={`${label}-${i}`} className="flex items-center justify-between px-3.5 py-3">
                    {inner}
                  </div>
                )
              })}
            </div>
          </Section>
        )}

        {/* ---------- Avis (scores critiques wineapi) ---------- */}
        {fiche && fiche.avis.length > 0 && (
          <Section title="Avis">
            <div className="flex flex-col gap-2">
              {fiche.avis.map((a) => (
                <div key={`${a.reviewer}-${a.date ?? ''}`} className={tileCls}>
                  <div className="flex items-center justify-between">
                    <span className="text-ink text-sm font-medium">{a.reviewer}</span>
                    {a.score != null && <span className="font-serif text-[1.15rem] text-gold">{a.score}</span>}
                  </div>
                  {(a.score_text || a.date) && (
                    <div className="text-muted text-xs mt-0.5">
                      {a.score_text}
                      {a.score_text && a.date ? ' · ' : ''}
                      {a.date ? formatDate(a.date) : ''}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Section>
        )}

        {/* ---------- Historique (ajout / retrait) ---------- */}
        <Section title="Historique (ajout / retrait)">
          {historique.length === 0 ? (
            <p className={`${tileCls} m-0 text-muted text-sm py-3.5`}>
              Aucun mouvement enregistré pour ce vin.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {historique.map((m) => {
                const entree = m.type_mouvement === 'ENTREE'
                return (
                  <div key={m.id} className={`${tileCls} flex justify-between items-center`}>
                    <span className="text-xs text-muted-strong">{formatDateTime(m.date)}</span>
                    <span
                      className={`text-[13px] font-semibold ${entree ? 'text-gold-soft' : 'text-ink'}`}
                    >
                      {entree ? '+' : '−'}
                      {m.quantite} {m.type_mouvement.toLowerCase()}
                      {m.occasion ? ` — ${m.occasion}` : ''}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </Section>

        {/* ---------- Notes personnelles ---------- */}
        <Section title="Notes personnelles">
          {fiche?.ma_note?.commentaire ? (
            <div className={`${tileCls} py-3 text-[13px] leading-relaxed text-muted-strong`}>
              {fiche.ma_note.commentaire}
            </div>
          ) : (
            <button
              onClick={() => setTasting(true)}
              className="w-full py-3.5 rounded-[10px] border border-dashed border-line text-muted text-sm"
            >
              ✏️ Ajouter une note personnelle
            </button>
          )}
        </Section>
      </div>

      {/* ---------- Barre d'action fixe ---------- */}
      <div
        className="bar-bottom border-t border-line/50 fixed bottom-0 left-0 right-0 z-10 flex gap-2.5 px-4 pt-3"
        style={{ paddingBottom: 'calc(14px + env(safe-area-inset-bottom))' }}
      >
        <button
          onClick={() => setTasting(true)}
          className={`flex-[1.4] py-3 rounded-xl font-semibold text-ink-bright text-[13px] ${wineFill} active:scale-[0.985] transition`}
        >
          Enregistrer une dégustation
        </button>
        <button
          onClick={() => onRetirer(selected)}
          className="flex-1 py-3 rounded-xl border border-line text-muted-strong text-[13px] font-semibold active:scale-[0.985] transition"
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
