import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api, apiAllPages } from './api'
import type { Bouteille, Cave, Couleur, Emplacement, Mouvement, Rangement } from './types'

interface DataCtx {
  caves: Cave[]
  caveId: number | null
  setCaveId: (id: number) => void
  emplacements: Emplacement[]
  bouteilles: Bouteille[]
  rangements: Rangement[]
  mouvements: Mouvement[]
  loading: boolean
  loadCaves: () => Promise<void>
  refresh: () => Promise<void>
  cuveeColor: (b: Bouteille) => Couleur
}

const Ctx = createContext<DataCtx>(null!)
// eslint-disable-next-line react-refresh/only-export-components
export const useData = () => useContext(Ctx)

export function DataProvider({ children }: { children: ReactNode }) {
  const [caves, setCaves] = useState<Cave[]>([])
  const [caveId, setCaveIdState] = useState<number | null>(null)
  const [emplacements, setEmplacements] = useState<Emplacement[]>([])
  const [bouteilles, setBouteilles] = useState<Bouteille[]>([])
  const [rangements, setRangements] = useState<Rangement[]>([])
  const [mouvements, setMouvements] = useState<Mouvement[]>([])
  const [loading, setLoading] = useState(true)

  // Numéro du dernier chargement demandé : une réponse plus ancienne qui arrive
  // après (changement rapide de cave, refresh concurrent) est ignorée, sinon
  // elle réaffiche les données de la cave précédente.
  const demande = useRef(0)

  const refreshFor = useCallback(async (cid: number | null) => {
    const moi = ++demande.current
    const [emps, btls, rgs, mvts] = await Promise.all([
      cid ? apiAllPages<Emplacement>(`/api/emplacements/?cave=${cid}`) : Promise.resolve([]),
      apiAllPages<Bouteille>('/api/bouteilles/'),
      cid ? apiAllPages<Rangement>(`/api/rangements/?emplacement__cave=${cid}`) : Promise.resolve([]),
      api<{ results?: Mouvement[] } | Mouvement[]>('GET', '/api/mouvements/?ordering=-date'),
    ])
    if (moi !== demande.current) return // une demande plus récente a pris la main
    setEmplacements(emps)
    setBouteilles(btls)
    setRangements(rgs)
    setMouvements((Array.isArray(mvts) ? mvts : mvts.results || []).slice(0, 30))
  }, [])

  const loadCaves = useCallback(async () => {
    const list = await apiAllPages<Cave>('/api/caves/')
    setCaves(list)
    let cid = caveId
    if (list.length && (!cid || !list.find((c) => c.id === cid))) cid = list[0].id
    if (!list.length) cid = null
    setCaveIdState(cid)
    await refreshFor(cid)
  }, [caveId, refreshFor])

  const refresh = useCallback(() => refreshFor(caveId), [caveId, refreshFor])

  const setCaveId = useCallback(
    (id: number) => {
      setCaveIdState(id)
      refreshFor(id)
    },
    [refreshFor],
  )

  useEffect(() => {
    loadCaves().finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // La couleur voyage désormais avec la ligne de stock.
  const cuveeColor = useCallback((b: Bouteille): Couleur => b.couleur ?? 'AUTRE', [])

  return (
    <Ctx.Provider
      value={{
        caves,
        caveId,
        setCaveId,
        emplacements,
        bouteilles,
        rangements,
        mouvements,
        loading,
        loadCaves,
        refresh,
        cuveeColor,
      }}
    >
      {children}
    </Ctx.Provider>
  )
}
