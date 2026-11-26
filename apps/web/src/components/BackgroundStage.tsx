import { useEffect, useMemo, useState } from 'react'
import { api } from '@/api/client'
import { useAuthed } from '@/lib/session'
import { cn } from '@/lib/utils'

/**
 * Aerial 式背景：若干张全屏静图轮流漂移，约 24s 换一张、1.8s 交叉淡入。
 *
 * - 图片不打进仓库：服务端首次运行时把 4K 风景照拉到本地库（/api/wallpapers/index），
 *   这里只取「已经就绪」的那几张；一张都没有或接口失败就只留深色底，主体照常渲染。
 * - 排程写在 CSS 里：每层一条 72s 的 sweep（前 12s 淡入、第 22.2s 起淡出），
 *   延迟按 --slot × 24s 错开；slot 0 常驻当底，其余层「盖瓦」式接力。
 * - 背景在独立图层里淡入（图片 decode 完才现身），不占首屏、不挡内容；
 *   后续图层由浏览器按 CSS 背景惰性下载，这里只提前预解码下一张。
 * - hero / inner 两个档位共用同一个 DOM，只切换暗化与模糊——从首页进内页时
 *   画面是「玻璃慢慢变暗」，而不是换一张底图。
 */

interface Wallpaper {
  slug: string
  url: string
  /** 0 = 服务端还没下载好，不参与排程 */
  weight: number
}

const SLOT_MS = 24_000

/** 低端设备退化：只留第一张静态图（电视盒子 / 弱核显） */
function lowPerf(): boolean {
  try {
    const cores = navigator.hardwareConcurrency ?? 8
    const coarse = window.matchMedia('(pointer: coarse)').matches
    return cores <= 4 && coarse
  } catch {
    return false
  }
}

export function BackgroundStage({ variant }: { variant: 'hero' | 'inner' }) {
  const [cycle, setCycle] = useState(0)
  const [ready, setReady] = useState<Wallpaper[]>([])
  const [firstPainted, setFirstPainted] = useState(false)
  // 登录前不拉背景：未登录时接口一律 401，拉到只会打一堆无意义的失败请求
  const authed = useAuthed()
  const [perf] = useState<'full' | 'low'>(() => (lowPerf() ? 'low' : 'full'))

  useEffect(() => {
    const root = document.documentElement
    root.dataset.perf = perf === 'low' ? 'low' : ''
    return () => {
      delete root.dataset.perf
    }
  }, [perf])

  // 只问一次就绪清单：首屏不等它（内容先出来），背景晚到就淡进来；
  // 首次运行时服务端还在后台下载，隔几秒再看一眼，最多看几轮
  useEffect(() => {
    if (!authed) return
    let alive = true
    let tries = 0
    const load = () => {
      api
        .wallpapers()
        .then((list) => {
          if (!alive) return
          const readyNow = list.filter((item) => item.weight > 0)
          if (readyNow.length > 0) setReady(readyNow)
          else if (++tries <= 4) setTimeout(load, 5000)
        })
        .catch(() => {
          /* 背景只是装饰：接口失败就保持深色底 */
        })
    }
    load()
    return () => {
      alive = false
    }
  }, [authed])

  // 预热下一张（只解码一张，别和首屏抢带宽）
  useEffect(() => {
    const next = ready[1]
    if (!next) return
    const img = new Image()
    img.decoding = 'async'
    img.src = next.url
  }, [ready])

  useEffect(() => {
    if (ready.length === 0) return
    const id = setTimeout(() => setCycle((cur) => cur + 1), ready.length * SLOT_MS)
    return () => clearTimeout(id)
  }, [cycle, perf, ready.length])

  // slot 0 解出来之后再让整层淡入：之前是深色底，不会出现半张空背景
  useEffect(() => {
    if (ready.length === 0) return
    let alive = true
    const img = new Image()
    img.onload = () => alive && setFirstPainted(true)
    img.src = ready[0].url
    return () => {
      alive = false
    }
  }, [ready])

  const hero = variant === 'hero'

  const veil = useMemo(
    () =>
      hero
        ? 'bg-gradient-to-b from-black/35 via-[#020306]/35 to-[#020306]/95'
        : 'bg-gradient-to-b from-[#020306]/72 via-[#020306]/82 to-[#020306]/94',
    [hero],
  )

  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden bg-[var(--bg-base)]">
      <div
        key={cycle}
        className={cn(
          'absolute inset-0 transition-[filter,opacity] duration-700 ease-aerial',
          firstPainted ? 'opacity-100' : 'opacity-0',
          hero ? 'blur-0' : 'blur-[3px]',
          perf === 'low' && 'blur-none',
        )}
      >
        {ready.map((item, index) => (
          <div
            key={`${cycle}-${item.slug}`}
            className="ken-burns-layer"
            data-slot={index}
            style={{ ['--slot' as string]: index }}
            aria-hidden
          >
            <div className="bg-layer-inner" style={{ backgroundImage: `url(${item.url})` }} />
          </div>
        ))}
      </div>
      {!firstPainted ? <div className="absolute inset-0 bg-[var(--bg-base)]" /> : null}

      <div className={cn('absolute inset-0 transition-colors duration-500', veil)} />
      {/* 内页左缘本来就是压暗给文字用的，首页不需要，否则左侧像贴了一层黑纱 */}
      {hero ? null : (
        <div className="absolute inset-0 bg-gradient-to-r from-black/25 via-transparent to-transparent" />
      )}
      {perf === 'low' ? <div className="absolute inset-0 bg-[var(--bg-base)]/45" /> : null}
    </div>
  )
}
