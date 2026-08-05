import { useRef, useState, type FormEvent } from 'react'
import { api, apiAllPages, errMsg } from '../api'
import { useData } from '../data'
import { useToast } from '../toast'
import { Card, CardTitle, Field, SegTabs, inputCls, primaryCls } from '../ui'
import { RackGrid, Slot } from '../components/bottle'
import { VinIdentification } from '../components/VinIdentification'
import type { IdentifiedWine } from '../identification'
import {
  COULEUR_VARS,
  DISPOSITION_LABELS,
  TYPE_LABELS,
  type Couleur,
  type Cuvee,
  type Disposition,
  type Domaine,
  type TypeEmplacement,
} from '../types'

type Seg = 'bouteille' | 'emplacement' | 'cave'

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
      <SegTabs
        className="mb-1.5"
        value={seg}
        onChange={setSeg}
        options={
          [
            ['bouteille', 'Bouteille'],
            ['emplacement', 'Emplacement'],
            ['cave', 'Cave'],
          ] as const
        }
      />
      {seg === 'bouteille' && <BottleForm onDone={onDone} />}
      {seg === 'emplacement' && <EmplacementForm onDone={onDone} />}
      {seg === 'cave' && <CaveForm onDone={onDone} />}
    </div>
  )
}

/* ================= Bouteille ================= */

/** Vin retenu pour la bouteille : une cuvée identifiée (photo ou recherche) ou
 *  une saisie manuelle, dernier recours atteint depuis la recherche — `saisie`
 *  reprend alors le texte tapé pour ne pas le faire retaper. */
type VinChoisi = { mode: 'cuvee'; cuvee: Cuvee } | { mode: 'manuel'; saisie: string }

