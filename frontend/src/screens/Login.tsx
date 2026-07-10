import { useState, type FormEvent } from 'react'
import { useAuth } from '../auth'
import { errMsg } from '../api'
import { useToast } from '../toast'
import { Logo, inputCls, primaryCls } from '../ui'
import { APP_VERSION } from '../version'

export default function Login() {
  const { login, register } = useAuth()
  const toast = useToast()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    try {
      if (mode === 'login') await login(username.trim(), password)
      else await register(username.trim(), email.trim(), password)
    } catch (err) {
      toast(errMsg(err, mode === 'login' ? 'Identifiants incorrects.' : 'Inscription impossible.'), 'err')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-[100dvh] flex flex-col items-center justify-center px-6 py-10">
      <Logo className="w-28 h-28 mb-1" />
      <div className="font-serif italic font-semibold text-[2.6rem] leading-none text-wine-soft">
        allée des vins
      </div>
      <div className="text-[0.78rem] tracking-[5px] uppercase text-gold mt-2 mb-7">
        votre cave, sublimée
      </div>

      <form onSubmit={submit} className="glass rounded-card p-5 w-full max-w-sm">
        <h2 className="font-serif text-xl text-center mb-1">
          {mode === 'login' ? 'Connexion' : 'Créer un compte'}
        </h2>
        <p className="text-muted text-sm text-center mb-4">
          {mode === 'login'
            ? 'Accédez à votre cave.'
            : 'Quelques secondes pour démarrer votre cave.'}
        </p>

        <input
          className={inputCls}
          placeholder="Identifiant"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />
        {mode === 'register' && (
          <input
            className={`${inputCls} mt-2.5`}
            type="email"
            placeholder="E-mail (facultatif)"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        )}
        <input
          className={`${inputCls} mt-2.5`}
          type="password"
          placeholder="Mot de passe"
          autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />

        <button className={primaryCls} disabled={busy}>
          {busy ? '…' : mode === 'login' ? 'Entrer' : 'Créer mon compte'}
        </button>

        <button
          type="button"
          className="w-full mt-3.5 text-sm text-muted"
          onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
        >
          {mode === 'login' ? (
            <>
              Pas encore de compte ? <span className="text-gold">Créer un compte</span>
            </>
          ) : (
            <>
              Déjà inscrit ? <span className="text-gold">Se connecter</span>
            </>
          )}
        </button>
      </form>

      <div className="text-[0.62rem] text-muted/60 mt-6 tabular-nums">v{APP_VERSION}</div>
    </div>
  )
}
