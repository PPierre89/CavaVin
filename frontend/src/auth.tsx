import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import { api, clearTokens, getAccess, setLogoutHandler, setTokens } from './api'

interface AuthCtx {
  username: string | null
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
  const [ready, setReady] = useState(false)

  useEffect(() => {
    setLogoutHandler(() => setUsername(null))
    async function boot() {
      if (getAccess()) {
        try {
          // Un appel authentifié valide (ou rafraîchit) le token en place.
          await api('GET', '/api/caves/')
          setUsername(localStorage.getItem('adv_user') || 'moi')
        } catch {
          /* token invalide -> écran de connexion */
        }
      }
      setReady(true)
    }
    boot()
  }, [])

  const login = useCallback(async (u: string, p: string) => {
    const data = await api<{ access: string; refresh: string }>('POST', '/api/auth/token/', {
      username: u,
      password: p,
    })
    setTokens(data.access, data.refresh)
    localStorage.setItem('adv_user', u)
    setUsername(u)
  }, [])

  const register = useCallback(async (u: string, email: string, p: string) => {
    const data = await api<{ access: string; refresh: string; username: string }>(
      'POST',
      '/api/auth/register/',
      { username: u, email, password: p },
    )
    setTokens(data.access, data.refresh)
    localStorage.setItem('adv_user', data.username)
    setUsername(data.username)
  }, [])

  const logout = useCallback(() => {
    clearTokens()
    localStorage.removeItem('adv_user')
    setUsername(null)
  }, [])

  return (
    <Ctx.Provider value={{ username, ready, login, register, logout }}>{children}</Ctx.Provider>
  )
}
