import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'

type ToastType = 'ok' | 'err' | ''
const ToastCtx = createContext<(msg: string, type?: ToastType) => void>(() => {})
// eslint-disable-next-line react-refresh/only-export-components
export const useToast = () => useContext(ToastCtx)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState('')
  const [type, setType] = useState<ToastType>('')
  const [show, setShow] = useState(false)
  const timer = useRef<number>(0)

  const toast = useCallback((m: string, t: ToastType = '') => {
    setMsg(m)
    setType(t)
    setShow(true)
    clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setShow(false), 3200)
  }, [])

  return (
    <ToastCtx.Provider value={toast}>
      {children}
      <div
        className="fixed z-[60] left-1/2 w-max max-w-[88vw]"
        style={{ top: 'calc(14px + env(safe-area-inset-top))', transform: 'translateX(-50%)' }}
      >
        <div
          className={`glass px-5 py-3 rounded-full text-sm text-center transition-all duration-300 ${
            show ? 'opacity-100 translate-y-0' : 'opacity-0 -translate-y-4 pointer-events-none'
          } ${type === 'err' ? 'border-alerte!' : type === 'ok' ? 'border-ok!' : ''}`}
        >
          {msg}
        </div>
      </div>
    </ToastCtx.Provider>
  )
}
