import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, UNAUTHORIZED_EVENT } from '@/api/client'
import { BackgroundStage } from '@/components/BackgroundStage'
import { Player } from '@/components/Player'
import { setAuthed as setSessionAuthed } from '@/lib/session'
import {
  enterPage,
  focus,
  FocusActiveContext,
  forgetPage,
  observeFocus,
  pageOfFocus,
  reclaimFocus,
  rememberPage,
  resetFocusMemory,
  startFocusManager,
} from '@/lib/focus'
import { TopBar } from '@/components/TopBar'
import { HomePage } from '@/pages/HomePage'
import { LoginPage } from '@/pages/LoginPage'
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

const NAV_KEYS = new Set([
  'Enter',
  ' ',
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  'Escape',
])

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

export function App() {
  const [page, setPage] = useState<Page>(parseHash)
  const [stage, setStage] = useState<StageEntry[]>([
    { key: pageKey(parseHash()), page: parseHash(), phase: 'enter' },
  ])
  const [back, setBack] = useState(false)
  const [curtain, setCurtain] = useState(0)
  const [playing, setPlaying] = useState<PlayTarget | null>(null)
  const [ready, setReady] = useState(false)
  // 登录门：null = 还没问过服务端，先不渲染任何页面（避免闪一下再跳回登录）
  const [authed, setAuthed] = useState<boolean | null>(null)
  const navId = useRef(0)

  useEffect(() => {
    let alive = true
    api
      .sessionStatus()
      .then((state) => {
        if (!alive) return
        setAuthed(state.authenticated)
        setSessionAuthed(state.authenticated)
      })
      .catch(() => alive && setAuthed(false))
    return () => {
      alive = false
    }
  }, [])

  // 任何请求吃到 401（会话被删、Cookie 被清）都立刻退回登录页
  useEffect(() => {
    const drop = () => setAuthed(false)
    window.addEventListener(UNAUTHORIZED_EVENT, drop)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, drop)
  }, [])

  // 会话重新建立时清掉上一次的焦点记忆（key 都对不上了，接上去只会落在别处）
  useLayoutEffect(() => {
    if (authed) resetFocusMemory()
  }, [authed])

  // 子组件的 useFocusable 注册比父组件的 effect 早，所以初始化要放在子组件出现之前：
  // 挂在登录成功的那一刻，而不是整棵树挂载之后。
  useLayoutEffect(() => {
    const stop = startFocusManager()
    setReady(true)
    return () => {
      setReady(false)
      stop()
    }
  }, [authed])

  // 页面记忆：焦点落到某页的卡上就记一下，前进/后退回来直接还原
  useEffect(() => {
    const current = pageKey(page)
    observeFocus((key) => {
      rememberPage(current, key)
      const owner = pageOfFocus(key)
      if (owner) rememberPage(owner, key)
    })
    return () => observeFocus(null)
  }, [page])

  // 浏览器前进 / 后退与转场把焦点留在 body 上时，方向键一按就全丢了：
  // 主键 / 方向键先让 Focus Manager 把焦点收回来
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Tab' || !NAV_KEYS.has(event.key)) return
      if (document.activeElement === document.body) reclaimFocus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
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

  // 页面切换：焦点交给 Focus Manager（记忆 → 落脚点 → 页内第一张 → 返回键），
  // 新页的落脚点通常要等数据回来，enterPage 自己会等；切走后旧的那次等待作废。
  useEffect(() => {
    if (!ready) return
    let cancelled = false
    void enterPage(pageKey(page), () => cancelled)
    return () => {
      cancelled = true
    }
  }, [page, ready])

  const retire = useCallback((key: string) => {
    // 退场页不留在舞台里，它那份焦点记忆已由转场前的 rememberPage 记下
    forgetPage(key)
    setStage((cur) => cur.filter((entry) => !(entry.phase === 'exit' && entry.key === key)))
  }, [])

  const startPlay = useCallback((target: PlayTarget) => setPlaying(target), [])
  const stopPlay = useCallback(() => {
    const from = playing?.focusKey
    setPlaying(null)
    // 卡片若已不在（列表刷新过），Focus Manager 会退到同位置 / 页面落脚点
    if (from) requestAnimationFrame(() => void focus(from))
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

  if (authed === null) {
    return <div className="min-h-screen bg-[var(--bg-base)]" aria-hidden />
  }

  if (!authed) {
    return (
      <div className="relative min-h-screen">
        <BackgroundStage variant="hero" />
        <LoginPage
          onSuccess={() => {
            setAuthed(true)
            setSessionAuthed(true)
          }}
        />
      </div>
    )
  }

  return (
    <div className="relative min-h-screen pb-10">
      <BackgroundStage variant={current.name === 'home' ? 'hero' : 'inner'} />

      {!playing ? (
        <TopBar
          showBack={current.name !== 'home' && current.name !== 'settings'}
          settingsActive={current.name === 'settings'}
          onBack={goHome}
          onSettings={() => navigate({ name: 'settings' })}
          onLogout={() => {
            void api.logout().catch(() => {})
            setAuthed(false)
            setSessionAuthed(false)
          }}
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
          onAnimationEnd={(event) => {
            if (event.animationName === 'curtain') setCurtain(0)
          }}
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
