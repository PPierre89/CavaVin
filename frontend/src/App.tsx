import { useState } from 'react'
import { useAuth } from './auth'
import { DataProvider, useData } from './data'
import Login from './screens/Login'
import AccueilScreen from './screens/AccueilScreen'
import CaveScreen from './screens/CaveScreen'
import AjouterScreen from './screens/AjouterScreen'
import CarnetScreen from './screens/CarnetScreen'
import MesVinsScreen from './screens/MesVinsScreen'
import { Logo, Sheet } from './ui'
import { APP_VERSION } from './version'

/* Navigation à quatre onglets (maquette « CavaVin Écrans ») ; l'ajout n'est
   plus un onglet mais une vue poussée depuis l'accueil ou « Mes vins ». */
type Tab = 'accueil' | 'vin' | 'cave' | 'carnet'
type View = Tab | 'ajouter'
type Seg = 'bouteille' | 'emplacement' | 'cave'

/* ---------- Icônes géométriques de la barre d'onglets ---------- */
function TabIcon({ tab, active }: { tab: Tab; active: boolean }) {
  const fill = active ? 'bg-wine' : 'bg-placeholder'
  const line = active ? 'border-wine-soft' : 'border-placeholder'
  const bar = active ? 'bg-wine-soft' : 'bg-placeholder'
  if (tab === 'accueil') {
    return <span className={`w-[18px] h-[18px] rounded-[5px] ${fill}`} />
  }
  if (tab === 'vin') {
    return <span className={`w-2 h-4 rounded-[2px_2px_5px_5px] ${fill}`} />
  }
  if (tab === 'cave') {
    // Grappe : trois grains puis deux en quinconce, comme le logo.
    return (
      <span className="grid grid-cols-[6px_6px_6px] gap-0.5" aria-hidden="true">
        {[0, 1, 2, 3, 4].map((i) => (
          <span
            key={i}
            className={`w-1.5 h-1.5 rounded-full ${fill} ${i >= 3 ? 'ml-[3px]' : ''}`}
          />
        ))}
      </span>
    )
  }
  return (
    <span
      className={`w-4 h-[18px] rounded-[2px] border-[1.5px] flex flex-col justify-center gap-[3px] px-[3px] ${line}`}
      aria-hidden="true"
    >
      <span className={`h-[1.5px] ${bar}`} />
      <span className={`h-[1.5px] ${bar}`} />
    </span>
  )
}

function Shell() {
  const { username, logout } = useAuth()
  const { loading } = useData()
  const [view, setView] = useState<View>('accueil')
  const [seg, setSeg] = useState<Seg>('bouteille')
  const [compte, setCompte] = useState(false)

  const go = (v: View) => {
    setView(v)
    window.scrollTo({ top: 0 })
  }
  const goAdd = (s: Seg) => {
    setSeg(s)
    go('ajouter')
  }

  return (
    <>
      <header
        className="bar-top border-b border-line/50 sticky top-0 z-20 flex justify-between items-center px-[18px] pb-2.5"
        style={{ paddingTop: 'calc(10px + env(safe-area-inset-top))' }}
      >
        <div className="flex items-center gap-2.5">
          <Logo className="w-[26px] h-[26px]" />
          <span className="font-serif text-[1.4rem] text-ink-bright leading-none">CavaVin</span>
        </div>
        <button
          onClick={() => setCompte(true)}
          aria-label="Mon compte"
          className="w-[30px] h-[30px] rounded-full bg-gold grid place-items-center text-[13px] font-bold text-ink-dark"
        >
          {(username || '?').charAt(0).toUpperCase()}
        </button>
      </header>

      <main
        className="max-w-[640px] mx-auto px-4 pt-4"
        style={{ paddingBottom: 'calc(104px + env(safe-area-inset-bottom))' }}
      >
        {loading ? (
          <div className="text-muted text-center mt-16">Chargement de votre cave…</div>
        ) : (
          <>
            {view === 'accueil' && (
              <AccueilScreen onAjouter={() => goAdd('bouteille')} />
            )}
            {view === 'vin' && <MesVinsScreen onAjouter={() => goAdd('bouteille')} />}
            {view === 'cave' && <CaveScreen onAdd={goAdd} />}
            {view === 'ajouter' && (
              <>
                <button
                  onClick={() => go('accueil')}
                  className="flex items-center gap-2 text-muted text-sm mb-3"
                >
                  <svg width="9" height="15" viewBox="0 0 9 15" aria-hidden="true">
                    <path
                      d="M8 1L1 7.5l7 6.5"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      fill="none"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  Retour
                </button>
                <AjouterScreen seg={seg} setSeg={setSeg} onDone={() => go('cave')} />
              </>
            )}
            {view === 'carnet' && <CarnetScreen />}
          </>
        )}
      </main>

      <nav
        className="bar-bottom border-t border-line/50 fixed bottom-0 left-0 right-0 z-30 flex flex-col"
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
      >
        <div className="flex justify-around">
          {(
            [
              ['accueil', 'Accueil'],
              ['vin', 'Vin'],
              ['cave', 'Cave'],
              ['carnet', 'Carnet'],
            ] as [Tab, string][]
          ).map(([t, lbl]) => (
            <button
              key={t}
              onClick={() => go(t)}
              className={`flex-1 pt-2.5 pb-1 flex flex-col items-center gap-1 text-[0.66rem] transition ${
                view === t ? 'text-ink' : 'text-muted'
              }`}
            >
              <span className="h-[18px] grid place-items-center">
                <TabIcon tab={t} active={view === t} />
              </span>
              {lbl}
            </button>
          ))}
        </div>
        <span className="text-center text-[0.62rem] text-muted/60 pb-1.5 tabular-nums">
          CavaVin · v{APP_VERSION}
        </span>
      </nav>

      {/* ---------- Feuille compte ---------- */}
      <Sheet open={compte} onClose={() => setCompte(false)}>
        {compte && (
          <>
            <h3 className="font-serif text-[1.35rem] m-0">Mon compte</h3>
            <div className="text-muted text-sm mt-1">
              Connecté en tant que <span className="text-ink">{username}</span>
            </div>
            <div className="text-muted/60 text-xs mt-1 tabular-nums">CavaVin · v{APP_VERSION}</div>
            <button
              onClick={() => {
                setCompte(false)
                logout()
              }}
              className="w-full mt-5 py-3.5 rounded-xl border border-line text-muted active:scale-[0.985] transition"
            >
              Se déconnecter
            </button>
          </>
        )}
      </Sheet>
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
