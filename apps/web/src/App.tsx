import { useCallback, useEffect, useRef, useState } from 'react'
import { destroy, init, setFocus } from '@noriginmedia/norigin-spatial-navigation'
import { api } from '@/api/client'
import { BackgroundStage } from '@/components/BackgroundStage'
import { FocusActiveContext } from '@/components/Focusable'
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
  /** 打开播放器的那张卡：退出后焦点回到它 */
  focusKey?: string
}

/** 转场舞台里的一层：正在进场的页或正在退场的上一页 */
interface StageEntry {
  key: string
  page: Page
  phase: 'enter' | 'exit'
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

/** 页面深度：用来判断「前进 / 后退」的方向感 */
function depth(p: Page): number {
  return p.name === 'home' ? 0 : 1
}

function pageKey(p: Page): string {
  return p.name === 'source' ? `source/${p.id}` : p.name
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
  const [stage, setStage] = useState<StageEntry[]>([
    { key: pageKey(parseHash()), page: parseHash(), phase: 'enter' },
  ])
  const [back, setBack] = useState(false)
  const [curtain, setCurtain] = useState(0)
  const [playing, setPlaying] = useState<PlayTarget | null>(null)
  const [ready, setReady] = useState(false)
  const navId = useRef(0)

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

  // 页面变化 → 旧层退场、新层进场；最多保留「退场 + 入场」两层，
  // 转场中再次导航时直接丢弃最旧的退场层。
  useEffect(() => {
    const key = pageKey(page)
    setStage((cur) => {
      const prev = cur[cur.length - 1]
      if (prev && prev.key === key) return cur
      const exiting: StageEntry[] = prev ? [{ ...prev, phase: 'exit' }] : []
      return [...exiting, { key, page, phase: 'enter' }]
    })
    setCurtain(++navId.current)
  }, [page])

  useEffect(() => {
    const want = toHash(page)
    if (window.location.hash !== want) window.history.pushState(null, '', want)
  }, [page])

  useEffect(() => {
    const onPop = () => {
      const next = parseHash()
      setBack(depth(next) < depth(page))
      setPage(next)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [page])

  const navigate = useCallback((next: Page) => {
    setBack(false)
    setPage(next)
  }, [])

  // 二级页面默认把焦点放在左上角返回键；首页由 HomePage 自己决定第一张卡。
  // 只等一帧（norigin 的 setFocus 内部会等布局），不用再叠 setTimeout。
  useEffect(() => {
    if (!ready || page.name === 'home') return
    const raf = requestAnimationFrame(() => {
      try {
        void setFocus('TOP-BACK').catch(() => {})
      } catch {
        /* key 未注册：保持现状 */
      }
    })
    return () => cancelAnimationFrame(raf)
  }, [page, ready])

  const retire = useCallback((key: string) => {
    setStage((cur) => cur.filter((entry) => !(entry.phase === 'exit' && entry.key === key)))
  }, [])

  const startPlay = useCallback((target: PlayTarget) => setPlaying(target), [])
  const stopPlay = useCallback(() => {
    const from = playing?.focusKey
    setPlaying(null)
    if (from) {
      requestAnimationFrame(() => {
        try {
          void setFocus(from).catch(() => {})
        } catch {
          /* 卡片已不在：交给 norigin 自己回焦 */
        }
      })
    }
  }, [playing])
  const goHome = useCallback(() => navigate({ name: 'home' }), [navigate])
  const openSource = useCallback((id: string) => navigate({ name: 'source', id }), [navigate])

  const renderPage = useCallback(
    (entry: StageEntry) => {
      const p = entry.page
      if (p.name === 'home') {
        return (
          <HomePage
            onPlay={startPlay}
            onOpenSource={openSource}
            onOpenClues={() => navigate({ name: 'tree' })}
          />
        )
      }
      if (p.name === 'source') return <SourcePage key={p.id} source={p.id} onPlay={startPlay} />
      if (p.name === 'tree') return <TreePage />
      return <SettingsPage />
    },
    [startPlay, openSource, navigate],
  )

  const top = stage[stage.length - 1]
  const current = top?.page ?? page

  return (
    <div className="relative min-h-screen pb-10">
      <BackgroundStage variant={current.name === 'home' ? 'hero' : 'inner'} />

      {!playing ? (
        <TopBar
          showBack={current.name !== 'home'}
          settingsActive={current.name === 'settings'}
          onBack={goHome}
          onSettings={() => navigate({ name: 'settings' })}
        />
      ) : null}

      {/* 退场层先渲染、入场层后渲染：同名 focusKey 由后注册的入场页覆盖。
          这个顺序不能调换，否则退场页会抢走入场的焦点注册。 */}
      <main className="relative z-10" aria-hidden={playing ? true : undefined}>
        {stage.map((entry) => (
          <FocusActiveContext.Provider
            key={`${entry.key}-${entry.phase}`}
            value={entry.phase === 'enter'}
          >
            <div
              className={
                entry.phase === 'exit'
                  ? 'animate-aerial-out [transform-origin:50%_40%]'
                  : back
                    ? 'animate-aerial-in-back [transform-origin:50%_40%]'
                    : 'animate-aerial-in [transform-origin:50%_40%]'
              }
              onAnimationEnd={(event) => {
                if (entry.phase === 'exit' && event.animationName === 'aerial-out') {
                  retire(entry.key)
                }
              }}
            >
              {renderPage(entry)}
            </div>
          </FocusActiveContext.Provider>
        ))}
      </main>

      {curtain > 0 ? (
        <div
          key={curtain}
          className="animate-curtain pointer-events-none fixed inset-0 z-[45] bg-black"
          aria-hidden
        />
      ) : null}

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
