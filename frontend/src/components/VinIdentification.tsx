import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { errMsg } from '../api'
import { useToast } from '../toast'
import { ghostCls, inputCls, primaryCls } from '../ui'
import {
  decodeBarcodeFromImage,
  identifyByBarcode,
  identifyByLabel,
  identifyByText,
  isValidEan,
  MAX_LABEL_SIZE,
  searchLocalCuvees,
  searchReferentiel,
  type IdentifiedWine,
  type SuggestionReferentiel,
} from '../identification'
import type { Cuvee } from '../types'

/* ------------------------------------------------------------------ *
 *  Module d'identification d'un vin.
 *
 *  Méthode par défaut : la PHOTO DE L'ÉTIQUETTE. Sur mobile, le bouton
 *  principal ouvre directement l'appareil photo (capture arrière) ; on
 *  propose aussi d'importer une image existante depuis la galerie.
 *
 *  Méthodes de repli (dépliables via « Autre méthode ») :
 *    • recherche par nom — dynamique et économe en quota : au fil de la
 *      frappe, on propose les cuvées DÉJÀ en base (filtrage en mémoire,
 *      sans réseau). L'appel à wineapi.io (consommateur de quota) n'est
 *      déclenché qu'explicitement, via 🔎 / « Rechercher en ligne », ou
 *      par Entrée quand aucune cuvée locale ne correspond.
 *    • code-barres : scan « live » (caméra, si contexte sécurisé), sinon PHOTO
 *      du code-barres décodée localement (fonctionne en HTTP / sur iPhone),
 *      avec saisie manuelle en dernier secours.
 *
 *  Découplé du formulaire d'ajout : il remonte le vin identifié via
 *  `onIdentified`, à charge de l'appelant de pré-remplir son formulaire.
 * ------------------------------------------------------------------ */

/**
 * Cycle de vie du scanner caméra (ZXing). Import dynamique pour ne pas alourdir
 * le bundle initial. La caméra est coupée à l'arrêt et au démontage du composant.
 */
function useBarcodeScanner(onDetected: (ean: string) => void) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const controlsRef = useRef<{ stop: () => void } | null>(null)
  const [scanning, setScanning] = useState(false)

  // Réf pour éviter de recréer start() à chaque rendu tout en appelant le dernier handler.
  const onDetectedRef = useRef(onDetected)
  onDetectedRef.current = onDetected

  const stop = useCallback(() => {
    controlsRef.current?.stop()
    controlsRef.current = null
    setScanning(false)
  }, [])

  const start = useCallback(async (): Promise<boolean> => {
    setScanning(true)
    try {
      const { BrowserMultiFormatReader } = await import('@zxing/browser')
      const reader = new BrowserMultiFormatReader()
      controlsRef.current = await reader.decodeFromConstraints(
        { video: { facingMode: 'environment' } },
        videoRef.current!,
        (result, _err, controls) => {
          if (result) {
            controls.stop()
            controlsRef.current = null
            setScanning(false)
            onDetectedRef.current(result.getText())
          }
        },
      )
      return true
    } catch {
      setScanning(false)
      return false
    }
  }, [])

  // Sécurité : coupe la caméra si le composant est démonté pendant un scan.
  useEffect(() => stop, [stop])

  return { videoRef, scanning, start, stop }
}

