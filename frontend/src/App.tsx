import { useState } from 'react'
import { useAuth } from './auth'
import { DataProvider, useData } from './data'
import Login from './screens/Login'
import CaveScreen from './screens/CaveScreen'
import AjouterScreen from './screens/AjouterScreen'
import JournalScreen from './screens/JournalScreen'
import CarnetScreen from './screens/CarnetScreen'
import { Logo } from './ui'

type Tab = 'cave' | 'ajouter' | 'journal' | 'carnet'
type Seg = 'bouteille' | 'emplacement' | 'cave'

const barFilter = 'saturate(1.2) blur(14px)'

function Shell() {
  const { username, logout } = useAuth()
  const { loading } = useData()
  const [tab, setTab] = useState<Tab>('cave')
  const [seg, setSeg] = useState<Seg>('bouteille')

  const goAdd = (s: Seg) => {
    setSeg(s)
    setTab('ajouter')
    window.scrollTo({ top: 0 })
  }
  const goCave = () => {
    setTab('cave')
    window.scrollTo({ top: 0 })
  }

  return (
    <>
      <header
        className="sticky top-0 z-20 flex justify-between items-center px-[18px]"
        style={{
          paddingTop: 'calc(10px + env(safe-area-inset-top))',
          paddingBottom: 10,
          background: 'linear-gradient(180deg, rgba(27,21,18,0.88), rgba(27,21,18,0.58))',
          backdropFilter: barFilter,
          WebkitBackdropFilter: barFilter,
          borderBottom: '1px solid rgba(205,168,106,0.16)',
        }}
      >
        <div className="flex items-center gap-2.5">
          <Logo className="w-[30px] h-[30px]" />
          <div className="flex flex-col leading-none">
            <span className="font-serif italic text-[1.5rem] text-wine-soft">allée</span>
            <span className="text-[0.56rem] tracking-[3px] uppercase text-gold mt-[3px]">des vins</span>
          </div>
        </div>
        <span className="text-[0.75rem] text-muted text-right">
          {username} ·{' '}
          <button onClick={logout} className="text-gold">
            sortir
          </button>
        </span>
      </header>

      <main
        className="max-w-[640px] mx-auto px-3.5 pt-3.5"
        style={{ paddingBottom: 'calc(92px + env(safe-area-inset-bottom))' }}
      >
        {loading ? (
          <div className="text-muted text-center mt-16">Chargement de votre cave…</div>
        ) : (
          <>
            {tab === 'cave' && <CaveScreen onAdd={goAdd} />}
            {tab === 'ajouter' && <AjouterScreen seg={seg} setSeg={setSeg} onDone={goCave} />}
            {tab === 'journal' && <JournalScreen />}
            {tab === 'carnet' && <CarnetScreen />}
          </>
        )}
      </main>

      <nav
        className="fixed bottom-0 left-0 right-0 z-30 flex justify-around"
        style={{
          paddingBottom: 'env(safe-area-inset-bottom)',
          background: 'linear-gradient(180deg, rgba(27,21,18,0.68), rgba(20,15,12,0.96))',
          backdropFilter: barFilter,
          WebkitBackdropFilter: barFilter,
          borderTop: '1px solid rgba(205,168,106,0.16)',
        }}
      >
        {(
          [
            ['cave', '🍷', 'Ma cave'],
            ['ajouter', '＋', 'Ajouter'],
            ['carnet', '📖', 'Carnet'],
            ['journal', '🕘', 'Journal'],
          ] as [Tab, string, string][]
        ).map(([t, ico, lbl]) => (
          <button
            key={t}
            onClick={() => {
              setTab(t)
              window.scrollTo({ top: 0 })
            }}
            className={`flex-1 py-2.5 flex flex-col items-center gap-0.5 text-[0.66rem] transition ${
              tab === t ? 'text-wine-soft' : 'text-muted'
            }`}
          >
            <span className="text-[1.3rem] leading-none">{ico}</span>
            {lbl}
          </button>
        ))}
      </nav>
    </>
  )
}

export default function App() {
  const { ready, username } = useAuth()
  if (!ready) return <div className="min-h-[100dvh] grid place-items-center text-muted">…</div>
  if (!username) return <Login />
  return (
    <DataProvider>
      <Shell />
    </DataProvider>
  )
}
