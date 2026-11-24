import type { ContinueItem, VideoSummary } from '@libre-tv/shared/types'
import { useEffect, useMemo, useState } from 'react'
import { setFocus } from '@noriginmedia/norigin-spatial-navigation'
import { ChevronRight, Compass, Plus } from 'lucide-react'
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

const PLATFORM_ORDER = ['local', 'bilibili', 'hanime1', 'pixiv', 'cctv']

const PLATFORM_STYLE: Record<
  string,
  { label: string; mark: string; gradient: string; soon?: boolean }
> = {
  local: {
    label: '本地',
    mark: '本',
    gradient: 'from-[#52525b] via-[#27272a] to-[#09090b]',
  },
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
 * 首页：tvOS 无 chrome 流式墙。
 * 4K 风景照打满全屏；除「继续播放 / 为你推荐」外，其余区块只有内容，没有标题与更多入口。
 */
export function HomePage({
  onPlay,
  onOpenSource,
  onOpenClues,
}: {
  onPlay: (target: HomePlayTarget) => void
  onOpenSource: (source: string) => void
  onOpenClues: () => void
}) {
  const [cont, setCont] = useState<ContinueItem[]>([])
  const [recent, setRecent] = useState<VideoSummary[]>([])
  const [sources, setSources] = useState<Array<{ id: string; name: string }>>([])
  const [facets, setFacets] = useState<Facets>()
  const [tree, setTree] = useState<TreeResponse>()
  const [error, setError] = useState<string>()

  useEffect(() => {
    let alive = true
    Promise.all([
      api.continueWatching(10),
      api.list('local', 1),
      api.listSources(),
      api.facets(),
      api.tree(),
    ])
      .then(([continues, latest, sourceList, facetData, treeData]) => {
        if (!alive) return
        setCont(continues)
        setRecent(latest.items.slice(0, 12))
        setSources(sourceList)
        setFacets(facetData)
        setTree(treeData)
        setError(undefined)
      })
      .catch((e) => {
        if (alive) setError((e as Error).message)
      })
    return () => {
      alive = false
    }
  }, [])

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
        count:
          source.id === 'local'
            ? (facets?.total ?? 0)
            : (facets?.sources.find((item) => item.source === source.id)?.count ?? 0),
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

  const playLocal = async (item: ContinueItem) => {
    try {
      const [resolve, progress] = await Promise.all([
        api.play('local', item.key),
        api.progress('local', item.key).catch(() => null),
      ])
      onPlay({
        source: 'local',
        id: item.key,
        title: item.meta.title,
        url: resolve.url,
        startAtMs: progress?.positionMs ?? item.progress.positionMs,
      })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const playRecent = async (item: VideoSummary) => {
    try {
      const [resolve, progress] = await Promise.all([
        api.play('local', item.id),
        api.progress('local', item.id).catch(() => null),
      ])
      onPlay({
        source: 'local',
        id: item.id,
        title: item.title,
        url: resolve.url,
        startAtMs: progress?.positionMs ?? 0,
      })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => {
    const target = cont.length
      ? 'HOME-CONTINUE-0'
      : recent.length
        ? 'HOME-RECENT-0'
        : platforms[0]
          ? `PLATFORM-${platforms[0].id}`
          : 'TOP-SETTINGS'
    const id = setTimeout(() => {
      void setFocus(target).catch(() => {})
    }, 140)
    return () => clearTimeout(id)
  }, [cont.length, recent.length, platforms])

  const clueGroups = (tree?.sources ?? []).filter((group) => group.nodes.length > 0)

  return (
    <div className="relative min-h-screen">
      <div className="space-y-10 px-12 pb-28 pt-24">
        {error ? <p className="text-sm text-amber-300">{error}</p> : null}

        <div className="grid gap-10 xl:grid-cols-2">
          <Shelf
            id="continue"
            title="继续播放"
            action={{ label: '查看全部', onClick: () => onOpenSource('local') }}
            items={cont.map((item) => ({
              item,
              progressMs: item.progress.positionMs,
              durationMs: item.progress.durationMs,
              onSelect: () => void playLocal(item),
            }))}
          />

          <Shelf
            id="recent"
            title="为你推荐"
            action={{ label: '查看全部', onClick: () => onOpenSource('local') }}
            items={recent.map((item) => ({
              summary: item,
              onSelect: () => void playRecent(item),
            }))}
          />
        </div>

        <section className="animate-fade-up [animation-delay:80ms]">
          <div className="shelf-scroll flex gap-4 overflow-x-auto rounded-[30px] border border-white/10 bg-white/[0.045] p-4 backdrop-blur-md [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {platforms.map((platform) => (
              <PlatformCard
                key={platform.id}
                platform={platform}
                onSelect={() => {
                  if (!platform.soon) onOpenSource(platform.id)
                }}
              />
            ))}
          </div>
        </section>

        <section className="animate-fade-up [animation-delay:160ms]">
          <div className="shelf-scroll flex gap-4 overflow-x-auto pb-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {clueGroups.length === 0 ? (
              <ClueCreateCard onSelect={onOpenClues} />
            ) : (
              clueGroups.map((group) => (
                <ClueSummary key={group.source} group={group} onSelect={onOpenClues} />
              ))
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

interface ShelfEntry {
  item?: ContinueItem
  summary?: VideoSummary
  progressMs?: number
  durationMs?: number | null
  onSelect: () => void
}

function Shelf({
  id,
  title,
  items,
  action,
}: {
  id: string
  title: string
  items: ShelfEntry[]
  action?: { label: string; onClick: () => void }
}) {
  if (items.length === 0) return null
  return (
    <section className="animate-fade-up min-w-0">
      <div className="mb-2 flex items-end justify-between gap-4 px-1">
        <h2 className="text-xl font-semibold tracking-tight text-white/95">{title}</h2>
        {action ? (
          <Focusable scale="sm" onEnter={action.onClick}>
            <span className="flex items-center gap-1 rounded-full bg-black/25 px-3 py-1.5 text-[11px] text-white/65 backdrop-blur">
              {action.label}
              <ChevronRight className="h-3 w-3" />
            </span>
          </Focusable>
        ) : null}
      </div>
      <div className="shelf-scroll flex gap-5 overflow-x-auto pb-4 pt-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {items.map((entry, index) => (
          <WideCard
            key={entry.item?.key ?? entry.summary?.id}
            card={
              entry.item
                ? {
                    title: entry.item.meta.title,
                    coverUrl: entry.item.coverFile
                      ? `/api/library/media/${entry.item.coverFile}`
                      : api.image(entry.item.source, entry.item.meta.coverUrl),
                    quality: entry.item.quality,
                    category: entry.item.category ?? entry.item.source,
                    durationSec: entry.item.meta.durationSec,
                    sizeBytes: entry.item.sizeBytes,
                  }
                : {
                    title: entry.summary?.title ?? '',
                    coverUrl: api.image('local', entry.summary?.coverUrl),
                    quality: entry.summary?.note,
                    category: 'local',
                    durationSec: entry.summary?.durationSec,
                  }
            }
            progressMs={entry.progressMs}
            durationMs={entry.durationMs}
            focusKey={`HOME-${id.toUpperCase()}-${index}`}
            onSelect={entry.onSelect}
          />
        ))}
      </div>
    </section>
  )
}

