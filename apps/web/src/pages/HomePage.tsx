import type { ContinueItem, LibraryItem } from '@libre-tv/shared/types'
import { useEffect, useMemo, useState } from 'react'
import { setFocus } from '@noriginmedia/norigin-spatial-navigation'
import { ChevronRight, Compass } from 'lucide-react'
import { api } from '@/api/client'
import type { Facets, TreeGroup, TreeResponse } from '@/api/client'
import { Focusable } from '@/components/Focusable'
import { WideCard } from '@/components/WideCard'

export interface HomePlayTarget {
  source: string
  id: string
  title: string
  url: string
  startAtMs: number
}

const PLATFORM_ORDER = ['bilibili', 'hanime1', 'pixiv', 'cctv']

const PLATFORM_STYLE: Record<
  string,
  { label: string; mark: string; gradient: string; soon?: boolean }
> = {
  bilibili: {
    label: '哔哩哔哩',
    mark: 'bilibili',
    gradient: 'from-[#FB7299] via-[#e0628f] to-[#00AEEC]',
  },
  hanime1: {
    label: 'Hanime1',
    mark: 'H1',
    gradient: 'from-[#2a0b0b] via-[#7f1d1d] to-[#ef4444]',
  },
  pixiv: {
    label: 'Pixiv',
    mark: 'P',
    gradient: 'from-[#0096FA] via-[#0073c2] to-[#052f52]',
    soon: true,
  },
  cctv: {
    label: 'CCTV',
    mark: 'CCTV',
    gradient: 'from-[#7f1d1d] via-[#b91c1c] to-[#f59e0b]',
    soon: true,
  },
}

/**
 * 首页：tvOS 流式墙。
 * 继续播放 / 为你推荐横向货架 → 平台入口 → 线索速览。
 * 获焦卡片会把封面推成背景，横向列表自动居中滚动。
 */
