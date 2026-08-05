import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { errMsg } from '../api'
import { useToast } from '../toast'
import { ghostCls, inputCls, primaryCls } from '../ui'
import {
  identifyByPhoto,
  identifyByText,
  MAX_LABEL_SIZE,
  searchCatalogue,
  searchReferentiel,
  type IdentifiedWine,
  type RechercheReferentiel,
  type SuggestionReferentiel,
} from '../identification'
import { COULEUR_VARS, type Couleur, type Cuvee } from '../types'

/* ------------------------------------------------------------------ *
 *  Module d'identification d'un vin — DEUX gestes, pas davantage.
 *
 *  1. LA PHOTO. Un seul bouton, qui ouvre l'appareil photo (capture
 *     arrière) ; « importer » pioche la même chose dans la galerie. Le
 *     tri entre étiquette et code-barres n'est plus demandé à
 *     l'utilisateur : `identifyByPhoto` décode d'abord le code-barres en
 *     local (gratuit, exact) et bascule sur l'étiquette sinon. Cette
 *     capture par input fichier marche partout, y compris hors contexte
 *     sécurisé (HTTP, iPhone) où le scan « live » getUserMedia échouait —
 *     c'est ce qui permet de n'avoir plus qu'un seul chemin photo.
 *
 *  2. LE NOM. Un champ, toujours visible (plus de repli « autre
 *     méthode »), qui sert les trois recours par ordre de coût : les
 *     cuvées DÉJÀ en base et les références LWIN remontent au fil de la
 *     frappe (recherches serveur, aucune source externe) ; la recherche
 *     en ligne (consommatrice de quota) reste une ligne explicite de la
 *     liste ; et la saisie manuelle en est la dernière — c'est ce que
 *     l'on fait quand rien ne correspond, pas une méthode parallèle.
 *
 *  Découplé du formulaire d'ajout : il remonte le vin identifié via
 *  `onIdentified`, à charge de l'appelant de pré-remplir son formulaire.
 * ------------------------------------------------------------------ */

