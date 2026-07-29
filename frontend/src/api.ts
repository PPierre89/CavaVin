// Client API : JWT (Bearer) avec refresh automatique, sur la même origine que Django.

const ACCESS_KEY = 'adv_access'
const REFRESH_KEY = 'adv_refresh'

export function getAccess(): string | null {
  return localStorage.getItem(ACCESS_KEY)
}
export function getRefresh(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}
export function setTokens(access: string, refresh?: string) {
  localStorage.setItem(ACCESS_KEY, access)
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh)
}
export function clearTokens() {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

// Notifie l'app (AuthProvider) quand la session expire pour de bon.
let onLogout: (() => void) | null = null
export function setLogoutHandler(cb: () => void) {
  onLogout = cb
}

export class ApiError extends Error {
  status: number
  data: unknown
  constructor(status: number, data: unknown) {
    super('API error')
    this.status = status
    this.data = data
  }
}

// Refresh en cours, partagé : l'app lance plusieurs requêtes en parallèle et
// elles expirent ensemble. Sans ce verrou, chacune déclenchait son propre
// refresh — rafale inutile sur le throttle « auth », et course entre les jetons
// tournants renvoyés (le serveur a ROTATE_REFRESH_TOKENS).
let refreshEnCours: Promise<boolean> | null = null

async function demanderRefresh(): Promise<boolean> {
  const refresh = getRefresh()
  if (!refresh) return false
  const res = await fetch('/api/auth/token/refresh/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh }),
  })
  if (!res.ok) return false
  const data = await res.json()
  // Le serveur fait tourner le jeton de refresh : le conserver prolonge la
  // session au fil de l'usage. L'ignorer figeait la session sur le jeton initial,
  // donc déconnectait tout utilisateur au bout de 7 jours même actif.
  setTokens(data.access, data.refresh)
  return true
}

function refreshAccess(): Promise<boolean> {
  refreshEnCours ??= demanderRefresh().finally(() => {
    refreshEnCours = null
  })
  return refreshEnCours
}

async function raw(method: string, path: string, body?: unknown): Promise<Response> {
  const headers: Record<string, string> = {}
  const access = getAccess()
  if (access) headers['Authorization'] = `Bearer ${access}`
  let payload: BodyInit | undefined
  if (body instanceof FormData) {
    payload = body // le navigateur fixe le Content-Type multipart
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  return fetch(path, { method, headers, body: payload, credentials: 'same-origin' })
}

export async function api<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  let res = await raw(method, path, body)
  if (res.status === 401 && getRefresh()) {
    if (await refreshAccess()) {
      res = await raw(method, path, body)
    } else {
      clearTokens()
      onLogout?.()
    }
  }
  const text = await res.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    if (res.status === 401) {
      clearTokens()
      onLogout?.()
    }
    throw new ApiError(res.status, data)
  }
  return data as T
}

interface Page<T> {
  results?: T[]
  next?: string | null
}

export async function apiAllPages<T = unknown>(url: string): Promise<T[]> {
  let results: T[] = []
  let next: string | null = url
  let guard = 0
  while (next && guard < 20) {
    const page: T[] | Page<T> = await api('GET', next)
    if (Array.isArray(page)) return results.concat(page)
    results = results.concat(page.results || [])
    next = page.next ?? null
    guard++
  }
  return results
}

export function errMsg(e: unknown, fallback: string): string {
  if (e instanceof ApiError && e.data) {
    const d = e.data
    if (typeof d === 'string') return d
    if (typeof d === 'object') {
      const obj = d as Record<string, unknown>
      if (typeof obj.detail === 'string') return obj.detail
      const first = Object.values(obj).flat()[0]
      if (typeof first === 'string') return first
    }
  }
  return fallback
}
