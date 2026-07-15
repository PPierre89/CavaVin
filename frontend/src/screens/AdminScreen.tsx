import { useCallback, useEffect, useRef, useState } from 'react'
import { api, apiAllPages, errMsg } from '../api'
import { useAuth } from '../auth'
import { formatDate } from '../dates'
import { Card, CardTitle, ConfirmSheet, ghostCls, inputCls, primaryCls, tileCls } from '../ui'

/* ------------------------------------------------------------------ *
 *  Panneau d'administration (réservé au staff).
 *
 *  Tableau de bord du déploiement : aperçu chiffré (comptes, catalogue
 *  mutualisé, stock, activité), état des sources d'enrichissement, et
 *  gestion des comptes (activation, rôle staff, suppression).
 * ------------------------------------------------------------------ */

interface Apercu {
  utilisateurs: { total: number; actifs: number; staff: number }
  catalogue: { domaines: number; cepages: number; cuvees: number; references_lwin: number }
  stock: { lignes: number; unites: number; caves: number; emplacements: number }
  activite: { notes_degustation: number; mouvements: number }
  systeme: {
    version: string
    debug: boolean
    providers: { nom: string; actif: boolean }[]
  }
}

interface Utilisateur {
  id: number
  username: string
  email: string
  is_active: boolean
  is_staff: boolean
  is_superuser: boolean
  date_joined: string
  last_login: string | null
  nb_bouteilles: number
  nb_caves: number
  nb_degustations: number
}

/* Tuile chiffrée compacte (grande valeur + libellé). */
function Stat({ valeur, libelle }: { valeur: number | string; libelle: string }) {
  return (
    <div className={tileCls}>
      <div className="text-[1.4rem] font-serif text-ink-bright leading-none tabular-nums">
        {valeur}
      </div>
      <div className="text-[0.68rem] text-muted uppercase tracking-wide mt-1">{libelle}</div>
    </div>
  )
}

function Badge({ children, ton }: { children: React.ReactNode; ton: 'gold' | 'wine' | 'muted' }) {
  const cls = {
    gold: 'bg-gold/15 text-gold border-gold/30',
    wine: 'bg-wine/25 text-ink-bright border-wine',
    muted: 'bg-transparent text-muted border-line',
  }[ton]
  return (
    <span className={`text-[0.6rem] px-1.5 py-0.5 rounded-full border uppercase tracking-wide ${cls}`}>
      {children}
    </span>
  )
}

interface Parametre {
  cle: string
  secret: boolean
  configure: boolean
  source: 'base' | 'env' | 'absent'
  apercu: string
}

// Libellés lisibles des paramètres pilotables (le reste = nom technique).
const LIBELLES_PARAM: Record<string, string> = {
  ANTHROPIC_API_KEY: 'Clé API Claude (Anthropic)',
  ANTHROPIC_MODEL: 'Modèle Claude',
  WINEAPI_KEY: 'Clé API wineapi.io',
  WINEAPI_BASE_URL: 'URL de base wineapi.io',
}