export function VinIdentification({
  onIdentified,
  onManuel,
}: {
  onIdentified: (wine: IdentifiedWine, sourceLabel: string) => void
  /** Dernier recours : créer la fiche à la main, à partir du texte saisi. */
  onManuel: (saisie: string) => void
}) {
  const toast = useToast()
  // Deux entrées fichier distinctes : l'une ouvre l'appareil photo (capture),
  // l'autre pioche dans la galerie (sans capture).
  const cameraRef = useRef<HTMLInputElement>(null)
  const galleryRef = useRef<HTMLInputElement>(null)
  const [search, setSearch] = useState('')
  // Champ de recherche actif : conditionne l'affichage des suggestions.
  const [focused, setFocused] = useState(false)
  // Libellé de l'opération d'identification en cours (null = aucune).
  const [pending, setPending] = useState<string | null>(null)

  /** Exécute une identification et gère état + erreurs de façon uniforme. */
  const run = useCallback(
    async (
      label: string,
      call: () => Promise<IdentifiedWine>,
      sourceLabel: string | ((w: IdentifiedWine) => string),
      notFoundMsg: string,
    ) => {
      setPending(label)
      try {
        const wine = await call()
        onIdentified(wine, typeof sourceLabel === 'function' ? sourceLabel(wine) : sourceLabel)
      } catch (e) {
        const status = (e as { status?: number }).status
        toast(status === 404 ? notFoundMsg : errMsg(e, "Erreur pendant l'identification."), 'err')
      } finally {
        setPending(null)
      }
    },
    [onIdentified, toast],
  )

  /** Traite une photo de bouteille (appareil photo ou galerie). */
  function onPhotoFile(e: React.ChangeEvent<HTMLInputElement>) {
    const input = e.currentTarget
    const file = input.files?.[0]
    input.value = ''
    if (!file) return
    if (file.size > MAX_LABEL_SIZE) return toast('Photo trop lourde (10 Mo max).', 'err')
    // `voie` n'est connue qu'après coup : c'est l'appel qui décide s'il a lu un
    // code-barres ou l'étiquette. Le toast le dit, l'utilisateur n'a rien choisi.
    let voie = ''
    run(
      'photo',
      async () => {
        const res = await identifyByPhoto(file)
        voie = res.voie
        return res.wine
      },
      (w) =>
        voie === 'code-barres'
          ? w.source === 'local'
            ? 'Reconnu au code-barres (déjà en base)'
            : 'Reconnu au code-barres'
          : "Identifié à l'étiquette",
      'Vin non reconnu sur cette photo. Réessaie de plus près, ou cherche-le par son nom.',
    )
  }

  const q = search.trim()

  // Recherche dynamique au fil de la frappe (debounce commun), sur deux fronts
  // 100 % locaux côté serveur — aucun quota externe consommé :
  //  - le catalogue mutualisé (cuvées déjà connues), interrogé en entier ;
  //  - le référentiel LWIN, dont le dernier mot est traité comme un préfixe
  //    (« marg » -> « Margaux »).
  const [matches, setMatches] = useState<Cuvee[]>([])
  const [referentiel, setReferentiel] = useState<RechercheReferentiel>({
    evaluation: null,
    resultats: [],
  })
  useEffect(() => {
    if (q.length < 2) {
      setMatches([])
      setReferentiel({ evaluation: null, resultats: [] })
      return
    }
    let annule = false
    const timer = setTimeout(() => {
      // Les deux recherches sont indépendantes et affichées *séparément* : ni
      // l'échec de l'une (référentiel LWIN non importé, réseau) ni sa lenteur ne
      // doivent retenir l'autre. Les attendre ensemble faisait patienter tout le
      // panneau au rythme de la plus lente.
      searchCatalogue(q)
        .then((r) => !annule && setMatches(r))
        .catch(() => !annule && setMatches([]))
      searchReferentiel(q)
        .then((r) => !annule && setReferentiel(r))
        .catch(() => !annule && setReferentiel({ evaluation: null, resultats: [] }))
    }, 300)
    return () => {
      annule = true
      clearTimeout(timer)
    }
  }, [q])

  // Écarte les suggestions du référentiel déjà présentes dans le catalogue
  // affiché au-dessus (même code LWIN).
  const suggestionsRef = useMemo(() => {
    const locaux = new Set(matches.map((c) => c.lwin_code).filter(Boolean))
    return referentiel.resultats.filter((s) => !locaux.has(s.lwin)).slice(0, 4)
  }, [referentiel, matches])

  // Saisie guidée : quand l'algorithme est sûr de lui (« evaluation: sur »),
  // la meilleure correspondance est proposée en fiche pré-remplie — sauf si
  // elle a été absorbée par le catalogue local affiché au-dessus. Sinon
  // (hésitation), la liste sollicite une vérification manuelle.
  const meilleure =
    referentiel.evaluation === 'sur' &&
    suggestionsRef.length > 0 &&
    suggestionsRef[0].lwin === referentiel.resultats[0]?.lwin
      ? suggestionsRef[0]
      : null
  const autresSuggestions = meilleure ? suggestionsRef.slice(1) : suggestionsRef

  /** Sélection d'une suggestion du référentiel : résolution locale par code
   *  LWIN côté serveur (cascade externe court-circuitée, aucun quota). */
  function pickReferentiel(s: SuggestionReferentiel) {
    setSearch('')
    setFocused(false)
    run(
      'text',
      () => identifyByText(q, s.lwin),
      'Identifié (référentiel)',
      'Vin non identifié. Saisis-le à la main.',
    )
  }

  /** Sélection d'une cuvée déjà en base : aucun appel externe, aucun quota. */
  function pickLocal(c: Cuvee) {
    setSearch('')
    setFocused(false)
    onIdentified({ source: 'local', cuvee: c, infos: { region: c.region ?? null } }, 'Déjà en base')
  }

  /** Recherche en ligne (wineapi.io) — explicite, car elle consomme le quota. */
  function searchOnline() {
    if (q.length < 2) return toast('Saisis au moins 2 caractères.', 'err')
    setFocused(false)
    run('text', () => identifyByText(q), 'Identifié', 'Vin non identifié. Saisis-le à la main.')
  }

  /** Dernier recours : la fiche est créée à la main, pré-remplie du texte saisi. */
  function saisirManuellement() {
    setFocused(false)
    onManuel(q)
  }

  // Entrée : on privilégie la première suggestion locale (gratuite), puis la
  // fiche proposée par le référentiel quand l'algorithme est sûr (gratuite
  // aussi) ; à défaut seulement, on bascule sur la recherche en ligne.
  function onSearchEnter() {
    if (matches.length) pickLocal(matches[0])
    else if (meilleure) pickReferentiel(meilleure)
    else searchOnline()
  }

  const busy = pending !== null

  return (
    <>
      {/* Geste n°1 : la photo. Un seul bouton — étiquette ou code-barres, c'est
          l'application qui tranche. */}
      <button
        onClick={() => cameraRef.current?.click()}
        disabled={busy}
        className={`${primaryCls.replace('mt-4', 'mt-0')} disabled:opacity-60`}
      >
        {pending === 'photo' ? '📷 Reconnaissance en cours…' : '📷 Photographier la bouteille'}
      </button>
      <p className="text-muted text-xs text-center mt-2">
        Étiquette ou code-barres : la photo suffit, on reconnaît les deux.
      </p>
      <button
        onClick={() => galleryRef.current?.click()}
        disabled={busy}
        className={`${ghostCls} w-full mt-2 disabled:opacity-60`}
      >
        🖼️ Importer une photo
      </button>
      {/* Appareil photo (capture arrière) — méthode principale.
          NB : on utilise `sr-only` (et non `hidden`/display:none) car iOS Safari
          refuse d'ouvrir un input fichier en display:none déclenché par `.click()`. */}
      <input
        ref={cameraRef}
        type="file"
        accept="image/jpeg,image/png"
        capture="environment"
        className="sr-only"
        onChange={onPhotoFile}
      />
      {/* Galerie (sans capture) — importer une photo existante. */}
      <input
        ref={galleryRef}
        type="file"
        accept="image/jpeg,image/png"
        className="sr-only"
        onChange={onPhotoFile}
      />

      <div className="flex items-center gap-3 my-3 text-muted text-[0.68rem] uppercase tracking-[0.14em]">
        <span className="h-px flex-1 bg-line/60" />
        ou
        <span className="h-px flex-1 bg-line/60" />
      </div>

      {/* Geste n°2 : le nom. Toujours visible — c'est le recours quand la photo
          n'est pas possible (bouteille absente, étiquette abîmée). */}
      <div className="relative">
        <input
          className={inputCls}
          placeholder="rechercher par nom (Petrus 2015)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onFocus={() => setFocused(true)}
          // Léger délai : laisse le clic sur une suggestion se déclencher avant
          // la fermeture du panneau.
          onBlur={() => setTimeout(() => setFocused(false), 120)}
          onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), onSearchEnter())}
        />

        {focused && q.length >= 1 && (
          <div className="absolute left-0 right-0 top-full mt-1 z-20 rounded-xl border border-line-strong bg-surface shadow-[0_10px_30px_rgba(0,0,0,0.5)] overflow-hidden">
            {matches.map((c) => (
              <button
                key={c.id}
                // onMouseDown (et non onClick) pour agir avant le blur de l'input.
                onMouseDown={(e) => (e.preventDefault(), pickLocal(c))}
                className="w-full text-left px-3.5 py-2.5 border-b border-line/40 last:border-b-0 hover:bg-white/5 transition"
              >
                <div className="text-muted text-[0.78rem] truncate">{c.domaine_nom}</div>
                <div className="text-ink text-sm truncate">
                  {c.nom}
                  {c.appellation ? ` · ${c.appellation}` : ''}
                </div>
              </button>
            ))}
            {/* Saisie guidée — l'algorithme est sûr : fiche pré-remplie
                proposée directement (domaine, cuvée, millésime, région, et
                l'enrichissement communautaire quand la cuvée est connue). */}
            {meilleure && (
              <div className="border-t border-line/40">
                <div className="px-3.5 pt-2 pb-1 text-[0.68rem] uppercase tracking-wider text-gold">
                  Meilleure correspondance
                </div>
                <button
                  onMouseDown={(e) => (e.preventDefault(), pickReferentiel(meilleure))}
                  disabled={busy}
                  className="w-full text-left px-3.5 py-2.5 hover:bg-white/5 transition disabled:opacity-60"
                >
                  <div className="flex items-center gap-2">
                    <span
                      aria-hidden
                      className="w-2.5 h-2.5 rounded-full shrink-0"
                      style={{
                        background:
                          COULEUR_VARS[meilleure.couleur as Couleur] ?? 'var(--color-autre)',
                      }}
                    />
                    <span className="text-ink text-sm font-medium truncate">
                      {meilleure.vin || meilleure.producteur}
                    </span>
                    {meilleure.millesime != null && (
                      <span className="text-muted text-xs shrink-0">{meilleure.millesime}</span>
                    )}
                  </div>
                  <div className="text-muted text-[0.78rem] truncate">
                    {meilleure.producteur}
                    {meilleure.appellation ? ` · ${meilleure.appellation}` : ''}
                    {meilleure.pays ? ` · ${meilleure.pays}` : ''}
                  </div>
                  {meilleure.en_base && (
                    <div className="text-muted text-[0.78rem] truncate mt-0.5">
                      {[
                        meilleure.en_base.cepages.join(', '),
                        meilleure.en_base.note != null
                          ? `★ ${meilleure.en_base.note.toFixed(1)} (${meilleure.en_base.nb_notes ?? 0} avis)`
                          : '',
                        meilleure.en_base.accords
                          .slice(0, 3)
                          .map((a) => `${a.emoji} ${a.nom}`)
                          .join('  '),
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </div>
                  )}
                  <div className="text-gold text-xs mt-1">✓ Utiliser cette fiche</div>
                </button>
              </div>
            )}
            {autresSuggestions.length > 0 && (
              <div className="px-3.5 pt-2 pb-1 text-[0.68rem] uppercase tracking-wider text-muted border-t border-line/40">
                {meilleure
                  ? 'Autres propositions'
                  : referentiel.evaluation === 'hesitant'
                    ? 'Plusieurs correspondances — vérifie la bonne'
                    : 'Référentiel'}
              </div>
            )}
            {autresSuggestions.map((s) => (
              <button
                key={s.lwin}
                onMouseDown={(e) => (e.preventDefault(), pickReferentiel(s))}
                disabled={busy}
                className="w-full text-left px-3.5 py-2.5 border-b border-line/40 last:border-b-0 hover:bg-white/5 transition disabled:opacity-60"
              >
                <div className="text-muted text-[0.78rem] truncate">
                  {s.producteur}
                  {s.pays ? ` · ${s.pays}` : ''}
                </div>
                <div className="text-ink text-sm truncate">
                  {s.vin || s.producteur}
                  {s.appellation ? ` · ${s.appellation}` : ''}
                  {s.en_base ? ' · déjà enrichi' : ''}
                </div>
              </button>
            ))}
            {/* Les deux recours de fin de liste, dans l'ordre : d'abord la
                recherche en ligne (elle peut encore trouver, mais consomme du
                quota — d'où le geste explicite), puis la saisie manuelle. */}
            {q.length >= 2 ? (
              <button
                onMouseDown={(e) => (e.preventDefault(), searchOnline())}
                disabled={busy}
                className="w-full text-left px-3.5 py-2.5 text-sm text-gold hover:bg-white/5 transition disabled:opacity-60 border-t border-line/40"
              >
                {pending === 'text' ? '⏳ Recherche en ligne…' : `🌐 Rechercher « ${q} » en ligne`}
              </button>
            ) : (
              matches.length === 0 && (
                <div className="px-3.5 py-2.5 text-muted text-sm">Continue à taper…</div>
              )
            )}
            <button
              onMouseDown={(e) => (e.preventDefault(), saisirManuellement())}
              disabled={busy}
              className="w-full text-left px-3.5 py-2.5 text-sm text-muted hover:bg-white/5 transition disabled:opacity-60 border-t border-line/40"
            >
              ✍️ Saisir « {q} » à la main
            </button>
          </div>
        )}
      </div>
    </>
  )
}
