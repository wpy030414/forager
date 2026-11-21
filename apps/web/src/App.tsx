import { useCallback, useEffect, useState } from 'react'
import { destroy, init, setFocus } from '@noriginmedia/norigin-spatial-navigation'
import { api } from '@/api/client'
import { Player } from '@/components/Player'
import { TopBar } from '@/components/TopBar'
import { HomePage } from '@/pages/HomePage'
import { LibraryPage } from '@/pages/LibraryPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { TreePage } from '@/pages/TreePage'

type Page =
  | { name: 'home' }
  | { name: 'library'; source?: string }
  | { name: 'tree' }
  | { name: 'settings' }

export interface PlayTarget {
  source: string
  id: string
  title: string
  url: string
  startAtMs: number
}

const TABS = [
  { key: 'home', label: '首页' },
  { key: 'library', label: '库' },
  { key: 'tree', label: '线索' },
]

function parseHash(): Page {
  const h = window.location.hash.replace(/^#\/?/, '')
  if (h === 'library') return { name: 'library' }
  if (h === 'tree') return { name: 'tree' }
  if (h === 'settings') return { name: 'settings' }
  return { name: 'home' }
}

function toHash(p: Page): string {
  if (p.name === 'home') return '#/'
  return `#/${p.name}`
}

// 必须在任何 useFocusable 注册之前完成初始化
init({
  debug: false,
  visualDebug: false,
  shouldFocusDOMNode: true,
  shouldUseNativeEvents: false,
})

export function App() {
  const [page, setPage] = useState<Page>(parseHash)
  const [playing, setPlaying] = useState<PlayTarget | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    init({
      debug: false,
      visualDebug: false,
      shouldFocusDOMNode: true,
      shouldUseNativeEvents: false,
    })
    setReady(true)
    return () => {
      setReady(false)
      destroy()
    }
  }, [])

  useEffect(() => {
    const want = toHash(page)
    if (window.location.hash !== want) window.history.pushState(null, '', want)
  }, [page])

  useEffect(() => {
    const onPop = () => setPage(parseHash())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    if (!ready) return
    const key = page.name === 'settings' ? 'TOP-SETTINGS' : `TAB-${page.name}`
    // 等新页面挂载并完成焦点注册（两帧）后再设焦点
    let inner: ReturnType<typeof setTimeout> | undefined
    const t = setTimeout(() => {
      inner = setTimeout(() => {
        try {
          void setFocus(key).catch(() => {})
        } catch {
          /* key 未注册：保持现状 */
        }
      }, 80)
    }, 32)
    return () => {
      clearTimeout(t)
      if (inner) clearTimeout(inner)
    }
  }, [page, ready])

  const startPlay = useCallback((target: PlayTarget) => setPlaying(target), [])
  const stopPlay = useCallback(() => setPlaying(null), [])

  const openSource = (source: string) => setPage({ name: 'library', source })

  return (
    <div className="relative min-h-screen bg-[hsl(var(--background))] pb-10">
      {!playing ? (
        <TopBar
          tabs={TABS}
          active={page.name}
          settingsActive={page.name === 'settings'}
          onSelect={(key) => {
            if (key === 'home') setPage({ name: 'home' })
            else if (key === 'library') setPage({ name: 'library' })
            else if (key === 'tree') setPage({ name: 'tree' })
          }}
          onSettings={() => setPage({ name: 'settings' })}
        />
      ) : null}

      <main className="relative">
        {!playing && page.name === 'home' ? (
          <HomePage
            onPlay={startPlay}
            onOpenSource={openSource}
            onOpenLibrary={() => setPage({ name: 'library' })}
            onOpenClues={() => setPage({ name: 'tree' })}
          />
        ) : null}
        {!playing && page.name === 'library' ? (
          <LibraryPage
            key={page.source ?? 'all'}
            initialSource={page.source}
            onPlay={startPlay}
          />
        ) : null}
        {!playing && page.name === 'tree' ? <TreePage /> : null}
        {!playing && page.name === 'settings' ? <SettingsPage /> : null}
      </main>

      {playing ? (
        <Player
          src={playing.url}
          title={playing.title}
          startAtMs={playing.startAtMs}
          onExit={stopPlay}
          onProgress={(pos, dur) => {
            void api.saveProgress(playing.source, playing.id, pos, dur).catch(() => {})
          }}
        />
      ) : null}
    </div>
  )
}
