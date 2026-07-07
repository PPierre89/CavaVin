import { useRef, useState, type FormEvent } from 'react'
import { api, apiAllPages, errMsg } from '../api'
import { useData } from '../data'
import { useToast } from '../toast'
import { Card, CardTitle, Field, inputCls, primaryCls, ghostCls } from '../ui'
import type { Couleur, Cuvee, Domaine, Statut } from '../types'

type Seg = 'bouteille' | 'emplacement' | 'cave'
interface IdentifyResult {
  cuvee: Cuvee
  confidence?: number | null
  infos?: { region?: string | null }
}

export default function AjouterScreen({
  seg,
  setSeg,
  onDone,
}: {
  seg: Seg
  setSeg: (s: Seg) => void
  onDone: () => void
}) {
  return (
    <div>
      <div className="glass rounded-xl p-1 mb-1.5 flex">
        {(['bouteille', 'emplacement', 'cave'] as Seg[]).map((s) => (
          <button
            key={s}
            onClick={() => setSeg(s)}
            className={`flex-1 py-2.5 rounded-[9px] text-sm capitalize transition ${
              seg === s
                ? 'bg-gradient-to-b from-wine-soft to-wine-deep text-white font-semibold'
                : 'text-muted'
            }`}
          >
            {s === 'cave' ? 'Cave' : s}
          </button>
        ))}
      </div>
      {seg === 'bouteille' && <BottleForm onDone={onDone} />}
      {seg === 'emplacement' && <EmplacementForm onDone={onDone} />}
      {seg === 'cave' && <CaveForm onDone={onDone} />}
    </div>
  )
}