export function HomePage({
  onPlay,
  onOpenSource,
  onOpenLibrary,
  onOpenClues,
}: {
  onPlay: (target: HomePlayTarget) => void
  onOpenSource: (source: string) => void
  onOpenLibrary: () => void
  onOpenClues: () => void
}) {
  const [cont, setCont] = useState<ContinueItem[]>([])
  const [recent, setRecent] = useState<LibraryItem[]>([])
  const [sources, setSources] = useState<Array<{ id: string; name: string }>>([])
  const [facets, setFacets] = useState<Facets>()
  const [tree, setTree] = useState<TreeResponse>()
  const [error, setError] = useState<string>()
  const [notice, setNotice] = useState<string>()
  const [backdrop, setBackdrop] = useState<string>()

  useEffect(() => {
    let alive = true
    Promise.all([
      api.continueWatching(10),
      api.recent(12),
      api.listSources(),
      api.facets(),
      api.tree(),
    ])
      .then(([continues, latest, sourceList, facetData, treeData]) => {
        if (!alive) return
        setCont(continues)
        setRecent(latest)
        setSources(sourceList)
        setFacets(facetData)
        setTree(treeData)
        setBackdrop(undefined)
        setError(undefined)
      })
      .catch((e) => {
        if (alive) setError((e as Error).message)
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (!notice) return
    const id = setTimeout(() => setNotice(undefined), 3200)
    return () => clearTimeout(id)
  }, [notice])

  const platforms = useMemo(() => {
    const active = sources.map((source) => {
      const style = PLATFORM_STYLE[source.id] ?? {
        label: source.name,
        mark: source.name.slice(0, 2).toUpperCase(),
        gradient: 'from-zinc-700 via-zinc-800 to-zinc-950',
      }
      return {
        id: source.id,
        label: style.label,
        mark: style.mark,
        gradient: style.gradient,
        soon: false,
        count: facets?.sources.find((item) => item.source === source.id)?.count ?? 0,
      }
    })
    const activeIds = new Set(active.map((item) => item.id))
    const upcoming = Object.entries(PLATFORM_STYLE)
      .filter(([id, style]) => style.soon && !activeIds.has(id))
      .map(([id, style]) => ({
        id,
        label: style.label,
        mark: style.mark,
        gradient: style.gradient,
        soon: true,
        count: 0,
      }))
    const rank = (id: string) => {
      const index = PLATFORM_ORDER.indexOf(id)
      return index < 0 ? PLATFORM_ORDER.length : index
    }
    return [...active, ...upcoming].sort((left, right) => rank(left.id) - rank(right.id))
  }, [sources, facets])

  const playLocal = (item: LibraryItem, positionMs = 0) =>
    onPlay({
      source: item.source,
      id: item.id,
      title: item.meta.title,
      url: `/api/library/media/${item.fileName}`,
      startAtMs: positionMs,
    })

  const playRecent = async (item: LibraryItem) => {
    const progress = await api.progress(item.source, item.id).catch(() => null)
    playLocal(item, progress?.positionMs ?? 0)
  }

  useEffect(() => {
    const target = cont.length
      ? 'HOME-CONTINUE-0'
      : recent.length
        ? 'HOME-RECENT-0'
        : platforms[0]
          ? `PLATFORM-${platforms[0].id}`
          : 'TAB-home'
    const id = setTimeout(() => {
      void setFocus(target).catch(() => {})
    }, 140)
    return () => clearTimeout(id)
  }, [cont.length, recent.length, platforms])

  const clueGroups = (tree?.sources ?? []).filter((group) => group.nodes.length > 0)

  return (
    <div className="relative min-h-screen">
      <Backdrop url={backdrop} />

      <div className="relative z-10 space-y-12 px-12 pb-28 pt-28">
        {error ? <p className="text-sm text-amber-300">{error}</p> : null}

        <div className="grid gap-10 xl:grid-cols-2">
          <Shelf
            id="continue"
            title="继续播放"
            caption="从上次停下的地方接着看"
            empty="还没有播放记录"
            items={cont.map((item) => ({
              item,
              progressMs: item.progress.positionMs,
              durationMs: item.progress.durationMs,
              onSelect: () => playLocal(item, item.progress.positionMs),
            }))}
            onFocusCover={setBackdrop}
            onEmpty={onOpenLibrary}
            emptyAction="去库里挑一部"
          />

          <Shelf
            id="recent"
            title="为你推荐"
            caption="最近入库"
            empty="库里还没有新内容"
            items={recent.map((item) => ({
              item,
              onSelect: () => void playRecent(item),
            }))}
            onFocusCover={setBackdrop}
            onEmpty={onOpenClues}
            emptyAction="去线索里发现"
          />
        </div>

        <section className="animate-fade-up [animation-delay:120ms]">
          <SectionTitle
            title="平台"
            caption="按来源进入内容库"
            action={{ label: '全部内容', onClick: onOpenLibrary }}
          />
          <div className="shelf-scroll flex gap-4 overflow-x-auto px-1 pb-2 pt-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {platforms.map((platform) => (
              <PlatformCard
                key={platform.id}
                platform={platform}
                onFocus={() => setBackdrop(undefined)}
                onSelect={() => {
                  if (platform.soon) {
                    setNotice(`${platform.label} 源还未接入`)
                    return
                  }
                  onOpenSource(platform.id)
                }}
              />
            ))}
          </div>
        </section>

        {clueGroups.length > 0 ? (
          <section className="animate-fade-up [animation-delay:220ms]">
            <SectionTitle
              title="线索"
              caption="持续发现规则"
              action={{ label: '全部线索', onClick: onOpenClues }}
            />
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {clueGroups.map((group) => (
                <ClueSummary
                  key={group.source}
                  group={group}
                  onFocus={() => setBackdrop(undefined)}
                  onSelect={onOpenClues}
                />
              ))}
            </div>
          </section>
        ) : null}
      </div>

      {notice ? (
        <div className="fixed bottom-10 left-1/2 z-50 -translate-x-1/2 rounded-full bg-white px-5 py-2 text-sm font-medium text-black shadow-2xl">
          {notice}
        </div>
      ) : null}
    </div>
  )
}

interface ShelfEntry {
  item: LibraryItem
  progressMs?: number
  durationMs?: number | null
  onSelect: () => void
}

function Shelf({
  id,
  title,
  caption,
  empty,
  emptyAction,
  items,
  onFocusCover,
  onEmpty,
}: {
  id: string
  title: string
  caption: string
  empty: string
  emptyAction: string
  items: ShelfEntry[]
  onFocusCover: (cover?: string) => void
  onEmpty: () => void
}) {
  return (
    <section className="min-w-0 animate-fade-up">
      <SectionTitle title={title} caption={caption} />
      {items.length === 0 ? (
        <Focusable focusKey={`HOME-EMPTY-${id}`} onEnter={onEmpty} scale="sm">
          <div className="flex h-52 w-full flex-col items-center justify-center gap-3 rounded-[22px] border border-dashed border-white/15 bg-white/[0.03]">
            <p className="text-sm text-zinc-400">{empty}</p>
            <span className="rounded-full bg-white/10 px-4 py-1.5 text-xs text-white">
              {emptyAction}
            </span>
          </div>
        </Focusable>
      ) : (
        <div className="shelf-scroll flex gap-5 overflow-x-auto pb-4 pt-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {items.map((entry, index) => (
            <WideCard
              key={entry.item.key}
              item={entry.item}
              progressMs={entry.progressMs}
              durationMs={entry.durationMs}
              focusKey={`HOME-${id.toUpperCase()}-${index}`}
              onSelect={entry.onSelect}
              onFocus={onFocusCover}
            />
          ))}
        </div>
      )}
    </section>
  )
}

