import { useRef, useState } from 'react'
import { Play } from 'lucide-react'
import { fmtBytes, fmtDuration } from '@/api/client'
import { Focusable } from './Focusable'

interface WideCardData {
  title: string
  coverUrl?: string
  quality?: string
  category?: string
  durationSec?: number
  sizeBytes?: number
}

/**
 * tvOS 首页横向卡片：16:9 封面 + 继续播放进度。
 * 获焦时会把自身滚到所在横向列表中间。
 */
export function WideCard({
  card,
  progressMs = 0,
  durationMs,
  onSelect,
  focusKey,
  className = 'w-[clamp(260px,30vw,440px)] shrink-0',
}: {
  card: WideCardData
  progressMs?: number
  durationMs?: number | null
  onSelect: () => void
  focusKey?: string
  className?: string
}) {
  const [failed, setFailed] = useState(false)
  const [focused, setFocused] = useState(false)
  const anchor = useRef<HTMLDivElement>(null)
  const cover = card.coverUrl
  const total = durationMs ? durationMs / 1000 : (card.durationSec ?? 0)
  const pct = progressMs && total ? Math.min(100, (progressMs / 1000 / total) * 100) : 0

  const handleFocus = () => {
    setFocused(true)
    anchor.current?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
  }

  return (
    <div ref={anchor} className={className}>
      <Focusable
        focusKey={focusKey}
        onEnter={onSelect}
        onFocus={handleFocus}
        onBlur={() => setFocused(false)}
      >
        <div className="group relative aspect-video overflow-hidden rounded-[22px] bg-white/[0.07] shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]">
          {cover && !failed ? (
            <img
              src={cover}
              alt=""
              loading="lazy"
              onError={() => setFailed(true)}
              className={[
                'h-full w-full object-cover transition-transform duration-700 ease-out',
                focused ? 'scale-[1.06]' : 'scale-100',
              ].join(' ')}
            />
          ) : (
            <div className="flex h-full items-center justify-center bg-gradient-to-br from-zinc-800 to-zinc-950 px-4 text-center text-sm text-zinc-400">
              {card.title}
            </div>
          )}

          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/85 via-black/20 to-transparent" />

          <span className="absolute right-3 top-3 rounded-md bg-black/60 px-2 py-0.5 text-[11px] font-medium text-white/90 backdrop-blur">
            {card.quality}
          </span>

          <div
            className={[
              'absolute inset-0 flex items-center justify-center transition-opacity duration-300',
              focused ? 'opacity-100' : 'opacity-0',
            ].join(' ')}
          >
            <span className="flex h-14 w-14 items-center justify-center rounded-full bg-white/95 text-black shadow-2xl">
              <Play className="h-6 w-6 translate-x-0.5" />
            </span>
          </div>

          <div className="absolute inset-x-0 bottom-0 p-4">
            <p className="line-clamp-2 text-[17px] font-semibold leading-snug text-white drop-shadow">
              {card.title}
            </p>
            <div className="mt-1.5 flex items-center gap-2 text-[11px] text-white/65">
              <span>{card.category ?? ''}</span>
              {card.durationSec ? <span>· {fmtDuration(card.durationSec)}</span> : null}
              {card.sizeBytes ? <span>· {fmtBytes(card.sizeBytes)}</span> : null}
            </div>
            {pct > 0 ? (
              <div className="mt-2.5 flex items-center gap-2">
                <div className="h-1 flex-1 overflow-hidden rounded-full bg-white/20">
                  <div className="h-full rounded-full bg-white" style={{ width: `${pct}%` }} />
                </div>
                <span className="text-[10px] tabular-nums text-white/70">
                  {fmtDuration(Math.round(progressMs / 1000))}
                </span>
              </div>
            ) : null}
          </div>
        </div>
      </Focusable>
    </div>
  )
}