/* ================= Bouteille ================= */
function BottleForm({ onDone }: { onDone: () => void }) {
  const { cuvees, emplacements, caveId, refresh } = useData()
  const toast = useToast()
  const formRef = useRef<HTMLFormElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const rafRef = useRef<number>(0)

  const [cuveeId, setCuveeId] = useState('')
  const [couleur, setCouleur] = useState<Couleur>('ROUGE')
  const [search, setSearch] = useState('')
  const [scanning, setScanning] = useState(false)

  const isNew = !cuveeId

  function applyIdentified(res: IdentifyResult, srcLabel: string) {
    refresh()
    setCuveeId(String(res.cuvee.id))
    if (res.cuvee.couleur) setCouleur(res.cuvee.couleur)
    const conf = res.confidence != null ? ` · ${Math.round(res.confidence * 100)}%` : ''
    const reg = res.infos?.region ? ` (${res.infos.region})` : ''
    toast(`${srcLabel}${conf} : ${res.cuvee.domaine_nom} — ${res.cuvee.nom}${reg}`, 'ok')
  }

  async function resolveBarcode(ean: string) {
    try {
      const res = await api<IdentifyResult & { source: string }>('POST', '/api/scan-code-barres/', {
        code_barres: ean,
      })
      applyIdentified(res, res.source === 'local' ? 'Reconnu (déjà en base)' : 'Reconnu')
    } catch (e) {
      const status = (e as { status?: number }).status
      if (status === 404) toast("Vin non reconnu. Essaie 🏷️ Photographier l'étiquette.", 'err')
      else toast(errMsg(e, 'Erreur pendant la recherche.'), 'err')
    }
  }

  function stopScan() {
    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    setScanning(false)
  }

  function manualEntry() {
    const ean = window.prompt('Saisis le code-barres (8 à 14 chiffres) :', '')
    if (ean === null) return
    const v = ean.trim()
    if (/^\d{8,14}$/.test(v)) resolveBarcode(v)
    else toast('Code-barres invalide.', 'err')
  }

  async function startScan() {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const Detector = (window as any).BarcodeDetector
    if (!Detector) return manualEntry()
    let formats: string[] = []
    try {
      formats = await Detector.getSupportedFormats()
    } catch { /* ignore */ }
    const wanted = ['ean_13', 'ean_8', 'upc_a', 'upc_e'].filter((f) => formats.includes(f))
    if (!wanted.length) return manualEntry()
    const detector = new Detector({ formats: wanted })
    try {
      streamRef.current = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
    } catch {
      toast('Caméra indisponible (autorisation refusée, ou HTTPS requis).', 'err')
      return manualEntry()
    }
    setScanning(true)
    const video = videoRef.current!
    video.srcObject = streamRef.current
    await video.play()
    const tick = async () => {
      if (!streamRef.current) return
      try {
        const codes = await detector.detect(video)
        if (codes.length && codes[0].rawValue) {
          const ean = codes[0].rawValue as string
          stopScan()
          resolveBarcode(ean)
          return
        }
      } catch { /* frame ignorée */ }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }

  async function onFile() {
    const f = fileRef.current?.files?.[0]
    if (fileRef.current) fileRef.current.value = ''
    if (!f) return
    if (f.size > 10 * 1024 * 1024) return toast('Photo trop lourde (10 Mo max).', 'err')
    toast("Identification de l'étiquette en cours…", 'ok')
    const fd = new FormData()
    fd.append('image', f, f.name || 'etiquette.jpg')
    try {
      const res = await api<IdentifyResult & { source: string }>('POST', '/api/scan-etiquette/', fd)
      applyIdentified(res, 'Identifié')
    } catch (e) {
      const status = (e as { status?: number }).status
      if (status === 404) toast("Vin non identifié sur l'étiquette. Essaie une photo plus nette.", 'err')
      else toast(errMsg(e, "Erreur pendant l'identification."), 'err')
    }
  }

  async function doSearch() {
    const q = search.trim()
    if (q.length < 2) return toast('Saisis au moins 2 caractères.', 'err')
    try {
      const res = await api<IdentifyResult & { source: string }>('POST', '/api/identifier-vin/', {
        query: q,
      })
      applyIdentified(res, 'Identifié')
    } catch (e) {
      const status = (e as { status?: number }).status
      if (status === 404) toast('Vin non identifié. Ajoute-le manuellement.', 'err')
      else toast(errMsg(e, "Erreur pendant l'identification."), 'err')
    }
  }

  async function findOrCreateDomaine(nom: string): Promise<number> {
    const list = await apiAllPages<Domaine>(`/api/domaines/?search=${encodeURIComponent(nom)}`)
    const match = list.find((d) => d.nom.toLowerCase() === nom.toLowerCase())
    if (match) return match.id
    const created = await api<Domaine>('POST', '/api/domaines/', { nom })
    return created.id
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const fd = new FormData(formRef.current!)
    const g = (k: string) => String(fd.get(k) || '').trim()
    const apogeeDebut = g('apogee_debut')
    const apogeeFin = g('apogee_fin')
    if (apogeeDebut && apogeeFin && parseInt(apogeeDebut, 10) > parseInt(apogeeFin, 10)) {
      return toast("L'apogée de début doit précéder celle de fin.", 'err')
    }
    try {
      let finalCuvee = cuveeId ? parseInt(cuveeId, 10) : null
      if (!finalCuvee) {
        const domaineNom = g('domaine_nom')
        const cuveeNom = g('cuvee_nom')
        if (!domaineNom || !cuveeNom) {
          return toast('Choisis une cuvée, ou renseigne domaine + nom de cuvée.', 'err')
        }
        const domaineId = await findOrCreateDomaine(domaineNom)
        const cuvee = await api<Cuvee>('POST', '/api/cuvees/', {
          domaine: domaineId,
          nom: cuveeNom,
          couleur,
          appellation: g('appellation'),
        })
        finalCuvee = cuvee.id
      }
      await api('POST', '/api/bouteilles/', {
        cuvee: finalCuvee,
        millesime: g('millesime') ? parseInt(g('millesime'), 10) : null,
        quantite: parseInt(g('quantite') || '1', 10),
        emplacement: g('emplacement') ? parseInt(g('emplacement'), 10) : null,
        statut: g('statut') as Statut,
        prix_achat: g('prix') ? parseFloat(g('prix')) : null,
        date_achat: g('date_achat') || null,
        apogee_debut: apogeeDebut ? parseInt(apogeeDebut, 10) : null,
        apogee_fin: apogeeFin ? parseInt(apogeeFin, 10) : null,
        notes: g('notes'),
      })
      formRef.current!.reset()
      setCuveeId('')
      setCouleur('ROUGE')
      toast('Bouteille ajoutée à la cave. 🍷', 'ok')
      await refresh()
      onDone()
    } catch (err) {
      toast(errMsg(err, "Erreur lors de l'ajout de la bouteille."), 'err')
    }
  }

  return (
    <Card>
      <CardTitle>Ajouter une bouteille</CardTitle>
      {scanning ? (
        <div>
          <video
            ref={videoRef}
            playsInline
            muted
            className="w-full rounded-xl bg-black aspect-[4/3] object-cover"
          />
          <p className="text-muted text-sm text-center mt-2">Vise le code-barres</p>
          <button onClick={stopScan} className={`${ghostCls} w-full mt-2`}>
            Annuler
          </button>
        </div>
      ) : (
        <>
          <button onClick={startScan} className={primaryCls.replace('mt-4', 'mt-0')}>
            📷 Scanner le code-barres
          </button>
          <button onClick={() => fileRef.current?.click()} className={`${primaryCls} mt-2`}>
            🏷️ Photographier l'étiquette
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png"
            capture="environment"
            className="hidden"
            onChange={onFile}
          />
          <div className="flex gap-2 mt-2.5">
            <input
              className={inputCls}
              placeholder="ou rechercher par nom (Petrus 2015)"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), doSearch())}
            />
            <button onClick={doSearch} className={`${ghostCls} shrink-0`}>
              🔎
            </button>
          </div>
        </>
      )}

      <form ref={formRef} onSubmit={submit} className="mt-4">
        <div className="text-[0.7rem] uppercase tracking-widest text-gold border-b border-gold/15 pb-1.5 mb-1">
          Le vin
        </div>
        <Field label="Cuvée">
          <select
            name="cuvee"
            className={inputCls}
            value={cuveeId}
            onChange={(e) => setCuveeId(e.target.value)}
          >
            <option value="">— nouvelle cuvée —</option>
            {cuvees.map((c) => (
              <option key={c.id} value={c.id}>
                {c.domaine_nom} — {c.nom}
              </option>
            ))}
          </select>
        </Field>

        {isNew && (
          <>
            <div className="grid grid-cols-2 gap-2.5">
              <Field label="Domaine">
                <input name="domaine_nom" className={inputCls} placeholder="Domaine Leflaive" />
              </Field>
              <Field label="Nom de cuvée">
                <input name="cuvee_nom" className={inputCls} placeholder="Chablis GC" />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-2.5">
              <Field label="Couleur">
                <select
                  name="couleur_display"
                  className={inputCls}
                  value={couleur}
                  onChange={(e) => setCouleur(e.target.value as Couleur)}
                >
                  <option value="ROUGE">Rouge</option>
                  <option value="BLANC">Blanc</option>
                  <option value="ROSE">Rosé</option>
                  <option value="BULLES">Bulles</option>
                  <option value="AUTRE">Autre</option>
                </select>
              </Field>
              <Field label="Appellation" hint="facultatif">
                <input name="appellation" className={inputCls} placeholder="Chablis GC" />
              </Field>
            </div>
          </>
        )}

        <div className="text-[0.7rem] uppercase tracking-widest text-gold border-b border-gold/15 pb-1.5 mb-1 mt-4">
          La bouteille
        </div>
        <div className="grid grid-cols-2 gap-2.5">
          <Field label="Quantité">
            <input name="quantite" type="number" inputMode="numeric" min={1} defaultValue={1} className={inputCls} />
          </Field>
          <Field label="Statut">
            <select name="statut" className={inputCls} defaultValue="A_GARDER">
              <option value="A_GARDER">À garder</option>
              <option value="A_BOIRE">À boire</option>
              <option value="DEPASSE">Dépassé</option>
            </select>
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-2.5">
          <Field label="Millésime" hint="facultatif">
            <input name="millesime" type="number" inputMode="numeric" placeholder="2018" className={inputCls} />
          </Field>
          <Field label="Emplacement" hint="facultatif">
            <select name="emplacement" className={inputCls} defaultValue="">
              <option value="">— non placée —</option>
              {emplacements.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.chemin}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-2.5">
          <Field label="Prix (€)" hint="facultatif">
            <input name="prix" type="number" inputMode="decimal" step="0.01" placeholder="24.90" className={inputCls} />
          </Field>
          <Field label="Date d'achat" hint="facultatif">
            <input name="date_achat" type="date" className={inputCls} />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-2.5">
          <Field label="Apogée dès" hint="facultatif">
            <input name="apogee_debut" type="number" inputMode="numeric" placeholder="2024" className={inputCls} />
          </Field>
          <Field label="Apogée jusqu'à" hint="facultatif">
            <input name="apogee_fin" type="number" inputMode="numeric" placeholder="2030" className={inputCls} />
          </Field>
        </div>
        <Field label="Notes" hint="facultatif">
          <textarea name="notes" rows={2} className={inputCls} placeholder="Occasion, cadeau, coup de cœur…" />
        </Field>
        {!caveId && (
          <p className="text-muted text-xs mt-3">Astuce : crée une cave et des emplacements pour ranger tes bouteilles.</p>
        )}
        <button className={primaryCls}>Ajouter à la cave</button>
      </form>
    </Card>
  )
}

