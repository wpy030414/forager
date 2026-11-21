import type { LibraryItem } from '@libre-tv/shared/types'
import { useCallback, useEffect, useState } from 'react'
import { Play } from 'lucide-react'
import { api, fmtBytes, fmtDuration } from '@/api/client'
import type { Facets } from '@/api/client'
import { Focusable } from '@/components/Focusable'

/**
 * 库：只展示本地内容，按「来源」与「源站分区」筛选（分区不是 tag）。
 */
export function LibraryPage({
  onPlay,
  initialSource,
}: {
  onPlay: (t: { source: string; id: string; title: string; url: string; startAtMs: number }) => void
  initialSource?: string
}) {
  const [items, setItems] = useState<LibraryItem[]>([])
  const [facets, setFacets] = useState<Facets>()
  const [source, setSource] = useState(initialSource ?? '')
  const [category, setCategory] = useState('')
  const [error, setError] = useState<string>()
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [lib, fac] = await Promise.all([
        api.libraryItems({ source: source || undefined, category: category || undefined }),
        api.facets(),
      ])
      setItems(lib)
      setFacets(fac)
      setError(undefined)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [source, category])

  useEffect(() => {
    void load()
  }, [load])

  const categories = (facets?.categories ?? []).filter((c) => !source || c.source === source)

  const play = async (item: LibraryItem) => {
    const progress = await api.progress(item.source, item.id).catch(() => null)
    onPlay({
      source: item.source,
      id: item.id,
      title: item.meta.title,
      url: `/api/library/media/${item.fileName}`,
      startAtMs: progress?.positionMs ?? 0,
    })
  }

  return (
    <div className="space-y-8 px-12 pb-24 pt-28">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-2xl font-bold text-white">库</h1>
      </div>

      <div className="flex flex-wrap gap-2">
        <Chip
          active={!source}
          label={`全部 ${facets?.total ?? 0}`}
          onClick={() => {
            setSource('')
            setCategory('')
          }}
        />
        {(facets?.sources ?? []).map((s) => (
          <Chip
            key={s.source}
            active={source === s.source}
            label={`${s.source} ${s.count}`}
            onClick={() => {
              setSource(s.source)
              setCategory('')
            }}
          />
        ))}
      </div>

      {categories.length > 0 ? (
        <div className="flex flex-wrap gap-2 border-t border-white/5 pt-5">
          <Chip small active={!category} label="全部分区" onClick={() => setCategory('')} />
          {categories.map((c) => (
            <Chip
              key={`${c.source}:${c.category}`}
              small
              active={category === c.category}
              label={`${c.category} ${c.count}`}
              onClick={() => setCategory(c.category)}
            />
          ))}
        </div>
      ) : null}

      {error ? <p className="text-sm text-amber-300">{error}</p> : null}

      {loading && items.length === 0 ? (
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {Array.from({ length: 10 }).map((_, index) => (
            <div
              key={index}
              className="aspect-[3/4] animate-pulse rounded-2xl bg-white/[0.06]"
            />
          ))}
        </div>
      ) : items.length === 0 ? null : (
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {items.map((item) => (
            <LibraryCard key={item.key} item={item} onPlay={() => void play(item)} />
          ))}
        </div>
      )}
    </div>
  )
}

function LibraryCard({ item, onPlay }: { item: LibraryItem; onPlay: () => void }) {
  const [failed, setFailed] = useState(false)
  const cover = item.coverFile
    ? `/api/library/media/${item.coverFile}`
    : api.image(item.source, item.meta.coverUrl)

  return (
    <div className="group">
      <Focusable onEnter={onPlay} className="w-full">
        <div className="relative aspect-[3/4] overflow-hidden rounded-2xl bg-zinc-900">
          {cover && !failed ? (
            <img
              src={cover}
              alt=""
              loading="lazy"
              onError={() => setFailed(true)}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full items-center justify-center bg-gradient-to-br from-zinc-800 to-zinc-950 px-3 text-center text-xs text-zinc-500">
              {item.meta.title}
            </div>
          )}
          <div className="pointer-events-none absolute inset-0 bg-black/0 transition group-hover:bg-black/20" />
          <span className="absolute right-2 top-2 rounded-md bg-black/70 px-2 py-0.5 text-[11px] font-medium text-white/90 backdrop-blur">
            {item.quality}
          </span>
          {item.meta.durationSec ? (
            <span className="absolute bottom-2 right-2 rounded-md bg-black/70 px-2 py-0.5 text-[11px] tabular-nums text-white/90 backdrop-blur">
              {fmtDuration(item.meta.durationSec)}
            </span>
          ) : null}
          <div className="absolute inset-0 flex items-center justify-center opacity-0 transition group-hover:opacity-100">
            <span className="flex h-12 w-12 items-center justify-center rounded-full bg-white/90 text-black">
              <Play className="h-5 w-5" />
            </span>
          </div>
        </div>
      </Focusable>
      <div className="mt-2 space-y-1 px-1">
        <p className="line-clamp-2 text-sm font-medium leading-snug text-white">
          {item.meta.title}
        </p>
        <p className="text-[11px] text-zinc-400">
          {item.category ?? '未分区'} · {item.source} · {fmtBytes(item.sizeBytes)}
        </p>
      </div>
    </div>
  )
}

function Chip({
  label,
  active,
  onClick,
  small = false,
}: {
  label: string
  active: boolean
  onClick: () => void
  small?: boolean
}) {
  return (
    <Focusable scale="sm" onEnter={onClick}>
      <span
        className={[
          'block rounded-full px-4 font-medium transition-colors',
          small ? 'py-1 text-xs' : 'py-1.5 text-sm',
          active ? 'bg-white text-black' : 'bg-white/10 text-zinc-300 hover:bg-white/20',
        ].join(' ')}
      >
        {label}
      </span>
    </Focusable>
  )
}
