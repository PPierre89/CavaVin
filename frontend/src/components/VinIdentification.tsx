import { useCallback, useEffect, useRef, useState } from 'react'
import { errMsg } from '../api'
import { useToast } from '../toast'
import { ghostCls, inputCls, primaryCls } from '../ui'
import {
  identifyByBarcode,
  identifyByLabel,
  identifyByText,
  isValidEan,
  MAX_LABEL_SIZE,
  type IdentifiedWine,
} from '../identification'

/* ------------------------------------------------------------------ *
 *  Module d'identification d'un vin — regroupe les trois méthodes :
 *  scan du code-barres (caméra), photo de l'étiquette (reconnaissance
 *  visuelle) et recherche par nom. Découplé du formulaire d'ajout : il
 *  remonte le vin identifié via `onIdentified`, à charge de l'appelant
 *  de pré-remplir son formulaire.
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
}: {
  onIdentified: (wine: IdentifiedWine, sourceLabel: string) => void
}) {
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [search, setSearch] = useState('')
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

  const scanner = useBarcodeScanner((ean) =>
    run(
      'barcode',
      () => identifyByBarcode(ean),
      (w) => (w.source === 'local' ? 'Reconnu (déjà en base)' : 'Reconnu'),
      "Vin non reconnu. Essaie 🏷️ Photographier l'étiquette.",
    ),
  )

  async function startScan() {
    if (!(await scanner.start())) {
      toast('Caméra indisponible — saisie manuelle.', 'err')
      manualEntry()
    }
  }

  function manualEntry() {
    const raw = window.prompt('Saisis le code-barres (8 à 14 chiffres) :', '')
    if (raw === null) return
    const ean = raw.trim()
    if (!isValidEan(ean)) return toast('Code-barres invalide.', 'err')
    run(
      'barcode',
      () => identifyByBarcode(ean),
      (w) => (w.source === 'local' ? 'Reconnu (déjà en base)' : 'Reconnu'),
      "Vin non reconnu. Essaie 🏷️ Photographier l'étiquette.",
    )
  }

  function onLabelFile() {
    const file = fileRef.current?.files?.[0]
    if (fileRef.current) fileRef.current.value = ''
    if (!file) return
    if (file.size > MAX_LABEL_SIZE) return toast('Photo trop lourde (10 Mo max).', 'err')
    run(
      'label',
      () => identifyByLabel(file),
      'Identifié',
      "Vin non identifié sur l'étiquette. Essaie une photo plus nette.",
    )
  }

  function doSearch() {
    const q = search.trim()
    if (q.length < 2) return toast('Saisis au moins 2 caractères.', 'err')
    run('text', () => identifyByText(q), 'Identifié', 'Vin non identifié. Ajoute-le manuellement.')
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
      <button onClick={startScan} disabled={busy} className={`${primaryCls.replace('mt-4', 'mt-0')} disabled:opacity-60`}>
        📷 Scanner le code-barres
      </button>
      <button
        onClick={() => fileRef.current?.click()}
        disabled={busy}
        className={`${primaryCls} mt-2 disabled:opacity-60`}
      >
        {pending === 'label' ? "🏷️ Identification de l'étiquette…" : "🏷️ Photographier l'étiquette"}
      </button>
      <input
        ref={fileRef}
        type="file"
        accept="image/jpeg,image/png"
        capture="environment"
        className="hidden"
        onChange={onLabelFile}
      />
      <div className="flex gap-2 mt-2.5">
        <input
          className={inputCls}
          placeholder="ou rechercher par nom (Petrus 2015)"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), doSearch())}
        />
        <button onClick={doSearch} disabled={busy} className={`${ghostCls} shrink-0 disabled:opacity-60`}>
          🔎
        </button>
      </div>
      <button onClick={manualEntry} disabled={busy} className="w-full mt-2 text-xs text-muted underline">
        saisir le code-barres à la main
      </button>
    </>
  )
}