function PlatformCard({
  platform,
  onSelect,
}: {
  platform: {
    id: string
    label: string
    mark: string
    gradient: string
    soon: boolean
    count: number
  }
  onSelect: () => void
}) {
  return (
    <Focusable focusKey={`PLATFORM-${platform.id}`} onEnter={onSelect} className="shrink-0">
      <div
        className={[
          'relative flex h-32 w-56 flex-col justify-between overflow-hidden rounded-[24px] bg-gradient-to-br p-5 ring-1 ring-white/15',
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

function ClueSummary({ group, onSelect }: { group: TreeGroup; onSelect: () => void }) {
  const total = group.total.discovered
  const done = group.total.done
  const pct = total ? Math.round((done / total) * 100) : 0

  return (
    <Focusable focusKey={`HOME-CLUE-${group.source}`} onEnter={onSelect} className="shrink-0">
      <div className="flex w-[22rem] items-center justify-between gap-4 rounded-2xl border border-white/10 bg-black/25 p-4 backdrop-blur-sm">
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
        <div className="w-20 shrink-0">
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

function ClueCreateCard({ onSelect }: { onSelect: () => void }) {
  return (
    <Focusable focusKey="HOME-CLUE-CREATE" onEnter={onSelect} className="shrink-0">
      <div className="flex h-[74px] w-[22rem] items-center gap-3 rounded-2xl border border-dashed border-white/20 bg-black/20 px-4 backdrop-blur-sm">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/10 text-white">
          <Plus className="h-5 w-5" />
        </span>
        <div>
          <p className="text-sm font-semibold text-white">创建第一条线索</p>
        </div>
      </div>
    </Focusable>
  )
}
