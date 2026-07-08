import { useEffect, useMemo, useState } from 'react'
import { useData } from '../data'
import { COULEUR_LABELS, type Bouteille, type Couleur } from '../types'

/* ------------------------------------------------------------------ *
 *  Fiche vin plein écran — reproduit la vue détaillée d'un vin :
 *  hero, millésimes, notes & avis, conseil de dégustation, qualité,
 *  caractéristiques gustatives, cépages, mets adaptés, stock, avis.
 *
 *  Le référentiel (couleur, appellation, domaine, millésimes en stock)
 *  vient des données réelles de la cave. Les sections œnologiques
 *  riches (profil gustatif, cépages, accords mets-vins, cote…) sont
 *  indicatives : le backend ne les expose pas encore, on présente donc
 *  un profil type et les emplacements « Oeni+ » à débloquer, comme sur
 *  les maquettes.
 * ------------------------------------------------------------------ */

const HERO_BG: Record<Couleur, string> = {
  ROUGE: 'linear-gradient(160deg, #3a1622, #6d2740 60%, #1b1512)',
  BLANC: 'linear-gradient(160deg, #4a4327, #7c6f3d 60%, #1b1512)',
  ROSE: 'linear-gradient(160deg, #4a2b34, #8e5266 60%, #1b1512)',
  BULLES: 'linear-gradient(160deg, #4a4027, #8a733d 60%, #1b1512)',
  AUTRE: 'linear-gradient(160deg, #2f333a, #52585f 60%, #1b1512)',
}

/* Profil œnologique indicatif d'un grand cru classé du Médoc. */
const PROFIL = {
  temperature: '16-18',
  carafage: '1h-2h',
  qualite: { niveau: 3, label: 'Très bon' }, // 0..4
  gustatif: [
    { g: 'Léger', d: 'Puissant', v: 0.78 },
    { g: 'Souple', d: 'Tannique', v: 0.86 },
    { g: 'Doux', d: 'Acide', v: 0.62 },
  ],
  cepages: [
    { nom: 'Cabernet Sauvignon', pct: 55 },
    { nom: 'Merlot', pct: 35 },
    { nom: 'Cabernet Franc', pct: 6 },
    { nom: 'Petit Verdot', pct: 4 },
  ],
  mets: [
    { nom: 'Bœuf', score: 95, emoji: '🥩' },
    { nom: 'Agneau', score: 90, emoji: '🍖' },
    { nom: 'Fromage affiné', score: 82, emoji: '🧀' },
  ],
  communaute: { note: 3.9, nb: 26 },
}

const AVIS = [
  {
    auteur: 'Laurent',
    date: '16/11/2025',
    note: 4.1,
    millesime: 2016,
    texte:
      "Médoc classique sans plus. Attention aux différents millésimes, trop de disparité à mon goût. Le 2016 est sans aucun doute l'un des meilleurs.",
    likes: 0,
    commentaires: 1,
  },
]

function Stars({ note, size = 16 }: { note: number; size?: number }) {
  return (
    <span className="inline-flex items-center gap-0.5" aria-label={`${note} sur 5`}>
      {[0, 1, 2, 3, 4].map((i) => {
        const fill = Math.max(0, Math.min(1, note - i))
        return (
          <span key={i} className="relative inline-block" style={{ width: size, height: size }}>
            <span className="absolute inset-0 text-gold/25" style={{ fontSize: size, lineHeight: 1 }}>
              ★
            </span>
            <span
              className="absolute inset-0 overflow-hidden text-gold"
              style={{ width: `${fill * 100}%`, fontSize: size, lineHeight: 1 }}
            >
              ★
            </span>
          </span>
        )
      })}
    </span>
  )
}

/* Petite carte « Oeni+ » verrouillée (fonctionnalité premium à venir). */
function OeniCard({ label }: { label: string }) {
  return (
    <div className="glass rounded-2xl px-3.5 py-3">
      <div className="flex items-center gap-1.5 font-serif text-[1.15rem] text-gold">
        Oeni+ <span className="text-[0.9rem]">👑</span>
      </div>
      <div className="text-[0.72rem] text-muted mt-1">{label}</div>
    </div>
  )
}

function ValueCard({ value, label }: { value: string; label: string }) {
  return (
    <div className="glass rounded-2xl px-3.5 py-3">
      <div className="font-serif text-[1.15rem] text-ink">{value}</div>
      <div className="text-[0.72rem] text-muted mt-1">{label}</div>
    </div>
  )
}