/* ---------- Configuration des clés d'API (override base > .env) ---------- */
function ConfigurationApi() {
  const [params, setParams] = useState<Parametre[]>([])
  const [saisies, setSaisies] = useState<Record<string, string>>({})
  const [erreur, setErreur] = useState('')
  const [enCours, setEnCours] = useState<string | null>(null)

  const charger = useCallback(async () => {
    try {
      const data = await api<{ parametres: Parametre[] }>('GET', '/api/admin-panel/configuration/')
      setParams(data.parametres)
    } catch (e) {
      setErreur(errMsg(e, 'Impossible de charger la configuration.'))
    }
  }, [])

  useEffect(() => {
    charger()
  }, [charger])

  // Enregistre (valeur non vide) ou efface (valeur vide) un override en base.
  const enregistrer = async (cle: string, valeur: string) => {
    setEnCours(cle)
    setErreur('')
    try {
      const data = await api<{ parametres: Parametre[] }>('PUT', '/api/admin-panel/configuration/', {
        cle,
        valeur,
      })
      setParams(data.parametres)
      setSaisies((s) => ({ ...s, [cle]: '' }))
    } catch (e) {
      setErreur(errMsg(e, 'Enregistrement impossible.'))
    } finally {
      setEnCours(null)
    }
  }

  const badgeSource = (p: Parametre) => {
    if (p.source === 'base') return <Badge ton="wine">Défini ici</Badge>
    if (p.source === 'env') return <Badge ton="muted">Via .env</Badge>
    return <Badge ton="muted">Absent</Badge>
  }

  return (
    <Card>
      <CardTitle>Configuration des API</CardTitle>
      {erreur && <div className="text-sm text-alerte mb-2">{erreur}</div>}
      <p className="text-muted/80 text-xs mb-3">
        Les valeurs saisies ici sont stockées en base et priment sur le fichier <code>.env</code>{' '}
        (modifiables sans redémarrer). Une valeur vide rétablit le repli <code>.env</code>.
      </p>
      {params.map((p) => (
        <div key={p.cle} className="py-3 border-b border-line/40 last:border-0">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <span className="text-ink text-sm font-medium">{LIBELLES_PARAM[p.cle] || p.cle}</span>
            {badgeSource(p)}
          </div>
          {p.configure && (
            <div className="text-muted/70 text-xs mt-0.5 tabular-nums">
              Actuel : {p.apercu || '(défini)'}
            </div>
          )}
          <div className="flex gap-2 mt-2">
            <input
              type={p.secret ? 'password' : 'text'}
              autoComplete="off"
              value={saisies[p.cle] ?? ''}
              onChange={(e) => setSaisies((s) => ({ ...s, [p.cle]: e.target.value }))}
              placeholder={p.secret ? 'Nouvelle clé…' : 'Nouvelle valeur…'}
              className={`${inputCls} flex-1`}
            />
            <button
              onClick={() => enregistrer(p.cle, (saisies[p.cle] ?? '').trim())}
              disabled={enCours === p.cle || !(saisies[p.cle] ?? '').trim()}
              className={`${ghostCls} shrink-0 disabled:opacity-40`}
            >
              Enregistrer
            </button>
          </div>
          {p.source === 'base' && (
            <button
              onClick={() => enregistrer(p.cle, '')}
              disabled={enCours === p.cle}
              className="mt-2 text-xs text-alerte/90 active:scale-[0.985] transition"
            >
              Effacer (revenir au .env)
            </button>
          )}
        </div>
      ))}
    </Card>
  )
}