function SectionTitle({
  title,
  caption,
  action,
}: {
  title: string
  caption?: string
  action?: { label: string; onClick: () => void }
}) {
  return (
    <div className="mb-2 flex items-end justify-between gap-4 px-1">
      <div>
        <h2 className="text-xl font-semibold tracking-tight text-white/95">{title}</h2>
        {caption ? <p className="mt-0.5 text-[11px] text-white/45">{caption}</p> : null}
      </div>
      {action ? (
        <Focusable scale="sm" onEnter={action.onClick}>
          <span className="flex items-center gap-1 rounded-full bg-white/5 px-3 py-1.5 text-[11px] text-white/65">
            {action.label}
            <ChevronRight className="h-3 w-3" />
          </span>
        </Focusable>
      ) : null}
    </div>
  )
}

function PlatformCard({
  platform,
  onFocus,
  onSelect,
}: {
  platform: { id: string; label: string; mark: string; gradient: string; soon: boolean; count: number }
  onFocus: () => void
  onSelect: () => void
}) {
  return (
    <Focusable
      focusKey={`PLATFORM-${platform.id}`}
      onFocus={onFocus}
      onEnter={onSelect}
      className="shrink-0"
    >
      <div
        className={[
          'relative flex h-32 w-56 flex-col justify-between overflow-hidden rounded-[26px] bg-gradient-to-br p-5 ring-1 ring-white/15',
          platform.gradient,
          platform.soon ? 'opacity-55' : '',
        ].join(' ')}
      >
        <div className="flex items-start justify-between">
          <span className="text-[22px] font-black tracking-tight text-white drop-shadow">
            {platform.mark}
          </span>
          {platform.soon ? (
            <span className="rounded-full bg-black/35 px-2 py-0.5 text-[10px] text-white/80">
              待接入
            </span>
          ) : null}
        </div>
        <div className="flex items-end justify-between">
          <span className="text-sm font-semibold text-white/95">{platform.label}</span>
          {!platform.soon ? (
            <span className="text-[11px] text-white/65">{platform.count} 条</span>
          ) : null}
        </div>
        <div className="pointer-events-none absolute -right-8 -top-10 h-28 w-28 rounded-full bg-white/20 blur-2xl" />
      </div>
    </Focusable>
  )
}

function ClueSummary({
  group,
  onFocus,
  onSelect,
}: {
  group: TreeGroup
  onFocus: () => void
  onSelect: () => void
}) {
  const total = group.total.discovered
  const done = group.total.done
  const pct = total ? Math.round((done / total) * 100) : 0

  return (
    <Focusable focusKey={`HOME-CLUE-${group.source}`} onFocus={onFocus} onEnter={onSelect}>
      <div className="flex items-center justify-between gap-4 rounded-2xl bg-white/[0.05] p-4 ring-1 ring-white/10">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/10 text-white">
            <Compass className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-white">{group.name}</p>
            <p className="mt-0.5 text-[11px] text-white/45">
              {group.nodes.length} 条线索 · {done}/{total}
            </p>
          </div>
        </div>
        <div className="w-24 shrink-0">
          <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
            <div
              className="h-full rounded-full bg-emerald-400 transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      </div>
    </Focusable>
  )
}

function Backdrop({ url }: { url?: string }) {
  const [current, setCurrent] = useState<string>()
  const [previous, setPrevious] = useState<string>()

  useEffect(() => {
    if (!url || url === current) return
    setPrevious(current)
    setCurrent(url)
  }, [url, current])

  return (
    <div className="pointer-events-none fixed inset-0 overflow-hidden">
      <div className="absolute inset-0 bg-[#05060a]" />
      <div className="home-ambient absolute inset-0" />
      {previous ? (
        <div
          className="absolute inset-0 scale-110 bg-cover bg-center opacity-25 blur-3xl"
          style={{ backgroundImage: `url(${previous})` }}
        />
      ) : null}
      {current ? (
        <div
          key={current}
          className="absolute inset-0 scale-110 animate-fade-in bg-cover bg-center opacity-55 blur-3xl"
          style={{ backgroundImage: `url(${current})` }}
        />
      ) : null}
      <div className="absolute inset-0 bg-gradient-to-b from-black/25 via-[#05060a]/65 to-[#05060a]" />
    </div>
  )
}
