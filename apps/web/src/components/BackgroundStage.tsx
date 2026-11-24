import { useEffect, useMemo, useState } from 'react'
import { cn } from '@/lib/utils'

/**
 * Aerial 式背景：6 张全屏静图，每张 48s 缓慢漂移，约 24s 一次 1.8s 交叉淡入。
 *
 * - 轮播排程全部写在 CSS 里（按层的 --slot 推导延迟），JS 只负责：
 *   ① 预加载后续图层；② 144s 一轮结束后换 key 重挂，让排程继续。
 * - hero / inner 两个档位共用同一个 DOM，只切换暗化与模糊——从首页进内页时
 *   画面是「玻璃慢慢变暗」，而不是换一张底图。
 */

export const WALLPAPERS = [
  { src: '/wallpapers/01-earth.jpg', credit: 'photo-1451187580459-43490279c0fa' },
  { src: '/wallpapers/02-nightlake.jpg', credit: 'photo-1519681393784-d120267933ba' },
  { src: '/wallpapers/03-sea-horizon.jpg', credit: 'photo-1534447677768-be436bb09401' },
  { src: '/wallpapers/04-forest-mist.jpg', credit: 'photo-1483347756197-71ef80e95f73' },
  { src: '/wallpapers/05-lake-dawn.jpg', credit: 'photo-1439066615861-d1af74d74000' },
  { src: '/wallpapers/06-valley.jpg', credit: 'photo-1464822759023-fed622ff2c3b' },
] as const

/** 一轮排程的时长：6 张 × 24s */
const CYCLE_MS = WALLPAPERS.length * 24_000

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
  const [perf] = useState<'full' | 'low'>(() => (lowPerf() ? 'low' : 'full'))

  useEffect(() => {
    const root = document.documentElement
    root.dataset.perf = perf === 'low' ? 'low' : ''
    return () => {
      delete root.dataset.perf
    }
  }, [perf])

  useEffect(() => {
    // 其余图层要等各自的动画被应用后才下载，这里提前把 6 张全部拉进缓存
    for (const item of WALLPAPERS.slice(1)) {
      const img = new Image()
      img.decoding = 'async'
      img.src = item.src
    }
  }, [])

  useEffect(() => {
    const id = setTimeout(() => setCycle((cur) => cur + 1), CYCLE_MS)
    return () => clearTimeout(id)
  }, [cycle, perf])

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
          'absolute inset-0 transition-[filter] duration-500 ease-aerial',
          hero ? 'blur-0' : 'blur-[3px]',
          perf === 'low' && 'blur-none',
        )}
      >
        {WALLPAPERS.map((item, index) => (
          <div
            key={`${cycle}-${item.src}`}
            className="ken-burns-layer"
            style={{ ['--slot' as string]: index }}
            aria-hidden
          >
            <div className="bg-layer-inner" style={{ backgroundImage: `url(${item.src})` }} />
          </div>
        ))}
      </div>

      <div className={cn('absolute inset-0 transition-colors duration-500', veil)} />
      <div className="absolute inset-0 bg-gradient-to-r from-black/35 via-transparent to-black/10" />
      {perf === 'low' ? <div className="absolute inset-0 bg-[var(--bg-base)]/45" /> : null}
    </div>
  )
}
