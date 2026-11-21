import { useCallback, useEffect, useState } from 'react'
import { destroy, init, setFocus } from '@noriginmedia/norigin-spatial-navigation'
import { api } from '@/api/client'
import { Player } from '@/components/Player'
import { TopBar } from '@/components/TopBar'
import { HomePage } from '@/pages/HomePage'
import { SettingsPage } from '@/pages/SettingsPage'
import { SourcePage } from '@/pages/SourcePage'
import { TreePage } from '@/pages/TreePage'

type Page =
  | { name: 'home' }
  | { name: 'source'; id: string }
  | { name: 'tree' }
  | { name: 'settings' }

export interface PlayTarget {
  source: string
  id: string
  title: string
  url: string
  startAtMs: number
}

function parseHash(): Page {
  const h = window.location.hash.replace(/^#\/?/, '')
  if (h === 'library') return { name: 'source', id: 'local' }
  if (h.startsWith('source/')) return { name: 'source', id: decodeURIComponent(h.slice(7)) }
  if (h === 'tree') return { name: 'tree' }
  if (h === 'settings') return { name: 'settings' }
  return { name: 'home' }
}

function toHash(p: Page): string {
  if (p.name === 'home') return '#/'
  if (p.name === 'source') return `#/source/${encodeURIComponent(p.id)}`
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
    if (!ready || page.name === 'home') return
    // 二级页面默认把焦点放在左上角返回键；首页由 HomePage 自己决定第一张卡
    let inner: ReturnType<typeof setTimeout> | undefined
    const t = setTimeout(() => {
      inner = setTimeout(() => {
        try {
          void setFocus('TOP-BACK').catch(() => {})
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
  const goHome = useCallback(() => setPage({ name: 'home' }), [])

  const openSource = useCallback((id: string) => setPage({ name: 'source', id }), [])

  return (
    <div className="relative min-h-screen bg-[hsl(var(--background))] pb-10">
      {!playing ? (
        <TopBar
          showBack={page.name !== 'home'}
          settingsActive={page.name === 'settings'}
          onBack={goHome}
          onSettings={() => setPage({ name: 'settings' })}
        />
      ) : null}

      <main className="relative">
        {!playing && page.name === 'home' ? (
          <HomePage
            onPlay={startPlay}
            onOpenSource={openSource}
            onOpenClues={() => setPage({ name: 'tree' })}
          />
        ) : null}
        {!playing && page.name === 'source' ? (
          <SourcePage key={page.id} source={page.id} onPlay={startPlay} />
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