function Section({ title, emoji, action, children }: {
  title: string
  emoji?: string
  action?: React.ReactNode
  children: React.ReactNode
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
  const { bouteilles, cuvees, cuveeColor } = useData()
  const couleur = cuveeColor(bouteille)
  const cuvee = cuvees.find((c) => c.id === bouteille.cuvee)
  const appellation = cuvee?.appellation || 'Appellation inconnue'

  // Tous les millésimes en stock de cette cuvée, triés du plus récent au plus ancien.
  const millesimes = useMemo(() => {
    return bouteilles
      .filter((b) => b.cuvee === bouteille.cuvee && b.quantite > 0)
      .sort((a, b) => (b.millesime ?? 0) - (a.millesime ?? 0))
  }, [bouteilles, bouteille.cuvee])

  const [selId, setSelId] = useState(bouteille.id)
  const selected = millesimes.find((b) => b.id === selId) ?? bouteille
  const totalStock = millesimes.reduce((s, b) => s + b.quantite, 0)
  const maxStock = Math.max(1, ...millesimes.map((b) => b.quantite))
  const [tab, setTab] = useState<'millesimes' | 'historique'>('millesimes')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [onClose])

  const share = async () => {
    const txt = `${bouteille.cuvee_nom} — ${bouteille.domaine_nom}`
    if (navigator.share) {
      try {
        await navigator.share({ title: txt, text: txt })
      } catch {
        /* annulé */
      }
    }
  }

  return (
    <div className="fixed inset-0 z-40 overflow-y-auto bg-bg">
      {/* ---------- Hero ---------- */}
      <div
        className="relative"
        style={{
          background: HERO_BG[couleur],
          paddingTop: 'calc(12px + env(safe-area-inset-top))',
        }}
      >
        <div className="flex items-center justify-between px-4">
          <button
            onClick={onClose}
            aria-label="Retour"
            className="w-11 h-11 grid place-items-center rounded-full glass text-ink text-xl"
          >
            ‹
          </button>
          <div className="flex gap-2.5">
            <span className="w-11 h-11 grid place-items-center rounded-full glass text-lg" title="Assistant">
              🧑‍🏫
            </span>
            <button
              onClick={share}
              aria-label="Partager"
              className="w-11 h-11 grid place-items-center rounded-full glass text-ink text-lg"
            >
              ⤴
            </button>
          </div>
        </div>

        <div className="flex flex-col items-center pt-3 pb-8">
          <div className="text-[5.5rem] leading-none drop-shadow-[0_10px_24px_rgba(0,0,0,0.5)]">🍷</div>
          <span
            className="mt-3 px-4 py-1.5 rounded-full text-white text-sm font-semibold"
            style={{ background: 'linear-gradient(180deg, var(--color-wine-soft), var(--color-wine-deep))' }}
          >
            {COULEUR_LABELS[couleur]}
          </span>
          <div className="text-muted text-sm mt-3">{appellation}</div>
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
                tab === t
                  ? 'bg-gradient-to-b from-wine-soft to-wine-deep text-white'
                  : 'text-muted'
              }`}
            >
              {t === 'millesimes' ? 'Millésimes' : 'Historique'}
            </button>
          ))}
        </div>

        {tab === 'historique' ? (
          <Section title="Historique">
            <p className="text-muted text-sm glass rounded-2xl p-4">
              Retrouvez ici les entrées et sorties de ce vin dans votre cave. (Consultez l'onglet
              Journal pour l'ensemble des mouvements.)
            </p>
          </Section>
        ) : (
          <>
            {/* ---------- Vos millésimes ---------- */}
            <Section title="Vos millésimes">
              <div className="flex gap-2 overflow-x-auto no-scrollbar pb-1">
                {millesimes.map((b) => {
                  const on = b.id === selId
                  return (
                    <button
                      key={b.id}
                      onClick={() => setSelId(b.id)}
                      className={`shrink-0 px-5 py-3 rounded-2xl text-left transition border ${
                        on
                          ? 'bg-gradient-to-b from-wine-soft to-wine-deep border-wine text-white'
                          : 'glass border-transparent text-muted'
                      }`}
                    >
                      <div className="font-serif text-[1.25rem] leading-none">
                        {b.millesime ?? 'N.M.'} <span className="text-[0.8rem] opacity-80">(x{b.quantite})</span>
                      </div>
                    </button>
                  )
                })}
              </div>
              <div className="text-wine-soft text-sm mt-2 font-medium">Standard 75cl</div>
            </Section>

            {/* ---------- Cartes valeur ---------- */}
            <div className="grid grid-cols-2 gap-2.5 mt-4">
              <OeniCard label="Maturité" />
              <OeniCard label="Date d'apogée" />
              <ValueCard value="-- €" label="Prix d'achat moyen" />
              <OeniCard label="Valeur actuelle" />
            </div>

            {/* ---------- Note et avis ---------- */}
            <Section title="Note et avis du millésime" emoji="⭐">
              <div className="grid grid-cols-2 gap-2.5">
                <div className="glass rounded-2xl py-4 text-center">
                  <div className="font-serif text-[1.6rem] text-ink flex items-center justify-center gap-1">
                    --/5 <span className="text-gold text-[1.1rem]">★</span>
                  </div>
                  <div className="text-[0.72rem] text-muted mt-1">Ma note</div>
                </div>
                <div className="glass rounded-2xl py-4 text-center">
                  <div className="font-serif text-[1.6rem] text-gold flex items-center justify-center gap-1">
                    {PROFIL.communaute.note}/5 <span className="text-[1.1rem]">★</span>
                  </div>
                  <div className="text-[0.72rem] text-muted mt-1">{PROFIL.communaute.nb} notes</div>
                </div>
              </div>
            </Section>

            {/* ---------- Conseil de dégustation ---------- */}
            <Section title="Conseil de dégustation" emoji="🍷">
              <div className="grid grid-cols-2 gap-2.5">
                <div className="glass rounded-2xl px-4 py-3.5 flex items-center gap-3">
                  <span className="text-2xl">🌡️</span>
                  <div>
                    <div className="font-serif text-[1.3rem] text-ink leading-none">
                      {PROFIL.temperature} <span className="text-xs text-muted">°C</span>
                    </div>
                    <div className="text-[0.72rem] text-muted mt-1">Température</div>
                  </div>
                </div>
                <div className="glass rounded-2xl px-4 py-3.5 flex items-center gap-3">
                  <span className="text-2xl">⏳</span>
                  <div>
                    <div className="font-serif text-[1.3rem] text-ink leading-none">{PROFIL.carafage}</div>
                    <div className="text-[0.72rem] text-muted mt-1">Carafage</div>
                  </div>
                </div>
              </div>
              <button
                className="w-full mt-3 py-3.5 rounded-2xl font-bold text-white flex items-center justify-center gap-2"
                style={{ background: 'linear-gradient(180deg, var(--color-gold), var(--color-gold-soft))' }}
              >
                🍷 Commencer une dégustation
              </button>
            </Section>

            {/* ---------- Tags ---------- */}
            <Section title="Tags" emoji="🏷️" action={<span className="text-gold text-sm">Ajouter</span>}>
              <button className="w-full py-3.5 rounded-2xl border border-dashed border-gold/25 text-muted text-sm">
                🏷️ Ajouter un tag
              </button>
            </Section>

            {/* ---------- Cave ---------- */}
            <Section title="Cave" emoji="🔎" action={<span className="text-gold text-sm">Modifier</span>}>
              <span className="inline-block px-4 py-2.5 rounded-full glass text-muted text-sm">
                {selected.emplacement_chemin || 'Ma cave'} ({totalStock})
              </span>
            </Section>

            {/* ---------- Notes personnelles ---------- */}
            <Section title="Vos notes personnelles" emoji="✍️">
              <button className="w-full py-3.5 rounded-2xl border border-dashed border-gold/25 text-muted text-sm">
                ✏️ Ajouter une note personnelle
              </button>
            </Section>

            {/* ---------- Qualité du millésime ---------- */}
            <Section title="Qualité du millésime :" emoji="🌦️">
              <div className="flex gap-1.5">
                {['#d8695f', '#e0a06a', '#cdd6a8', '#6cae82', '#a9c2a0'].map((c, i) => (
                  <div
                    key={i}
                    className="h-2.5 flex-1 rounded-full transition"
                    style={{ background: c, opacity: i === PROFIL.qualite.niveau ? 1 : 0.35 }}
                  />
                ))}
              </div>
              <div className="text-center text-sm text-ok font-medium mt-2">{PROFIL.qualite.label}</div>
            </Section>

            {/* ---------- Caractéristique gustative ---------- */}
            <Section title="Caractéristique gustative" emoji="🍷">
              <div className="glass rounded-2xl p-4 flex flex-col gap-3.5">
                {PROFIL.gustatif.map((row) => (
                  <div key={row.g} className="flex items-center gap-3 text-sm">
                    <span className="w-16 text-muted text-right shrink-0">{row.g}</span>
                    <div className="flex-1 h-2.5 rounded-full bg-black/30 overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${row.v * 100}%`,
                          background: 'linear-gradient(90deg, var(--color-gold-soft), var(--color-gold))',
                        }}
                      />
                    </div>
                    <span className="w-16 text-ink shrink-0">{row.d}</span>
                  </div>
                ))}
              </div>
            </Section>

            {/* ---------- Cépages ---------- */}
            <Section title="Cépages" emoji="🍇">
              <div className="flex gap-2 overflow-x-auto no-scrollbar pb-1">
                {PROFIL.cepages.map((c) => (
                  <span
                    key={c.nom}
                    className="shrink-0 px-4 py-2.5 rounded-full glass text-sm text-ink"
                  >
                    {c.nom} <span className="text-muted">({c.pct}%)</span>
                  </span>
                ))}
              </div>
            </Section>

            {/* ---------- Mets adaptés ---------- */}
            <Section title="Mets adaptés">
              <div className="flex gap-3 overflow-x-auto no-scrollbar pb-1">
                {PROFIL.mets.map((m) => (
                  <div key={m.nom} className="shrink-0 w-40">
                    <div className="relative h-36 rounded-2xl glass grid place-items-center text-6xl overflow-hidden">
                      {m.emoji}
                      <span className="absolute top-2 right-2 px-2 py-0.5 rounded-full bg-black/55 text-ok text-sm font-bold">
                        {m.score}%
                      </span>
                    </div>
                    <div className="text-center text-sm mt-2 text-ink">{m.nom}</div>
                  </div>
                ))}
              </div>
            </Section>

            {/* ---------- Sections premium ---------- */}
            <Section title="Phase de vieillissement">
              <button
                className="w-full py-3.5 rounded-2xl font-bold text-white"
                style={{ background: 'linear-gradient(180deg, var(--color-gold), var(--color-gold-soft))' }}
              >
                Essayer Oeni+
              </button>
            </Section>

            <Section title="Prix de la bouteille">
              <div className="text-muted text-sm mb-3">Historique de prix : France</div>
              <button
                className="w-full py-3.5 rounded-2xl font-bold text-white"
                style={{ background: 'linear-gradient(180deg, var(--color-gold), var(--color-gold-soft))' }}
              >
                Essayer Oeni+
              </button>
            </Section>

            {/* ---------- Stock par millésimes ---------- */}
            <Section title="Stock par millésimes">
              <div className="glass rounded-2xl p-4">
                <div className="flex items-end justify-around gap-3 h-40">
                  {millesimes.map((b) => (
                    <div key={b.id} className="flex flex-col items-center justify-end h-full flex-1 max-w-16">
                      <div className="text-sm text-ink mb-1">{b.quantite}</div>
                      <div
                        className="w-8 rounded-t-md"
                        style={{
                          height: `${(b.quantite / maxStock) * 100}%`,
                          background: 'linear-gradient(180deg, var(--color-gold), var(--color-gold-soft))',
                        }}
                      />
                      <div className="text-xs text-muted mt-2">{b.millesime ?? 'N.M.'}</div>
                    </div>
                  ))}
                </div>
              </div>
            </Section>

            {/* ---------- Avis utilisateurs ---------- */}
            <Section title="Avis utilisateurs" emoji="👥">
              {AVIS.map((a, i) => (
                <div key={i} className="mb-3">
                  <div className="flex items-center gap-3 mb-2">
                    <span className="w-10 h-10 rounded-full grid place-items-center bg-wine/30 text-lg">🧑</span>
                    <div>
                      <div className="text-ink font-medium leading-tight">{a.auteur}</div>
                      <div className="text-muted text-xs">{a.date}</div>
                    </div>
                  </div>
                  <div className="glass rounded-2xl p-4">
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-1.5 text-ink font-medium">
                        <span className="text-gold">★</span> Évaluation
                      </span>
                      <span className="font-serif text-[1.25rem] text-ink">{a.note}</span>
                    </div>
                    <div className="flex items-center justify-between mt-1">
                      <span className="text-muted text-sm">Millésime {a.millesime}</span>
                      <Stars note={a.note} />
                    </div>
                    <p className="text-sm text-ink/90 mt-3 leading-relaxed">{a.texte}</p>
                    <div className="flex items-center gap-4 text-muted text-sm mt-3">
                      <span>🤍 {a.likes}</span>
                      <span>💬 {a.commentaires}</span>
                    </div>
                  </div>
                </div>
              ))}
              <button className="w-full mt-1 py-3 rounded-full border border-gold/20 text-muted text-sm">
                Voir les autres avis
              </button>
            </Section>
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
        <button
          onClick={() => onOptions(selected)}
          className="flex-1 py-3.5 rounded-full font-bold text-white"
          style={{ background: 'linear-gradient(180deg, var(--color-gold), var(--color-gold-soft))' }}
        >
          Autres options
        </button>
        <button
          onClick={() => onRetirer(selected)}
          className="flex-1 py-3.5 rounded-full font-semibold text-muted glass"
        >
          Retirer
        </button>
      </div>
    </div>
  )
}
