import type { VideoSummary } from '@libre-tv/shared/types'
import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Search } from 'lucide-react'
import { api } from '@/api/client'
import { Focusable } from '@/components/Focusable'
import { PosterCard } from '@/components/PosterCard'

/**
 * 源浏览页：本地与在线源共用同一套浏览与播放路径。
 * 内容一律来自 /api/sources/<id> 的 list / search / categories，播放走 play 解析。
 */
export function SourcePage({
  source,
  onPlay,
}: {
  source: string
  onPlay: (target: {
    source: string
    id: string
    title: string
    url: string
    startAtMs: number
  }) => void
}) {
  const [name, setName] = useState(source)
  const [categories, setCategories] = useState<Array<{ id: string; name: string }>>([])
  const [category, setCategory] = useState('')
  const [keyword, setKeyword] = useState('')
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<VideoSummary[]>([])
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const [libraryKeys, setLibraryKeys] = useState<Set<string>>(new Set())

  useEffect(() => {
    let alive = true
    setCategory('')
    setKeyword('')
    setQuery('')
    setItems([])
    setPage(1)
    setHasMore(false)
    Promise.all([api.listSources(), api.sourceCategories(source)])
      .then(([sources, cats]) => {
        if (!alive) return
        setName(sources.find((item) => item.id === source)?.name ?? source)
        setCategories(cats)
      })
      .catch((e) => {
        if (alive) setError((e as Error).message)
      })
    return () => {
      alive = false
    }
  }, [source])

  useEffect(() => {
    api
      .libraryKeys()
      .then((keys) => setLibraryKeys(new Set(keys)))
      .catch(() => {})
  }, [items.length])

  const load = useCallback(
    async (nextPage: number) => {
      setBusy(true)
      setError(undefined)
      try {
        const res = query
          ? await api.search(source, query, nextPage)
          : await api.list(source, nextPage, category || undefined)
        setItems((prev) => (nextPage === 1 ? res.items : [...prev, ...res.items]))
        setPage(res.page)
        setHasMore(res.hasMore)
      } catch (e) {
        setError((e as Error).message)
      } finally {
        setBusy(false)
      }
    },
    [source, query, category],
  )

  useEffect(() => {
    void load(1)
  }, [load])

  const applyCategory = (id: string) => {
    setCategory(id)
    setQuery('')
    setKeyword('')
  }

  const submitSearch = () => {
    setCategory('')
    setQuery(keyword.trim())
  }

  const open = async (video: VideoSummary) => {
    try {
      const [resolve, progress] = await Promise.all([
        api.play(source, video.id),
        api.progress(source, video.id).catch(() => null),
      ])
      onPlay({
        source,
        id: video.id,
        title: video.title,
        url: resolve.url,
        startAtMs: progress?.positionMs ?? 0,
      })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="space-y-8 px-12 pb-24 pt-28">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-white">{name}</h1>
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            submitSearch()
          }}
        >
          <input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            className="w-56 rounded-full bg-black/40 px-4 py-2 text-sm text-white outline-none ring-1 ring-white/10 transition focus:ring-white/40"
          />
          <Focusable scale="sm" onEnter={submitSearch}>
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10 text-white">
              <Search className="h-4 w-4" />
            </span>
          </Focusable>
        </form>
      </div>

      {categories.length > 0 ? (
        <div className="shelf-scroll flex gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          <Chip active={!category && !query} label="全部" onClick={() => applyCategory('')} />
          {categories.map((item) => (
            <Chip
              key={item.id}
              active={category === item.id}
              label={item.name}
              onClick={() => applyCategory(item.id)}
            />
          ))}
        </div>
      ) : null}

      {error ? <p className="text-sm text-amber-300">{error}</p> : null}

      {busy && items.length === 0 ? (
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {Array.from({ length: 10 }).map((_, index) => (
            <div key={index} className="aspect-[3/4] animate-pulse rounded-2xl bg-white/[0.06]" />
          ))}
        </div>
      ) : items.length === 0 ? null : (
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {items.map((video, index) => (
            <PosterCard
              key={`${video.source}:${video.id}`}
              fluid
              video={video}
              inLibrary={source !== 'local' && libraryKeys.has(`${source}:${video.id}`)}
              focusKey={`SRC-${source}-${index}`}
              onSelect={() => void open(video)}
            />
          ))}
        </div>
      )}

      {hasMore ? (
        <div className="flex justify-center pt-2">
          <Focusable scale="sm" onEnter={busy ? undefined : () => void load(page + 1)}>
            <span className="flex items-center gap-1.5 rounded-full bg-white/10 px-5 py-2 text-xs font-medium text-white">
              <RefreshCw className={`h-3 w-3 ${busy ? 'animate-spin' : ''}`} />
              加载更多
            </span>
          </Focusable>
        </div>
      ) : null}
    </div>
  )
}

function Chip({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <Focusable scale="sm" onEnter={onClick}>
      <span
        className={[
          'block whitespace-nowrap rounded-full px-4 py-1.5 text-sm font-medium transition-colors',
          active ? 'bg-white text-black' : 'bg-white/10 text-zinc-300',
        ].join(' ')}
      >
        {label}
      </span>
    </Focusable>
  )
}