export function VinIdentification({
  onIdentified,
  cuvees,
}: {
  onIdentified: (wine: IdentifiedWine, sourceLabel: string) => void
  // Catalogue déjà chargé côté client : sert la recherche locale « au fil de la
  // frappe » sans aucun appel réseau (donc sans consommer le quota wineapi).
  cuvees: Cuvee[]
}) {
  const toast = useToast()
  // Deux entrées fichier distinctes : l'une ouvre l'appareil photo (capture),
  // l'autre pioche dans la galerie (sans capture).
  const cameraRef = useRef<HTMLInputElement>(null)
  const galleryRef = useRef<HTMLInputElement>(null)
  // Capture d'une photo du code-barres (repli iPhone / HTTP où le scan live échoue).
  const barcodeCamRef = useRef<HTMLInputElement>(null)
  const [search, setSearch] = useState('')
  // Champ de recherche actif : conditionne l'affichage des suggestions.
  const [focused, setFocused] = useState(false)
  // Replis (nom / code-barres) masqués par défaut : la photo d'étiquette prime.
  const [fallbackOpen, setFallbackOpen] = useState(false)
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

  /** Identifie un vin à partir d'un code-barres (scan live, photo ou saisie). */
  const runBarcode = useCallback(
    (ean: string) =>
      run(
        'barcode',
        () => identifyByBarcode(ean),
        (w) => (w.source === 'local' ? 'Reconnu (déjà en base)' : 'Reconnu'),
        "Vin non reconnu. Essaie 🏷️ Photographier l'étiquette.",
      ),
    [run],
  )

  const scanner = useBarcodeScanner(runBarcode)

  async function startScan() {
    // Le scan « live » (getUserMedia) exige un contexte sécurisé (HTTPS) : sur
    // iPhone servi en HTTP, il échoue. On invite alors à utiliser la capture
    // d'une PHOTO du code-barres (bouton dédié), qui fonctionne partout, plutôt
    // que d'imposer directement la saisie manuelle.
    if (!(await scanner.start())) {
      toast('Scan live indisponible — utilise « 🏷️ Photographier le code-barres ».', 'err')
    }
  }

  /** Décode localement une photo du code-barres puis lance l'identification. */
  async function onBarcodePhoto(e: React.ChangeEvent<HTMLInputElement>) {
    const input = e.currentTarget
    const file = input.files?.[0]
    input.value = ''
    if (!file) return
    if (file.size > MAX_LABEL_SIZE) return toast('Photo trop lourde (10 Mo max).', 'err')
    setPending('barcode')
    let ean: string
    try {
      ean = (await decodeBarcodeFromImage(file)).trim()
    } catch {
      setPending(null)
      return toast('Code-barres illisible — réessaie ou saisis-le à la main.', 'err')
    }
    setPending(null)
    if (!isValidEan(ean)) return toast('Code-barres invalide.', 'err')
    runBarcode(ean)
  }

  function manualEntry() {
    const raw = window.prompt('Saisis le code-barres (8 à 14 chiffres) :', '')
    if (raw === null) return
    const ean = raw.trim()
    if (!isValidEan(ean)) return toast('Code-barres invalide.', 'err')
    runBarcode(ean)
  }

  /** Traite une photo d'étiquette (appareil photo ou galerie). */
  function onLabelFile(e: React.ChangeEvent<HTMLInputElement>) {
    const input = e.currentTarget
    const file = input.files?.[0]
    input.value = ''
    if (!file) return
    if (file.size > MAX_LABEL_SIZE) return toast('Photo trop lourde (10 Mo max).', 'err')
    run(
      'label',
      () => identifyByLabel(file),
      'Identifié',
      "Vin non identifié sur l'étiquette. Essaie une photo plus nette.",
    )
  }

  const q = search.trim()
  // Suggestions locales (mémoire, zéro appel réseau) recalculées à chaque frappe.
  const matches = useMemo(() => searchLocalCuvees(cuvees, q), [cuvees, q])

  // Recherche dynamique dans le référentiel LWIN local (côté serveur, aucun
  // quota externe) : déclenchée au fil de la frappe avec un debounce, le
  // dernier mot étant traité comme un préfixe (« marg » -> « Margaux »).
  const [referentiel, setReferentiel] = useState<SuggestionReferentiel[]>([])
  useEffect(() => {
    if (q.length < 2) {
      setReferentiel([])
      return
    }
    let annule = false
    const timer = setTimeout(async () => {
      try {
        const resultats = await searchReferentiel(q)
        if (!annule) setReferentiel(resultats)
      } catch {
        // Référentiel non importé / réseau : la recherche locale reste servie.
        if (!annule) setReferentiel([])
      }
    }, 300)
    return () => {
      annule = true
      clearTimeout(timer)
    }
  }, [q])

  // Écarte les suggestions du référentiel déjà présentes dans le catalogue
  // local affiché au-dessus (même code LWIN).
  const suggestionsRef = useMemo(() => {
    const locaux = new Set(matches.map((c) => c.lwin_code).filter(Boolean))
    return referentiel.filter((s) => !locaux.has(s.lwin)).slice(0, 4)
  }, [referentiel, matches])

  /** Sélection d'une suggestion du référentiel : résolution locale par code
   *  LWIN côté serveur (cascade externe court-circuitée, aucun quota). */
  function pickReferentiel(s: SuggestionReferentiel) {
    setSearch('')
    setFocused(false)
    run(
      'text',
      () => identifyByText(q, s.lwin),
      'Identifié (référentiel)',
      'Vin non identifié. Ajoute-le manuellement.',
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
    run('text', () => identifyByText(q), 'Identifié', 'Vin non identifié. Ajoute-le manuellement.')
  }

  // Entrée : on privilégie la première suggestion locale (gratuite) ; à défaut,
  // seulement, on bascule sur la recherche en ligne.
  function onSearchEnter() {
    if (matches.length) pickLocal(matches[0])
    else searchOnline()
  }

  if (scanner.scanning) {
    return (
      <div>
        <video
          ref={scanner.videoRef}
          playsInline
          muted
          className="w-full rounded-xl bg-black aspect-[4/3] object-cover"
        />
        <p className="text-muted text-sm text-center mt-2">Vise le code-barres</p>
        <button onClick={scanner.stop} className={`${ghostCls} w-full mt-2`}>
          Annuler
        </button>
      </div>
    )
  }

  const busy = pending !== null

  return (
    <>
      {/* Méthode par défaut : photo de l'étiquette (ouvre l'appareil photo). */}
      <button
        onClick={() => cameraRef.current?.click()}
        disabled={busy}
        className={`${primaryCls.replace('mt-4', 'mt-0')} disabled:opacity-60`}
      >
        {pending === 'label' ? "🏷️ Identification de l'étiquette…" : "🏷️ Photographier l'étiquette"}
      </button>
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
        onChange={onLabelFile}
      />
      {/* Galerie (sans capture) — importer une photo existante. */}
      <input
        ref={galleryRef}
        type="file"
        accept="image/jpeg,image/png"
        className="sr-only"
        onChange={onLabelFile}
      />

      {/* Replis : recherche par nom ou code-barres, masqués par défaut. */}
      <button
        onClick={() => setFallbackOpen((v) => !v)}
        className="w-full mt-3 text-xs text-muted flex items-center justify-center gap-1"
      >
        <span className="underline">Autre méthode : nom ou code-barres</span>
        <span className={`transition-transform ${fallbackOpen ? 'rotate-180' : ''}`}>▾</span>
      </button>

      {fallbackOpen && (
        <div className="mt-2">
          <div className="relative">
            <div className="flex gap-2">
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
              <button
                onClick={searchOnline}
                disabled={busy}
                className={`${ghostCls} shrink-0 disabled:opacity-60`}
              >
                {pending === 'text' ? '⏳' : '🔎'}
              </button>
            </div>

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
                {suggestionsRef.length > 0 && (
                  <div className="px-3.5 pt-2 pb-1 text-[0.68rem] uppercase tracking-wider text-muted border-t border-line/40">
                    Référentiel
                  </div>
                )}
                {suggestionsRef.map((s) => (
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
                    </div>
                  </button>
                ))}
                {q.length >= 2 ? (
                  <button
                    onMouseDown={(e) => (e.preventDefault(), searchOnline())}
                    disabled={busy}
                    className="w-full text-left px-3.5 py-2.5 text-sm text-gold hover:bg-white/5 transition disabled:opacity-60"
                  >
                    🌐 Rechercher « {q} » en ligne
                  </button>
                ) : (
                  matches.length === 0 && (
                    <div className="px-3.5 py-2.5 text-muted text-sm">Continue à taper…</div>
                  )
                )}
              </div>
            )}
          </div>

          <button
            onClick={startScan}
            disabled={busy}
            className={`${ghostCls} w-full mt-2 disabled:opacity-60`}
          >
            📷 Scanner le code-barres
          </button>
          {/* Repli universel (iPhone / HTTP) : photo du code-barres décodée en local. */}
          <button
            onClick={() => barcodeCamRef.current?.click()}
            disabled={busy}
            className={`${ghostCls} w-full mt-2 disabled:opacity-60`}
          >
            {pending === 'barcode' ? '🏷️ Lecture du code-barres…' : '🏷️ Photographier le code-barres'}
          </button>
          <input
            ref={barcodeCamRef}
            type="file"
            accept="image/jpeg,image/png"
            capture="environment"
            className="sr-only"
            onChange={onBarcodePhoto}
          />
          <button
            onClick={manualEntry}
            disabled={busy}
            className="w-full mt-2 text-xs text-muted underline"
          >
            saisir le code-barres à la main
          </button>
        </div>
      )}
    </>
  )
}