function BottleForm({ onDone }: { onDone: () => void }) {
  const { emplacements, caveId, refresh } = useData()
  const toast = useToast()
  const formRef = useRef<HTMLFormElement>(null)

  // L'ajout se fait en deux temps : 1. LE VIN — deux gestes seulement, la photo
  // ou le nom (cf. VinIdentification), la saisie manuelle n'étant que le fond de
  // la recherche ; 2. LA BOUTEILLE, réduite à l'essentiel (quantité, millésime,
  // emplacement), les champs secondaires étant repliés sous « Plus de détails ».
  const [vin, setVin] = useState<VinChoisi | null>(null)
  const [couleur, setCouleur] = useState<Couleur>('ROUGE')
  const [quantite, setQuantite] = useState(1)
  const [millesime, setMillesime] = useState('')
  const [detailsOuverts, setDetailsOuverts] = useState(false)

  function resetAll() {
    setVin(null)
    setCouleur('ROUGE')
    setQuantite(1)
    setMillesime('')
    setDetailsOuverts(false)
  }

  // Passe à l'étape bouteille à partir d'un vin identifié (scan, étiquette, nom).
  function applyIdentified(res: IdentifiedWine, srcLabel: string) {
    refresh()
    setVin({ mode: 'cuvee', cuvee: res.cuvee })
    if (res.millesime) setMillesime(String(res.millesime))
    const conf = res.confidence != null ? ` · ${Math.round(res.confidence * 100)}%` : ''
    const reg = res.infos?.region ? ` (${res.infos.region})` : ''
    toast(`${srcLabel}${conf} : ${res.cuvee.domaine_nom} — ${res.cuvee.nom}${reg}`, 'ok')
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
    if (!vin) return
    const fd = new FormData(formRef.current!)
    const g = (k: string) => String(fd.get(k) || '').trim()
    const apogeeDebut = g('apogee_debut')
    const apogeeFin = g('apogee_fin')
    if (apogeeDebut && apogeeFin && parseInt(apogeeDebut, 10) > parseInt(apogeeFin, 10)) {
      return toast("L'apogée de début doit précéder celle de fin.", 'err')
    }
    try {
      let finalCuvee: number
      if (vin.mode === 'cuvee') {
        finalCuvee = vin.cuvee.id
      } else {
        const domaineNom = g('domaine_nom')
        const cuveeNom = g('cuvee_nom')
        if (!domaineNom || !cuveeNom) {
          return toast('Renseigne le domaine et le nom de cuvée.', 'err')
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
        millesime: millesime ? parseInt(millesime, 10) : null,
        quantite,
        emplacement: g('emplacement') ? parseInt(g('emplacement'), 10) : null,
        prix_achat: g('prix') ? parseFloat(g('prix')) : null,
        date_achat: g('date_achat') || null,
        apogee_debut: apogeeDebut ? parseInt(apogeeDebut, 10) : null,
        apogee_fin: apogeeFin ? parseInt(apogeeFin, 10) : null,
        notes: g('notes'),
      })
      resetAll()
      toast('Bouteille ajoutée à la cave. 🍷', 'ok')
      await refresh()
      onDone()
    } catch (err) {
      toast(errMsg(err, "Erreur lors de l'ajout de la bouteille."), 'err')
    }
  }

  // --- Étape 1 : le vin ---
  if (!vin) {
    return (
      <Card>
        <CardTitle>Ajouter une bouteille</CardTitle>
        <VinIdentification
          onIdentified={applyIdentified}
          onManuel={(saisie) => setVin({ mode: 'manuel', saisie })}
        />
      </Card>
    )
  }

  // --- Étape 2 : la bouteille ---
  return (
    <Card>
      <CardTitle>Ajouter une bouteille</CardTitle>

      {vin.mode === 'cuvee' ? (
        // Récapitulatif du vin retenu : l'étape 1 a fait le travail, on ne
        // redemande rien — « changer » ramène à l'identification.
        <div className="glass rounded-2xl px-3.5 py-3 flex items-center gap-3">
          <span
            aria-hidden
            className="w-2.5 h-2.5 rounded-full shrink-0"
            style={{ background: COULEUR_VARS[vin.cuvee.couleur] ?? 'var(--color-autre)' }}
          />
          <div className="min-w-0 flex-1">
            <div className="text-muted text-[0.78rem] truncate">{vin.cuvee.domaine_nom}</div>
            <div className="text-ink text-sm font-medium truncate">
              {vin.cuvee.nom}
              {vin.cuvee.appellation ? ` · ${vin.cuvee.appellation}` : ''}
            </div>
          </div>
          <button type="button" onClick={resetAll} className="text-xs text-gold underline shrink-0">
            changer
          </button>
        </div>
      ) : (
        <div className="flex items-center justify-between">
          <div className="text-[0.7rem] uppercase tracking-[0.1em] text-muted">Saisie manuelle</div>
          <button type="button" onClick={resetAll} className="text-xs text-gold underline">
            changer
          </button>
        </div>
      )}

      <form ref={formRef} onSubmit={submit} className="mt-2">
        {vin.mode === 'manuel' && (
          <>
            <div className="grid grid-cols-2 gap-2.5">
              <Field label="Domaine">
                {/* Pré-rempli du texte cherché : on tape le plus souvent le
                    producteur, et rien ne doit être saisi deux fois. */}
                <input
                  name="domaine_nom"
                  className={inputCls}
                  placeholder="Domaine Leflaive"
                  defaultValue={vin.saisie}
                />
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

        {/* L'essentiel — tout le reste est replié sous « Plus de détails ». */}
        <div className="flex items-center justify-between mt-2">
          <div>
            <div className="text-[0.92rem] font-semibold">Quantité</div>
            <div className="text-muted text-xs">Bouteilles à ajouter</div>
          </div>
          <Stepper value={quantite} onChange={setQuantite} max={99} />
        </div>
        <div className="grid grid-cols-2 gap-2.5 mt-2">
          <Field label="Millésime" hint="facultatif">
            <input
              name="millesime"
              type="number"
              inputMode="numeric"
              placeholder="2018"
              className={inputCls}
              value={millesime}
              onChange={(e) => setMillesime(e.target.value)}
            />
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

        <button
          type="button"
          onClick={() => setDetailsOuverts((v) => !v)}
          className="w-full mt-3 text-xs text-muted flex items-center justify-center gap-1"
        >
          <span className="underline">Plus de détails : prix, apogée, notes</span>
          <span className={`transition-transform ${detailsOuverts ? 'rotate-180' : ''}`}>▾</span>
        </button>

        {detailsOuverts && (
          <>
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
            <p className="text-muted text-xs mt-1">
              Le statut (à garder / à boire / dépassé) est calculé automatiquement à partir du millésime et
              de la couleur. Renseigne l'apogée pour l'affiner.
            </p>
            <Field label="Notes" hint="facultatif">
              <textarea name="notes" rows={2} className={inputCls} placeholder="Occasion, cadeau, coup de cœur…" />
            </Field>
          </>
        )}

        {!caveId && (
          <p className="text-muted text-xs mt-3">Astuce : crée une cave et des emplacements pour ranger tes bouteilles.</p>
        )}
        <button className={primaryCls}>Ajouter à la cave</button>
      </form>
    </Card>
  )
}

/* ================= Emplacement ================= */
const MIN_DIM = 1
const MAX_DIM = 24
const DISPOSITIONS: Disposition[] = ['DECALE_GAUCHE', 'ALIGNE', 'DECALE_DROITE']
// Une « case individuelle » n'a pas de grille : c'est un seul logement.
const GRID_TYPES: TypeEmplacement[] = ['ARMOIRE', 'CASIER', 'CLAYETTE', 'CAISSE']

/** Compteur ± réutilisé pour la largeur et la hauteur de la rangée. */
function Stepper({
  value,
  onChange,
  min = MIN_DIM,
  max = MAX_DIM,
}: {
  value: number
  onChange: (v: number) => void
  min?: number
  max?: number
}) {
  const btn =
    'w-10 h-10 rounded-full border border-line bg-surface text-xl leading-none disabled:opacity-35 active:scale-95 transition'
  return (
    <div className="flex items-center gap-3">
      <button type="button" className={btn} disabled={value <= min} onClick={() => onChange(value - 1)}>
        −
      </button>
      <span className="font-serif text-xl min-w-6 text-center tabular-nums">{value}</span>
      <button type="button" className={btn} disabled={value >= max} onClick={() => onChange(value + 1)}>
        ＋
      </button>
    </div>
  )
}

function EmplacementForm({ onDone }: { onDone: () => void }) {
  const { caveId, emplacements, refresh } = useData()
  const toast = useToast()
  const nomRef = useRef<HTMLInputElement>(null)
  const parentRef = useRef<HTMLSelectElement>(null)

  const [type, setType] = useState<TypeEmplacement>('ARMOIRE')
  const [largeur, setLargeur] = useState(6)
  const [hauteur, setHauteur] = useState(5)
  const [disposition, setDisposition] = useState<Disposition>('ALIGNE')

  const isGrid = GRID_TYPES.includes(type)
  const total = largeur * hauteur

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!caveId) return toast("Crée d'abord une cave.", 'err')
    const nom = (nomRef.current?.value || '').trim()
    if (!nom) return
    const parent = parentRef.current?.value
    try {
      await api('POST', '/api/emplacements/', {
        cave: caveId,
        nom,
        type_emplacement: type,
        parent: parent ? parseInt(parent, 10) : null,
        ...(isGrid
          ? { nb_colonnes: largeur, nb_rangees: hauteur, disposition }
          : { capacite: 1 }),
      })
      if (nomRef.current) nomRef.current.value = ''
      toast('Emplacement ajouté.', 'ok')
      await refresh()
      onDone()
    } catch (err) {
      toast(errMsg(err, "Erreur lors de l'ajout."), 'err')
    }
  }

  return (
    <Card>
      <CardTitle>Personnalisez votre rangée</CardTitle>
      <form onSubmit={submit}>
        <Field label="Nom">
          <input ref={nomRef} name="nom" required className={inputCls} placeholder="Armoire 1, Clayette 3, B4…" />
        </Field>

        <Field label="Type">
          <select
            className={inputCls}
            value={type}
            onChange={(e) => setType(e.target.value as TypeEmplacement)}
          >
            {(Object.keys(TYPE_LABELS) as TypeEmplacement[]).map((t) => (
              <option key={t} value={t}>
                {TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </Field>

        {isGrid ? (
          <>
            {/* Aperçu vivant de la rangée : se met à jour à chaque réglage. */}
            <div className="glass rounded-2xl px-3 py-4 mt-4 flex justify-center">
              <RackGrid
                cols={largeur}
                rows={hauteur}
                disposition={disposition}
                size={22}
                gap={5}
                renderSlot={(i) => <Slot key={i} empty size={22} />}
              />
            </div>

            <div className="flex items-center justify-between mt-4">
              <div>
                <div className="text-[0.92rem] font-semibold">Largeur</div>
                <div className="text-muted text-xs">Bouteilles par rangée</div>
              </div>
              <Stepper value={largeur} onChange={setLargeur} />
            </div>
            <div className="flex items-center justify-between mt-4 pt-4 border-t border-line/40">
              <div>
                <div className="text-[0.92rem] font-semibold">Hauteur</div>
                <div className="text-muted text-xs">Nombre de rangées</div>
              </div>
              <Stepper value={hauteur} onChange={setHauteur} />
            </div>

            <div className="mt-4 pt-4 border-t border-line/40">
              <div className="text-[0.92rem] font-semibold mb-2.5">Disposition</div>
              <div className="grid grid-cols-3 gap-2">
                {DISPOSITIONS.map((d) => (
                  <button
                    type="button"
                    key={d}
                    onClick={() => setDisposition(d)}
                    className={`rounded-xl border px-2 py-3 text-xs leading-tight transition ${
                      disposition === d
                        ? 'border-gold bg-gold/10 text-ink font-semibold'
                        : 'border-line text-muted'
                    }`}
                  >
                    {DISPOSITION_LABELS[d]}
                  </button>
                ))}
              </div>
            </div>

            <p className="text-muted text-xs mt-4">
              Capacité : <span className="text-ink font-semibold">{total}</span> bouteille
              {total > 1 ? 's' : ''} ({largeur} × {hauteur}).
            </p>
          </>
        ) : (
          <p className="text-muted text-xs mt-3">
            Une case individuelle accueille une seule bouteille.
          </p>
        )}

        <Field label="À l'intérieur de">
          <select ref={parentRef} name="parent" className={inputCls} defaultValue="">
            <option value="">— Racine de la cave —</option>
            {emplacements.map((e) => (
              <option key={e.id} value={e.id}>
                {e.chemin}
              </option>
            ))}
          </select>
        </Field>
        <button className={primaryCls}>Enregistrer la rangée</button>
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
