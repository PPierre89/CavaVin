import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { api, apiAllPages } from './api'
import type { Bouteille, Cave, Couleur, Cuvee, Emplacement, Mouvement } from './types'

interface DataCtx {
  caves: Cave[]
  caveId: number | null
  setCaveId: (id: number) => void
  emplacements: Emplacement[]
  bouteilles: Bouteille[]
  cuvees: Cuvee[]
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
  const [cuvees, setCuvees] = useState<Cuvee[]>([])
  const [mouvements, setMouvements] = useState<Mouvement[]>([])
  const [loading, setLoading] = useState(true)

  const refreshFor = useCallback(async (cid: number | null) => {
    const [emps, btls, cvs, mvts] = await Promise.all([
      cid ? apiAllPages<Emplacement>(`/api/emplacements/?cave=${cid}`) : Promise.resolve([]),
      apiAllPages<Bouteille>('/api/bouteilles/'),
      apiAllPages<Cuvee>('/api/cuvees/'),
      api<{ results?: Mouvement[] } | Mouvement[]>('GET', '/api/mouvements/?ordering=-date'),
    ])
    setEmplacements(emps)
    setBouteilles(btls)
    setCuvees(cvs)
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

  const colorMap = useMemo(() => {
    const m = new Map<number, Couleur>()
    cuvees.forEach((c) => m.set(c.id, c.couleur))
    return m
  }, [cuvees])
  const cuveeColor = useCallback((b: Bouteille): Couleur => colorMap.get(b.cuvee) ?? 'AUTRE', [colorMap])

  return (
    <Ctx.Provider
      value={{
        caves,
        caveId,
        setCaveId,
        emplacements,
        bouteilles,
        cuvees,
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
