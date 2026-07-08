import { useEffect, useState } from 'react'
import { api, errMsg } from '../api'
import { useToast } from '../toast'
import { Sheet, inputCls, labelCls, primaryCls } from '../ui'

/* ------------------------------------------------------------------ *
 *  Saisie d'une dégustation (carnet). Bottom sheet ouvert depuis la
 *  fiche vin : note /5, commentaire, curseurs optionnels (acidité,
 *  tanin, fruit) et date. POST /api/notes-degustation/.
 * ------------------------------------------------------------------ */

function StarInput({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  return (
    <div className="flex gap-1.5 text-[2rem] leading-none">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          aria-label={`${n} sur 5`}
          onClick={() => onChange(n === value ? 0 : n)}
          className={n <= value ? 'text-gold' : 'text-gold/25'}
        >
          ★
        </button>
      ))}
    </div>
  )
}

function Cursor({ label, value, onChange }: { label: string; value: number; onChange: (n: number) => void }) {
  return (
    <div>
      <div className="flex justify-between text-xs text-muted mb-1">
        <span>{label}</span>
        <span>{value}/5</span>
      </div>
      <input
        type="range"
        min={0}
        max={5}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[color:var(--color-wine-soft)]"
      />
    </div>
  )
}

export function TastingSheet({
  open,
  onClose,
  cuveeId,
  cuveeNom,
  millesime,
  onSaved,
}: {
  open: boolean
  onClose: () => void
  cuveeId: number
  cuveeNom: string
  millesime: number | null
  onSaved: () => void
}) {
  const toast = useToast()
  const [note, setNote] = useState(0)
  const [commentaire, setCommentaire] = useState('')
  const [acidite, setAcidite] = useState(0)
  const [tanin, setTanin] = useState(0)
  const [fruit, setFruit] = useState(0)
  const [saving, setSaving] = useState(false)

  // Réinitialise à chaque ouverture.
  useEffect(() => {
    if (open) {
      setNote(0)
      setCommentaire('')
      setAcidite(0)
      setTanin(0)
      setFruit(0)
    }
  }, [open])

  async function save() {
    if (note === 0) return toast('Donne une note (1 à 5 étoiles).', 'err')
    setSaving(true)
    try {
      await api('POST', '/api/notes-degustation/', {
        cuvee: cuveeId,
        millesime,
        note: String(note),
        commentaire: commentaire.trim(),
        // Curseurs facultatifs : 0 = non renseigné.
        acidite: acidite || null,
        tanin: tanin || null,
        fruit: fruit || null,
      })
      toast('Dégustation ajoutée au carnet. 🍷', 'ok')
      onSaved()
      onClose()
    } catch (e) {
      toast(errMsg(e, "Impossible d'enregistrer la dégustation."), 'err')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Sheet open={open} onClose={onClose}>
      <h3 className="font-serif text-[1.35rem] m-0">Nouvelle dégustation</h3>
      <div className="text-muted text-sm mt-1">
        {cuveeNom}
        {millesime ? ` · ${millesime}` : ''}
      </div>

      <label className={labelCls}>Ma note</label>
      <StarInput value={note} onChange={setNote} />

      <label className={`${labelCls} mt-4`}>Commentaire</label>
      <textarea
        className={inputCls}
        rows={3}
        placeholder="Nez, bouche, accord, occasion…"
        value={commentaire}
        onChange={(e) => setCommentaire(e.target.value)}
      />

      <label className={`${labelCls} mt-4`}>Profil (facultatif)</label>
      <div className="flex flex-col gap-3">
        <Cursor label="Acidité" value={acidite} onChange={setAcidite} />
        <Cursor label="Tanin" value={tanin} onChange={setTanin} />
        <Cursor label="Fruit" value={fruit} onChange={setFruit} />
      </div>

      <button onClick={save} disabled={saving} className={`${primaryCls} disabled:opacity-60`}>
        {saving ? 'Enregistrement…' : 'Enregistrer la dégustation'}
      </button>
    </Sheet>
  )
}
