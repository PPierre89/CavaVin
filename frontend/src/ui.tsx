import { useEffect, type ReactNode } from 'react'

/* ---------- Logo verre-à-vin "allée des vins" ---------- */
export function Logo({ className = 'w-8 h-8' }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={`block text-wine ${className}`} aria-hidden="true">
      <path d="M32 39V52" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      <path d="M21 54h22" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      <path
        d="M16 11h32c0 16-8 29-16 29S16 27 16 11Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinejoin="round"
      />
      <g fill="currentColor">
        <circle cx="26" cy="19" r="3.4" />
        <circle cx="34" cy="18" r="3.4" />
        <circle cx="41" cy="20" r="3.2" />
        <circle cx="29.5" cy="25.5" r="3.4" />
        <circle cx="37.5" cy="25" r="3.4" />
        <circle cx="33.5" cy="31.5" r="3.2" />
      </g>
      <path d="M44 9c4-3 8.5-3 11.5 0-2.2 5.2-7.2 6.2-11.5 3Z" className="fill-gold" />
    </svg>
  )
}

/* ---------- Classes réutilisables ---------- */
export const inputCls =
  'w-full text-[16px] px-3.5 py-3 rounded-xl bg-black/30 border border-gold/15 text-ink outline-none transition focus:border-gold/35 focus:ring-2 focus:ring-wine/25 placeholder:text-[#8a7a6d]'
export const labelCls = 'block text-xs mt-3 mb-1.5 text-muted uppercase tracking-wide'
export const primaryCls =
  'w-full mt-4 py-3.5 rounded-xl font-bold text-white bg-gradient-to-b from-wine-soft to-wine-deep shadow-[0_8px_22px_rgba(124,39,64,0.38)] active:scale-[0.985] transition disabled:opacity-60'
export const ghostCls =
  'px-3.5 py-2.5 rounded-xl border border-gold/15 text-muted text-sm bg-transparent active:scale-[0.985] transition'

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`glass rounded-[18px] p-4 mb-3.5 ${className}`}>{children}</div>
}

export function CardTitle({ children }: { children: ReactNode }) {
  return <h2 className="font-serif text-[1.12rem] text-gold m-0 mb-3">{children}</h2>
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <div>
      <label className={labelCls}>
        {label} {hint && <span className="text-[#8a7a6d] normal-case tracking-normal">{hint}</span>}
      </label>
      {children}
    </div>
  )
}

/* ---------- Bottom sheet ---------- */
export function Sheet({
  open,
  onClose,
  children,
}: {
  open: boolean
  onClose: () => void
  children: ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  return (
    <>
      <div
        onClick={onClose}
        className={`fixed inset-0 z-40 bg-black/60 transition-opacity duration-200 ${
          open ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
      />
      <div
        className={`fixed left-0 right-0 bottom-0 z-50 rounded-t-[24px] border border-gold/15 border-b-0 px-5 pt-2.5 max-h-[82vh] overflow-y-auto transition-transform duration-[250ms] ${
          open ? 'translate-y-0' : 'translate-y-[105%]'
        }`}
        style={{
          paddingBottom: 'calc(24px + env(safe-area-inset-bottom))',
          background: 'linear-gradient(180deg, rgba(48,38,33,0.94), rgba(32,24,20,0.97))',
          backdropFilter: 'saturate(1.2) blur(14px)',
          WebkitBackdropFilter: 'saturate(1.2) blur(14px)',
          boxShadow: '0 -14px 40px rgba(0,0,0,0.45)',
        }}
      >
        <div className="w-10 h-1 rounded bg-gold/30 mx-auto my-1 mb-3.5" />
        {children}
      </div>
    </>
  )
}