/* ---------- Import du référentiel LWIN (upload d'un dump XLSX/CSV) ---------- */
function ImportLwin({ total, onImported }: { total?: number; onImported: () => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [fichier, setFichier] = useState<File | null>(null)
  const [enCours, setEnCours] = useState(false)
  const [message, setMessage] = useState('')
  const [erreur, setErreur] = useState('')

  const envoyer = async () => {
    if (!fichier) return
    setEnCours(true)
    setErreur('')
    setMessage('')
    try {
      const form = new FormData()
      form.append('fichier', fichier)
      const res = await api<{ importes: number; total: number }>(
        'POST',
        '/api/admin-panel/import-lwin/',
        form,
      )
      setMessage(`${res.importes} entrées importées — ${res.total} au total en base.`)
      setFichier(null)
      if (inputRef.current) inputRef.current.value = ''
      onImported()
    } catch (e) {
      setErreur(errMsg(e, "Échec de l'import du fichier LWIN."))
    } finally {
      setEnCours(false)
    }
  }

  return (
    <Card>
      <CardTitle>Référentiel LWIN</CardTitle>
      <p className="text-muted/80 text-xs mb-3">
        Dump Liv-ex (<code>.xlsx</code> ou <code>.csv</code>) alimentant le repli d'identification
        gratuit et hors-ligne. L'import est idempotent (mise à jour par code LWIN).
        {typeof total === 'number' && (
          <>
            {' '}
            Actuellement <span className="text-ink tabular-nums">{total}</span> références en base.
          </>
        )}
      </p>
      {erreur && <div className="text-sm text-alerte mb-2">{erreur}</div>}
      {message && <div className="text-sm text-vigne mb-2">{message}</div>}
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xlsm,.csv"
        onChange={(e) => setFichier(e.target.files?.[0] ?? null)}
        className="block w-full text-sm text-muted file:mr-3 file:py-2 file:px-3 file:rounded-lg file:border file:border-line file:bg-transparent file:text-ink file:text-sm"
      />
      <button
        onClick={envoyer}
        disabled={!fichier || enCours}
        className={`${primaryCls} disabled:opacity-40`}
      >
        {enCours ? 'Import en cours…' : 'Importer le référentiel'}
      </button>
    </Card>
  )
}

export default function AdminScreen() {
  const { username } = useAuth()
  const [apercu, setApercu] = useState<Apercu | null>(null)
  const [users, setUsers] = useState<Utilisateur[]>([])
  const [loading, setLoading] = useState(true)
  const [erreur, setErreur] = useState('')
  const [aSupprimer, setASupprimer] = useState<Utilisateur | null>(null)

  const charger = useCallback(async () => {
    setLoading(true)
    setErreur('')
    try {
      const [a, u] = await Promise.all([
        api<Apercu>('GET', '/api/admin-panel/apercu/'),
        apiAllPages<Utilisateur>('/api/admin-panel/utilisateurs/'),
      ])
      setApercu(a)
      setUsers(u)
    } catch (e) {
      setErreur(errMsg(e, "Impossible de charger le panneau d'administration."))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    charger()
  }, [charger])

  // Rafraîchit seulement l'aperçu chiffré (ex. après un import LWIN), sans
  // recharger toute la liste des comptes.
  const rafraichirApercu = useCallback(() => {
    api<Apercu>('GET', '/api/admin-panel/apercu/').then(setApercu).catch(() => {})
  }, [])

  // Bascule un drapeau (is_active / is_staff) et rafraîchit l'aperçu associé.
  const basculer = async (u: Utilisateur, champ: 'is_active' | 'is_staff') => {
    setErreur('')
    try {
      const maj = await api<Utilisateur>('PATCH', `/api/admin-panel/utilisateurs/${u.id}/`, {
        [champ]: !u[champ],
      })
      setUsers((prev) => prev.map((x) => (x.id === u.id ? { ...x, ...maj } : x)))
      // Les compteurs d'en-tête (actifs / staff) dépendent de ces drapeaux.
      api<Apercu>('GET', '/api/admin-panel/apercu/').then(setApercu).catch(() => {})
    } catch (e) {
      setErreur(errMsg(e, 'Action impossible.'))
    }
  }

  const supprimer = async () => {
    if (!aSupprimer) return
    const cible = aSupprimer
    setASupprimer(null)
    setErreur('')
    try {
      await api('DELETE', `/api/admin-panel/utilisateurs/${cible.id}/`)
      setUsers((prev) => prev.filter((x) => x.id !== cible.id))
      api<Apercu>('GET', '/api/admin-panel/apercu/').then(setApercu).catch(() => {})
    } catch (e) {
      setErreur(errMsg(e, 'Suppression impossible.'))
    }
  }

  return (
    <div>
      <h1 className="font-serif text-[1.75rem] text-ink-bright m-0 mb-3 px-0.5 font-medium">
        Administration
      </h1>

      {erreur && (
        <div className="mb-3.5 text-sm text-alerte bg-alerte/10 border border-alerte/30 rounded-xl px-3.5 py-2.5">
          {erreur}
        </div>
      )}

      {loading ? (
        <p className="text-muted text-sm px-0.5">Chargement du panneau…</p>
      ) : (
        <>
          {apercu && (
            <>
              <Card>
                <CardTitle>Comptes</CardTitle>
                <div className="grid grid-cols-3 gap-2">
                  <Stat valeur={apercu.utilisateurs.total} libelle="Comptes" />
                  <Stat valeur={apercu.utilisateurs.actifs} libelle="Actifs" />
                  <Stat valeur={apercu.utilisateurs.staff} libelle="Staff" />
                </div>
              </Card>

              <Card>
                <CardTitle>Catalogue mutualisé</CardTitle>
                <div className="grid grid-cols-2 gap-2">
                  <Stat valeur={apercu.catalogue.cuvees} libelle="Cuvées" />
                  <Stat valeur={apercu.catalogue.domaines} libelle="Domaines" />
                  <Stat valeur={apercu.catalogue.cepages} libelle="Cépages" />
                  <Stat valeur={apercu.catalogue.references_lwin} libelle="Réf. LWIN" />
                </div>
              </Card>

              <Card>
                <CardTitle>Stock &amp; activité</CardTitle>
                <div className="grid grid-cols-3 gap-2">
                  <Stat valeur={apercu.stock.unites} libelle="Bouteilles" />
                  <Stat valeur={apercu.stock.caves} libelle="Caves" />
                  <Stat valeur={apercu.stock.emplacements} libelle="Emplac." />
                  <Stat valeur={apercu.activite.notes_degustation} libelle="Dégust." />
                  <Stat valeur={apercu.activite.mouvements} libelle="Mouvem." />
                  <Stat valeur={apercu.stock.lignes} libelle="Lignes" />
                </div>
              </Card>

              <Card>
                <CardTitle>Système</CardTitle>
                <div className="flex items-center justify-between py-1.5 text-sm">
                  <span className="text-muted">Version</span>
                  <span className="text-ink tabular-nums">v{apercu.systeme.version}</span>
                </div>
                <div className="flex items-center justify-between py-1.5 text-sm border-t border-line/40">
                  <span className="text-muted">Mode debug</span>
                  <span className={apercu.systeme.debug ? 'text-alerte' : 'text-ink'}>
                    {apercu.systeme.debug ? 'Activé' : 'Désactivé'}
                  </span>
                </div>
                <div className="mt-2 pt-2 border-t border-line/40">
                  <div className="text-[0.68rem] text-muted uppercase tracking-wide mb-2">
                    Sources d'enrichissement
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {apercu.systeme.providers.map((p) => (
                      <span
                        key={p.nom}
                        className={`text-xs px-2 py-1 rounded-full border ${
                          p.actif
                            ? 'bg-wine/25 text-ink-bright border-wine'
                            : 'text-muted border-line'
                        }`}
                      >
                        {p.actif ? '● ' : '○ '}
                        {p.nom}
                      </span>
                    ))}
                  </div>
                </div>
              </Card>
            </>
          )}

          <ConfigurationApi />

          <ImportLwin
            total={apercu?.catalogue.references_lwin}
            onImported={rafraichirApercu}
          />

          <Card>
            <CardTitle>Utilisateurs</CardTitle>
            {users.map((u) => {
              const estMoi = u.username === username
              return (
                <div key={u.id} className="py-3 border-b border-line/40 last:border-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-ink font-medium">{u.username}</span>
                    {u.is_superuser && <Badge ton="gold">Super-admin</Badge>}
                    {u.is_staff && !u.is_superuser && <Badge ton="wine">Staff</Badge>}
                    {!u.is_active && <Badge ton="muted">Désactivé</Badge>}
                    {estMoi && <Badge ton="muted">Vous</Badge>}
                  </div>
                  {u.email && <div className="text-muted text-xs mt-0.5">{u.email}</div>}
                  <div className="text-muted/70 text-[0.68rem] mt-1 tabular-nums">
                    {u.nb_bouteilles} bouteille{u.nb_bouteilles > 1 ? 's' : ''} · {u.nb_caves} cave
                    {u.nb_caves > 1 ? 's' : ''} · {u.nb_degustations} dégust. · inscrit le{' '}
                    {formatDate(u.date_joined)}
                  </div>
                  {!estMoi && (
                    <div className="flex gap-2 mt-2.5 flex-wrap">
                      <button
                        onClick={() => basculer(u, 'is_active')}
                        className="px-3 py-1.5 rounded-full border border-line text-muted text-xs active:scale-[0.985] transition"
                      >
                        {u.is_active ? 'Désactiver' : 'Réactiver'}
                      </button>
                      <button
                        onClick={() => basculer(u, 'is_staff')}
                        className="px-3 py-1.5 rounded-full border border-line text-muted text-xs active:scale-[0.985] transition"
                      >
                        {u.is_staff ? 'Retirer staff' : 'Promouvoir staff'}
                      </button>
                      <button
                        onClick={() => setASupprimer(u)}
                        className="px-3 py-1.5 rounded-full border border-alerte/40 text-alerte text-xs active:scale-[0.985] transition"
                      >
                        Supprimer
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </Card>
        </>
      )}

      <ConfirmSheet
        open={!!aSupprimer}
        title="Supprimer ce compte ?"
        message={
          aSupprimer && (
            <>
              Le compte <span className="text-ink">{aSupprimer.username}</span> et toutes ses
              données privées (caves, bouteilles, dégustations) seront définitivement supprimés.
              Cette action est irréversible.
            </>
          )
        }
        confirmLabel="Supprimer le compte"
        onConfirm={supprimer}
        onClose={() => setASupprimer(null)}
      />
    </div>
  )
}
