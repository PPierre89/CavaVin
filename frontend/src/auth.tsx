import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import { api, clearTokens, getAccess, setLogoutHandler, setTokens } from './api'

interface Moi {
  username?: string
  is_staff?: boolean
  is_superuser?: boolean
}

interface AuthCtx {
  username: string | null
  isStaff: boolean
  ready: boolean
  login: (u: string, p: string) => Promise<void>
  register: (u: string, email: string, p: string) => Promise<void>
  logout: () => void
}

const Ctx = createContext<AuthCtx>(null!)
// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => useContext(Ctx)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [username, setUsername] = useState<string | null>(null)
  const [isStaff, setIsStaff] = useState(false)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    setLogoutHandler(() => {
      setUsername(null)
      setIsStaff(false)
    })
    async function boot() {
      if (getAccess()) {
        try {
          // Un appel authentifié valide (ou rafraîchit) le token en place et
          // récupère le rôle (staff) pour piloter l'accès au panneau d'admin.
          const moi = await api<Moi>('GET', '/api/auth/me/')
          setUsername(moi?.username || localStorage.getItem('adv_user') || 'moi')
          setIsStaff(!!moi?.is_staff)
        } catch {
          /* token invalide -> écran de connexion */
        }
      }
      setReady(true)
    }
    boot()
  }, [])

  // Récupère le rôle du compte après (ré)authentification. Best-effort : une
  // erreur ne doit pas empêcher la connexion (l'admin restera juste masqué).
  const chargerRole = useCallback(async () => {
    try {
      const moi = await api<Moi>('GET', '/api/auth/me/')
      setIsStaff(!!moi?.is_staff)
    } catch {
      setIsStaff(false)
    }
  }, [])

  const login = useCallback(
    async (u: string, p: string) => {
      const data = await api<{ access: string; refresh: string }>('POST', '/api/auth/token/', {
        username: u,
        password: p,
      })
      setTokens(data.access, data.refresh)
      localStorage.setItem('adv_user', u)
      setUsername(u)
      await chargerRole()
    },
    [chargerRole],
  )

  const register = useCallback(async (u: string, email: string, p: string) => {
    const data = await api<{ access: string; refresh: string; username: string }>(
      'POST',
      '/api/auth/register/',
      { username: u, email, password: p },
    )
    setTokens(data.access, data.refresh)
    localStorage.setItem('adv_user', data.username)
    setUsername(data.username)
    // Un compte fraîchement créé n'est jamais staff.
    setIsStaff(false)
  }, [])

  const logout = useCallback(() => {
    clearTokens()
    localStorage.removeItem('adv_user')
    setUsername(null)
    setIsStaff(false)
  }, [])

  return (
    <Ctx.Provider value={{ username, isStaff, ready, login, register, logout }}>
      {children}
    </Ctx.Provider>
  )
}