/* ================= Emplacement ================= */
function EmplacementForm({ onDone }: { onDone: () => void }) {
  const { caveId, emplacements, refresh } = useData()
  const toast = useToast()
  const formRef = useRef<HTMLFormElement>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!caveId) return toast("Crée d'abord une cave.", 'err')
    const fd = new FormData(formRef.current!)
    const g = (k: string) => String(fd.get(k) || '').trim()
    if (!g('nom')) return
    try {
      await api('POST', '/api/emplacements/', {
        cave: caveId,
        nom: g('nom'),
        type_emplacement: g('type_emplacement'),
        parent: g('parent') ? parseInt(g('parent'), 10) : null,
        capacite: g('capacite') ? parseInt(g('capacite'), 10) : null,
      })
      formRef.current!.reset()
      toast('Emplacement ajouté.', 'ok')
      await refresh()
      onDone()
    } catch (err) {
      toast(errMsg(err, "Erreur lors de l'ajout."), 'err')
    }
  }

  return (
    <Card>
      <CardTitle>Ajouter un emplacement</CardTitle>
      <form ref={formRef} onSubmit={submit}>
        <Field label="Nom">
          <input name="nom" required className={inputCls} placeholder="Armoire 1, Clayette 3, B4…" />
        </Field>
        <div className="grid grid-cols-2 gap-2.5">
          <Field label="Type">
            <select name="type_emplacement" className={inputCls} defaultValue="ARMOIRE">
              <option value="ARMOIRE">Armoire</option>
              <option value="CASIER">Casier</option>
              <option value="CLAYETTE">Clayette</option>
              <option value="CAISSE">Caisse bois</option>
              <option value="CASE">Case individuelle</option>
            </select>
          </Field>
          <Field label="Capacité" hint="facultatif">
            <input name="capacite" type="number" inputMode="numeric" min={0} className={inputCls} placeholder="optionnel" />
          </Field>
        </div>
        <Field label="À l'intérieur de">
          <select name="parent" className={inputCls} defaultValue="">
            <option value="">— Racine de la cave —</option>
            {emplacements.map((e) => (
              <option key={e.id} value={e.id}>
                {e.chemin}
              </option>
            ))}
          </select>
        </Field>
        <button className={primaryCls}>Créer l'emplacement</button>
      </form>
    </Card>
  )
}

/* ================= Cave ================= */
function CaveForm({ onDone }: { onDone: () => void }) {
  const { loadCaves } = useData()
  const toast = useToast()
  const formRef = useRef<HTMLFormElement>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const fd = new FormData(formRef.current!)
    const nom = String(fd.get('nom') || '').trim()
    if (!nom) return
    try {
      await api('POST', '/api/caves/', { nom, description: String(fd.get('description') || '').trim() })
      formRef.current!.reset()
      toast('Cave créée.', 'ok')
      await loadCaves()
      onDone()
    } catch (err) {
      toast(errMsg(err, 'Erreur lors de la création.'), 'err')
    }
  }

  return (
    <Card>
      <CardTitle>Nouvelle cave</CardTitle>
      <form ref={formRef} onSubmit={submit}>
        <Field label="Nom">
          <input name="nom" required className={inputCls} placeholder="Cave principale, Garage…" />
        </Field>
        <Field label="Description" hint="facultatif">
          <input name="description" className={inputCls} placeholder="optionnel" />
        </Field>
        <button className={primaryCls}>Créer la cave</button>
      </form>
    </Card>
  )
}
