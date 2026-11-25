import type { DownloadJob, VideoDetail, VideoEpisode, VideoSummary } from '@libre-tv/shared/types'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from '@noriginmedia/norigin-spatial-navigation'
import { ChevronDown, ChevronRight, Download, Plus, RefreshCw, Trash2, X } from 'lucide-react'
import { api, fmtBytes, fmtDuration } from '@/api/client'
import type {
  Clue,
  ClueSource,
  DownloadsSnapshot,
  Fetcher,
  FetchersResponse,
  Trigger,
} from '@/api/client'
import { Focusable } from '@/components/Focusable'
import { Overlay } from '@/components/ui/Overlay'
import { PosterCard } from '@/components/PosterCard'
import { cn } from '@/lib/utils'

const EXPAND_OPTIONS = [
  { value: '', label: '无' },
  { value: 'favorites', label: '我的收藏' },
  { value: 'following', label: '我的关注' },
]

const ZERO = { discovered: 0, done: 0, pending: 0, failed: 0 }
const EMPTY_SNAPSHOT: DownloadsSnapshot = {
  jobs: [],
  counts: { queued: 0, downloading: 0, done: 0, error: 0 },
}

const jobKey = (source: string, id: string) => `${source}:${id}`
const fetcherKey = (id: number) => `FETCHER-${id}`
const clueKey = (id: number) => `CLUE-${id}`
const pagesLabel = (pages: number) => (pages === 0 ? '全部' : `前 ${pages} 页`)
const triggerLabel = (trigger: Trigger) => (trigger === 'manual' ? '手动' : trigger.toUpperCase())

/** 等列表刷新、新的焦点目标注册好之后再把焦点交过去；目标已不在就退回返回键 */
function refocus(key: string) {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      void setFocus(doesFocusableExist(key) ? key : 'TOP-BACK').catch(() => {})
    })
  })
}

/**
 * 线索：线索 = 一条发现规则，采集器 = 若干线索的并集 + 触发时机。
 * 左栏是采集器及其线索，右侧浏览选中线索对应的在线内容；点视频按最高清晰度入库，
 * 系列（番剧季度 / UGC 分P）可继续下探，逐集选择或全部下载。
 */
export function TreePage() {
  const [data, setData] = useState<FetchersResponse>()
  const [error, setError] = useState<string>()
  const [busyId, setBusyId] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [items, setItems] = useState<VideoSummary[]>([])
  const [browsePage, setBrowsePage] = useState(1)
  const [browseHasMore, setBrowseHasMore] = useState(false)
  const [browseBusy, setBrowseBusy] = useState(false)
  const [browseError, setBrowseError] = useState<string>()

  const [picker, setPicker] = useState<VideoDetail>()
  const [snapshot, setSnapshot] = useState<DownloadsSnapshot>(EMPTY_SNAPSHOT)
  const [visibleJobs, setVisibleJobs] = useState<DownloadJob[]>([])
  const [libraryKeys, setLibraryKeys] = useState<Set<string>>(new Set())

  const loadData = useCallback(async () => {
    try {
      setData(await api.fetchers())
      setError(undefined)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  // 下载列表被服务端裁剪：汇总给面板，当前浏览页那几张卡的任务再按 key 精确取一次
  const loadDownloads = useCallback(async (keys: string[]) => {
    try {
      const [summary, exact] = await Promise.all([
        api.downloads(),
        keys.length ? api.downloads({ keys }) : Promise.resolve(undefined),
      ])
      setSnapshot(summary)
      setVisibleJobs(exact ? exact.jobs : [])
    } catch {
      /* 状态条静默 */
    }
  }, [])

  const loadLibrary = useCallback(async () => {
    try {
      setLibraryKeys(new Set(await api.libraryKeys()))
    } catch {
      /* 状态条静默 */
    }
  }, [])

  useEffect(() => {
    void loadData()
    const id = setInterval(() => void loadData(), 5000)
    return () => clearInterval(id)
  }, [loadData])

  const browseKeys = useMemo(() => items.map((video) => jobKey(video.source, video.id)), [items])

  useEffect(() => {
    void loadDownloads(browseKeys)
    const id = setInterval(() => void loadDownloads(browseKeys), 2000)
    return () => clearInterval(id)
  }, [loadDownloads, browseKeys])

  useEffect(() => {
    void loadLibrary()
    const id = setInterval(() => void loadLibrary(), 4000)
    return () => clearInterval(id)
  }, [loadLibrary])

  const selected = useMemo(() => findClue(data, selectedId), [data, selectedId])

  useEffect(() => {
    if (selectedId !== null && data && !findClue(data, selectedId)) {
      setSelectedId(null)
      setItems([])
    }
  }, [data, selectedId])

  const jobsByKey = useMemo(
    () => new Map([...snapshot.jobs, ...visibleJobs].map((job) => [job.key, job])),
    [snapshot, visibleJobs],
  )

  // 轮询会增删获焦的行（删除、任务结束、列表裁剪）：焦点一旦落空，方向键就全死了，退回返回键
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      if (!getCurrentFocusKey()) void setFocus('TOP-BACK').catch(() => {})
    })
    return () => cancelAnimationFrame(raf)
  }, [data, snapshot, visibleJobs])

  const loadBrowse = useCallback(async (clue: Clue, page = 1) => {
    setBrowseBusy(true)
    setBrowseError(undefined)
    try {
      const res = await api.clueItems(clue.id, page)
      setItems((prev) => (page === 1 ? res.items : [...prev, ...res.items]))
      setBrowsePage(res.page)
      setBrowseHasMore(res.hasMore)
    } catch (e) {
      setBrowseError((e as Error).message)
    } finally {
      setBrowseBusy(false)
    }
  }, [])

  const selectClue = (clue: Clue) => {
    if (selectedId === clue.id) {
      setSelectedId(null)
      setItems([])
      return
    }
    setSelectedId(clue.id)
    setItems([])
    setBrowseHasMore(false)
    setBrowseError(undefined)
    void loadBrowse(clue, 1)
  }

  /** 删除之后焦点该落在哪：同卡相邻一条 → 采集器卡 → 上一张卡 → 返回键 */
  const fallbackKey = (kind: 'fetcher' | 'clue', id: number): string => {
    const fetchers = data?.fetchers ?? []
    if (kind === 'clue') {
      const owner = fetchers.find((item) => item.clues.some((clue) => clue.id === id))
      if (!owner) return 'TOP-BACK'
      const index = owner.clues.findIndex((clue) => clue.id === id)
      const neighbour = owner.clues[index + 1] ?? owner.clues[index - 1]
      return neighbour ? clueKey(neighbour.id) : fetcherKey(owner.id)
    }
    const index = fetchers.findIndex((item) => item.id === id)
    const neighbour = fetchers[index + 1] ?? fetchers[index - 1]
    return neighbour ? fetcherKey(neighbour.id) : 'TOP-BACK'
  }

  const run = async (fetcher: Fetcher) => {
    setBusyId(`fetcher-${fetcher.id}-run`)
    try {
      await api.runFetcher(fetcher.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
      void loadData()
    }
  }

  const removeFetcher = async (fetcher: Fetcher) => {
    const target = fallbackKey('fetcher', fetcher.id)
    setBusyId(`fetcher-${fetcher.id}-del`)
    try {
      await api.deleteFetcher(fetcher.id)
      if (fetcher.clues.some((clue) => clue.id === selectedId)) {
        setSelectedId(null)
        setItems([])
      }
      await loadData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
    }
    refocus(target)
  }

  const removeClue = async (clue: Clue) => {
    const target = fallbackKey('clue', clue.id)
    setBusyId(`clue-${clue.id}-del`)
    try {
      await api.deleteClue(clue.id)
      if (selectedId === clue.id) {
        setSelectedId(null)
        setItems([])
      }
      await loadData()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
    }
    refocus(target)
  }

  const openOnline = async (video: VideoSummary) => {
    try {
      if (libraryKeys.has(jobKey(video.source, video.id))) return
      const detail = await api.detail(video.source, video.id)
      const episodes = detail.episodes ?? []
      if (video.kind === 'series' && episodes.length === 0) return
      // 季度（kind=series）与 UGC 多P 先下探；PGC 单集本身就是一个最小下载单元
      const expandable =
        episodes.length > 0 &&
        (video.kind === 'series' || (!video.id.startsWith('ep') && episodes.length > 1))
      if (expandable) {
        setPicker(detail)
        return
      }
      await api.download(video.source, video.id)
      void loadDownloads(browseKeys)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const downloadEpisode = async (source: string, episode: VideoEpisode) => {
    try {
      await api.download(source, episode.id)
      void loadDownloads(browseKeys)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const downloadAll = async (source: string, episodes: VideoEpisode[]) => {
    let fail = 0
    for (const episode of episodes) {
      try {
        await api.download(source, episode.id)
      } catch {
        fail += 1
      }
    }
    if (fail) setError(`${fail} 集下载失败`)
    void loadDownloads(browseKeys)
  }

  const retryJob = async (job: DownloadJob) => {
    try {
      await api.download(job.source, job.id, job.quality === 'auto' ? undefined : job.quality)
      void loadDownloads(browseKeys)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const cancelJob = async (job: DownloadJob) => {
    try {
      await api.cancelDownload(job.key)
      void loadDownloads(browseKeys)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const closePicker = () => setPicker(undefined)

  // 弹层退场动画结束后再把焦点还给选中的线索；线索已删则退回返回键
  const restoreClueFocus = () => {
    refocus(selectedId === null ? 'TOP-BACK' : clueKey(selectedId))
  }

  const total = data?.total ?? ZERO
  const qualityOptions = useMemo(
    () => Array.from(new Set((data?.sources ?? []).flatMap((source) => source.qualityOptions))),
    [data],
  )
  const sourceName = (id: string) =>
    data?.sources.find((source) => source.id === id)?.name ?? id

  return (
    <div className="space-y-6 px-8 pb-24 pt-28 xl:px-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-2xl font-bold text-white">线索</h1>
        <div className="w-64">
          <Bar done={total.done} discovered={total.discovered} />
        </div>
      </div>

      {error ? <p className="text-sm text-amber-300">{error}</p> : null}

      {data ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <FetcherForm
            triggers={data.triggers}
            qualityOptions={qualityOptions}
            onCreated={loadData}
          />
          <ClueForm data={data} onCreated={loadData} />
        </div>
      ) : null}

      <DownloadPanel
        snapshot={snapshot}
        onRetry={(job) => void retryJob(job)}
        onCancel={(job) => void cancelJob(job)}
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(300px,380px)_1fr]">
        <aside className="space-y-4">
          {(data?.fetchers ?? []).map((fetcher) => (
            <FetcherCard
              key={fetcher.id}
              fetcher={fetcher}
              selectedId={selectedId}
              busyId={busyId}
              sourceName={sourceName}
              onSelect={selectClue}
              onRun={() => void run(fetcher)}
              onDelete={() => void removeFetcher(fetcher)}
              onDeleteClue={(clue) => void removeClue(clue)}
            />
          ))}
        </aside>

        <BrowsePanel
          clue={selected}
          sourceName={selected ? sourceName(selected.source) : ''}
          items={items}
          busy={browseBusy}
          error={browseError}
          hasMore={browseHasMore}
          jobsByKey={jobsByKey}
          libraryKeys={libraryKeys}
          onRefresh={() => selected && void loadBrowse(selected, 1)}
          onMore={() => selected && void loadBrowse(selected, browsePage + 1)}
          onOpen={(video) => void openOnline(video)}
        />
      </div>

      {picker ? (
        <EpisodePicker
          key={picker.id}
          detail={picker}
          jobsByKey={jobsByKey}
          libraryKeys={libraryKeys}
          onClose={closePicker}
          onExited={restoreClueFocus}
          onDownload={(episode) => void downloadEpisode(picker.source, episode)}
          onDownloadAll={(episodes) => downloadAll(picker.source, episodes)}
        />
      ) : null}
    </div>
  )
}

function FetcherForm({
  triggers,
  qualityOptions,
  onCreated,
}: {
  triggers: FetchersResponse['triggers']
  qualityOptions: string[]
  onCreated: () => Promise<void>
}) {
  const [name, setName] = useState('')
  const [trigger, setTrigger] = useState<Trigger>('manual')
  const [quality, setQuality] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()

  const submit = async () => {
    setBusy(true)
    setError(undefined)
    try {
      await api.createFetcher({ name: name.trim(), trigger, quality: quality || undefined })
      setName('')
      setTrigger('manual')
      setQuality('')
      await onCreated()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="rounded-3xl bg-white/[0.06] p-5 ring-1 ring-white/10">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-white">
        <Plus className="h-4 w-4" />
        新建采集器
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="名称">
          <input value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
        </Field>
        <Field label="触发">
          <select
            value={trigger}
            onChange={(e) => setTrigger(e.target.value as Trigger)}
            className={inputClass}
          >
            {triggers.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="清晰度">
          <select value={quality} onChange={(e) => setQuality(e.target.value)} className={inputClass}>
            <option value="">跟随默认</option>
            {qualityOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <FormFooter error={error} busy={busy} label="创建采集器" onSubmit={submit} />
    </section>
  )
}

function ClueForm({ data, onCreated }: { data: FetchersResponse; onCreated: () => Promise<void> }) {
  const sources = data.sources
  const [fetcherId, setFetcherId] = useState<number | ''>(data.fetchers[0]?.id ?? '')
  const [sourceId, setSourceId] = useState(sources[0]?.id ?? '')
  const [partition, setPartition] = useState('')
  const [keyword, setKeyword] = useState('')
  const [uploader, setUploader] = useState('')
  const [expand, setExpand] = useState('')
  const [pages, setPages] = useState(1)
  const [confirmAll, setConfirmAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()

  // 第一张采集器是后建出来的、或选中的那张被删了：自动落到第一张
  useEffect(() => {
    if (data.fetchers.some((item) => item.id === fetcherId)) return
    setFetcherId(data.fetchers[0]?.id ?? '')
  }, [data.fetchers, fetcherId])

  useEffect(() => {
    if (!confirmAll) return
    const id = setTimeout(() => setConfirmAll(false), 4000)
    return () => clearTimeout(id)
  }, [confirmAll])

  const source: ClueSource | undefined = sources.find((item) => item.id === sourceId) ?? sources[0]
  const shows = (field: string) => Boolean(source?.fields.includes(field))
  const canExpand = Boolean(source?.kinds.some((kind) => kind !== 'search'))
  const expanding = expand !== ''
  const categories = source?.categories ?? []

  const pickSource = (next: string) => {
    setSourceId(next)
    setPartition('')
    setKeyword('')
    setUploader('')
    setExpand('')
    setConfirmAll(false)
  }

  const submit = async () => {
    // 「全部」首轮可能入队上万条：沿用分集「全部下载」的两步确认
    if (pages === 0 && !confirmAll) {
      setConfirmAll(true)
      return
    }
    setBusy(true)
    setError(undefined)
    try {
      await api.createClue({
        fetcherId: Number(fetcherId),
        source: source?.id ?? '',
        kind: expanding ? expand : undefined,
        partition: expanding || !partition ? undefined : partition,
        keyword: expanding ? undefined : keyword.trim() || undefined,
        uploader: expanding ? undefined : uploader.trim() || undefined,
        pages,
      })
      setPartition('')
      setKeyword('')
      setUploader('')
      setConfirmAll(false)
      await onCreated()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const confirmLabel = confirmAll ? '确认创建（全部）' : '创建线索'

  return (
    <section className="rounded-3xl bg-white/[0.06] p-5 ring-1 ring-white/10">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-white">
        <Plus className="h-4 w-4" />
        新建线索
      </div>

      <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-4">
        <Field label="采集器">
          <select
            value={fetcherId}
            disabled={data.fetchers.length === 0}
            onChange={(e) => setFetcherId(e.target.value ? Number(e.target.value) : '')}
            className={inputClass}
          >
            {data.fetchers.length === 0 ? <option value="">无</option> : null}
            {data.fetchers.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="来源">
          <select value={source?.id ?? ''} onChange={(e) => pickSource(e.target.value)} className={inputClass}>
            {sources.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </Field>

        {canExpand ? (
          <Field label="展开">
            <select
              value={expand}
              onChange={(e) => {
                setExpand(e.target.value)
                setConfirmAll(false)
              }}
              className={inputClass}
            >
              {EXPAND_OPTIONS.filter(
                (option) => option.value === '' || source?.kinds.includes(option.value),
              ).map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>
        ) : null}

        {shows('category') && !expanding ? (
          <Field label="分区">
            <select
              value={partition}
              onChange={(e) => {
                setPartition(e.target.value)
                setConfirmAll(false)
              }}
              className={inputClass}
            >
              <option value="">全部</option>
              {categories.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}

        {shows('keyword') && !expanding ? (
          <Field label="关键词">
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              className={inputClass}
            />
          </Field>
        ) : null}

        {shows('uploader') && !expanding ? (
          <Field label="画师 ID">
            <input
              value={uploader}
              onChange={(e) => setUploader(e.target.value)}
              className={inputClass}
            />
          </Field>
        ) : null}

        <Field label={expanding ? '每个画师取' : '数量'}>
          <select
            value={pages}
            onChange={(e) => {
              setPages(Number(e.target.value))
              setConfirmAll(false)
            }}
            className={inputClass}
          >
            {data.pageOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <FormFooter
        error={error}
        busy={busy}
        label={confirmLabel}
        accent={confirmAll}
        onSubmit={submit}
      />
    </section>
  )
}

function FormFooter({
  error,
  busy,
  label,
  accent = false,
  onSubmit,
}: {
  error?: string
  busy: boolean
  label: string
  accent?: boolean
  onSubmit: () => void
}) {
  return (
    <div className="mt-4 flex items-center justify-between">
      <span className="text-xs text-amber-300">{error ?? ''}</span>
      <Focusable scale="sm" onEnter={busy ? undefined : onSubmit}>
        <span
          className={cn(
            'flex items-center gap-2 rounded-full px-5 py-2 text-xs font-semibold',
            accent ? 'bg-amber-400 text-black' : 'bg-white text-black',
          )}
        >
          {busy ? (
            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Plus className="h-3.5 w-3.5" />
          )}
          {label}
        </span>
      </Focusable>
    </div>
  )
}

function FetcherCard({
  fetcher,
  selectedId,
  busyId,
  sourceName,
  onSelect,
  onRun,
  onDelete,
  onDeleteClue,
}: {
  fetcher: Fetcher
  selectedId: number | null
  busyId: string | null
  sourceName: (id: string) => string
  onSelect: (clue: Clue) => void
  onRun: () => void
  onDelete: () => void
  onDeleteClue: (clue: Clue) => void
}) {
  const [open, setOpen] = useState(true)
  const stats = fetcher.stats
  const running = fetcher.status === 'running'
  const prefix = `fetcher-${fetcher.id}-`
  const busy = busyId?.startsWith(prefix) ?? false

  return (
    <section className="rounded-3xl bg-white/[0.04] p-4 ring-1 ring-white/10">
      <Focusable focusKey={fetcherKey(fetcher.id)} scale="sm" onEnter={() => setOpen((value) => !value)}>
        <div className="rounded-2xl">
          <div className="flex items-center gap-2">
            {open ? (
              <ChevronDown className="h-4 w-4 shrink-0 text-zinc-400" />
            ) : (
              <ChevronRight className="h-4 w-4 shrink-0 text-zinc-400" />
            )}
            <StatusDot status={fetcher.status} />
            <span className="min-w-0 flex-1 truncate text-base font-semibold text-white">
              {fetcher.name}
            </span>
            <Chip>{triggerLabel(fetcher.trigger)}</Chip>
            {fetcher.quality ? <Chip tone="emerald">{fetcher.quality}</Chip> : null}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <div className="flex-1">
              <Bar done={stats.done} discovered={stats.discovered} />
            </div>
            <span className="shrink-0 text-[10px] tabular-nums text-zinc-500">
              {stats.done}/{stats.discovered}
            </span>
            {stats.failed > 0 ? (
              <span className="shrink-0 text-[10px] text-amber-300">失败 {stats.failed}</span>
            ) : null}
          </div>
          {fetcher.lastError ? (
            <p className="mt-1 line-clamp-1 text-[10px] text-amber-300/80">{fetcher.lastError}</p>
          ) : null}
        </div>
      </Focusable>

      <div className="mt-2 flex items-center gap-2 pl-1">
        <Focusable scale="sm" onEnter={busy || running ? undefined : onRun}>
          <span className="flex items-center gap-1 rounded-full bg-white/10 px-2.5 py-1 text-[10px] text-white">
            <RefreshCw className={`h-3 w-3 ${running ? 'animate-spin' : ''}`} />
            立即运行
          </span>
        </Focusable>
        <Focusable scale="sm" onEnter={busy ? undefined : onDelete}>
          <span className="flex items-center gap-1 rounded-full bg-white/5 px-2.5 py-1 text-[10px] text-zinc-400">
            <Trash2 className="h-3 w-3" />
            删除
          </span>
        </Focusable>
        <span className="text-[10px] text-zinc-500">{fetcher.clues.length} 条线索</span>
      </div>

      {open ? (
        <div className="mt-3 space-y-2">
          {fetcher.clues.map((clue) => (
            <ClueRow
              key={clue.id}
              clue={clue}
              sourceName={sourceName(clue.source)}
              selected={selectedId === clue.id}
              busy={busyId === `clue-${clue.id}-del`}
              onSelect={() => onSelect(clue)}
              onDelete={() => onDeleteClue(clue)}
            />
          ))}
        </div>
      ) : null}
    </section>
  )
}

function ClueRow({
  clue,
  sourceName,
  selected,
  busy,
  onSelect,
  onDelete,
}: {
  clue: Clue
  sourceName: string
  selected: boolean
  busy: boolean
  onSelect: () => void
  onDelete: () => void
}) {
  const stats = clue.stats
  return (
    <div className="space-y-1.5">
      <Focusable focusKey={clueKey(clue.id)} scale="sm" onEnter={onSelect}>
        <div
          className={cn(
            'rounded-2xl px-3 py-2 ring-1 transition',
            selected ? 'bg-emerald-400/10 ring-emerald-400/60' : 'bg-white/[0.04] ring-white/5',
          )}
        >
          <div className="flex items-center gap-2">
            <StatusDot status={clue.status} />
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-white">{clue.title}</span>
            <Chip>{sourceName}</Chip>
          </div>
          <div className="mt-1.5 flex items-center gap-2">
            <div className="flex-1">
              <Bar done={stats.done} discovered={stats.discovered} />
            </div>
            <span className="shrink-0 text-[10px] tabular-nums text-zinc-500">
              {stats.done}/{stats.discovered}
            </span>
            {stats.failed > 0 ? (
              <span className="shrink-0 text-[10px] text-amber-300">失败 {stats.failed}</span>
            ) : null}
          </div>
          {clue.lastError ? (
            <p className="mt-1 line-clamp-1 text-[10px] text-amber-300/80">{clue.lastError}</p>
          ) : null}
        </div>
      </Focusable>

      {clue.derived ? null : (
        <div className="flex items-center gap-2 pl-1">
          <Focusable scale="sm" onEnter={busy ? undefined : onDelete}>
            <span className="flex items-center gap-1 rounded-full bg-white/5 px-2.5 py-1 text-[10px] text-zinc-400">
              <Trash2 className="h-3 w-3" />
              删除
            </span>
          </Focusable>
        </div>
      )}
    </div>
  )
}

function BrowsePanel({
  clue,
  sourceName,
  items,
  busy,
  error,
  hasMore,
  jobsByKey,
  libraryKeys,
  onRefresh,
  onMore,
  onOpen,
}: {
  clue?: Clue
  sourceName: string
  items: VideoSummary[]
  busy: boolean
  error?: string
  hasMore: boolean
  jobsByKey: Map<string, DownloadJob>
  libraryKeys: Set<string>
  onRefresh: () => void
  onMore: () => void
  onOpen: (video: VideoSummary) => void
}) {
  if (!clue) {
    return <section className="min-h-[320px]" />
  }

  return (
    <section className="min-h-[320px] space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 pb-3">
        <div className="flex min-w-0 items-center gap-3">
          <h2 className="truncate text-lg font-semibold text-white">{clue.title}</h2>
          <span className="shrink-0 text-[11px] text-zinc-500">
            {sourceName} · {pagesLabel(clue.pages)} · {items.length}
          </span>
        </div>
        <Focusable scale="sm" onEnter={busy ? undefined : onRefresh}>
          <span className="flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1.5 text-[11px] text-white">
            <RefreshCw className={`h-3 w-3 ${busy ? 'animate-spin' : ''}`} />
            刷新
          </span>
        </Focusable>
      </header>

      {error ? <p className="text-sm text-amber-300">{error}</p> : null}

      {busy && items.length === 0 ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {Array.from({ length: 10 }).map((_, index) => (
            <div key={index} className="aspect-[3/4] animate-pulse rounded-2xl bg-white/[0.06]" />
          ))}
        </div>
      ) : items.length === 0 ? null : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {items.map((video, index) => (
            <PosterCard
              key={`${video.source}:${video.id}`}
              fluid
              video={video}
              job={jobsByKey.get(jobKey(video.source, video.id))}
              inLibrary={libraryKeys.has(jobKey(video.source, video.id))}
              focusKey={`BROWSE-${clue.id}-${index}`}
              onSelect={() => onOpen(video)}
            />
          ))}
        </div>
      )}

      {hasMore ? (
        <div className="flex justify-center pt-2">
          <Focusable scale="sm" onEnter={busy ? undefined : onMore}>
            <span className="flex items-center gap-1.5 rounded-full bg-white/10 px-5 py-2 text-xs font-medium text-white">
              <RefreshCw className={`h-3 w-3 ${busy ? 'animate-spin' : ''}`} />
              加载更多
            </span>
          </Focusable>
        </div>
      ) : null}
    </section>
  )
}

function DownloadPanel({
  snapshot,
  onRetry,
  onCancel,
}: {
  snapshot: DownloadsSnapshot
  onRetry: (job: DownloadJob) => void
  onCancel: (job: DownloadJob) => void
}) {
  const [open, setOpen] = useState(false)
  const { jobs, counts } = snapshot
  const active = counts.queued + counts.downloading
  if (jobs.length === 0) return null

  return (
    <section className="rounded-3xl bg-white/[0.04] ring-1 ring-white/10">
      <Focusable focusKey="DOWNLOAD-HEAD" scale="sm" onEnter={() => setOpen((value) => !value)}>
        <div className="flex items-center justify-between px-5 py-3">
          <span className="flex items-center gap-2 text-sm font-semibold text-white">
            {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            下载任务
            <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] font-normal text-zinc-300">
              进行中 {active} · 已完成 {counts.done} · 失败 {counts.error}
            </span>
          </span>
        </div>
      </Focusable>

      <div
        className="grid transition-[grid-template-rows,opacity] duration-[480ms] ease-aerial"
        style={{ gridTemplateRows: open ? '1fr' : '0fr', opacity: open ? 1 : 0 }}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="max-h-[320px] overflow-y-auto border-t border-white/5">
            {jobs.map((job) => (
              <div
                key={job.key}
                className="flex items-center gap-3 border-b border-white/5 px-5 py-3 last:border-0"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-white">{job.title || job.key}</p>
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-zinc-500">
                    <span>{job.source}</span>
                    <span>· {job.quality}</span>
                    {job.fromLibrary ? <span className="text-emerald-300">· 本地已有</span> : null}
                    {job.qualityFallback ? <span className="text-amber-300">· 已降级</span> : null}
                    <span>
                      · {fmtBytes(job.receivedBytes)}
                      {job.totalBytes ? ` / ${fmtBytes(job.totalBytes)}` : ''}
                    </span>
                  </div>
                  <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/10">
                    <div
                      className={[
                        'h-full rounded-full transition-all duration-300',
                        job.status === 'done'
                          ? 'bg-emerald-400'
                          : job.status === 'error'
                            ? 'bg-amber-400'
                            : 'bg-white',
                      ].join(' ')}
                      style={{ width: `${job.status === 'done' ? 100 : job.progress}%` }}
                    />
                  </div>
                  {job.error ? (
                    <p className="mt-1 line-clamp-1 text-[11px] text-amber-300/90">{job.error}</p>
                  ) : null}
                </div>

                <StatusChip job={job} />

                {job.status === 'error' ? (
                  <Focusable focusKey={`JOB-${job.key}-RETRY`} scale="sm" onEnter={() => onRetry(job)}>
                    <span className="rounded-full bg-white/10 px-3 py-1 text-[11px] text-white">
                      重试
                    </span>
                  </Focusable>
                ) : job.status === 'queued' || job.status === 'downloading' ? (
                  <Focusable focusKey={`JOB-${job.key}-CANCEL`} scale="sm" onEnter={() => onCancel(job)}>
                    <span className="rounded-full bg-white/5 px-3 py-1 text-[11px] text-zinc-400">
                      取消
                    </span>
                  </Focusable>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}

function EpisodePicker({
  detail,
  jobsByKey,
  libraryKeys,
  onClose,
  onExited,
  onDownload,
  onDownloadAll,
}: {
  detail: VideoDetail
  jobsByKey: Map<string, DownloadJob>
  libraryKeys: Set<string>
  onClose: () => void
  onExited: () => void
  onDownload: (episode: VideoEpisode) => void
  onDownloadAll: (episodes: VideoEpisode[]) => Promise<void>
}) {
  const [confirmAll, setConfirmAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const [visible, setVisible] = useState(60)
  const episodes = detail.episodes ?? []

  const jobFor = (episode: VideoEpisode) => jobsByKey.get(jobKey(detail.source, episode.id))
  const inLibrary = (episode: VideoEpisode) => libraryKeys.has(jobKey(detail.source, episode.id))
  const pending = episodes.filter((episode) => {
    const job = jobFor(episode)
    if (inLibrary(episode)) return false
    return (
      !job || (job.status !== 'done' && job.status !== 'downloading' && job.status !== 'queued')
    )
  })

  useEffect(() => {
    if (!confirmAll) return
    const id = setTimeout(() => setConfirmAll(false), 4000)
    return () => clearTimeout(id)
  }, [confirmAll])

  const downloadAll = async () => {
    if (pending.length === 0) return
    if (!confirmAll) {
      setConfirmAll(true)
      return
    }
    setConfirmAll(false)
    setBusy(true)
    try {
      await onDownloadAll(pending)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Overlay
      open
      onClose={onClose}
      onEntered={() => void setFocus('PICKER-ALL').catch(() => {})}
      onExited={onExited}
    >
      <header className="flex items-start justify-between gap-4 border-b border-white/10 px-6 py-4">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold text-white">{detail.title}</h2>
          <p className="mt-1 text-[11px] text-zinc-500">共 {episodes.length} 集</p>
        </div>
        <Focusable focusKey="PICKER-CLOSE" scale="sm" onEnter={onClose}>
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-white/10 text-white">
            <X className="h-4 w-4" />
          </span>
        </Focusable>
      </header>

      <div className="flex items-center justify-between gap-4 border-b border-white/5 px-6 py-3">
        <Focusable
          focusKey="PICKER-ALL"
          scale="sm"
          onEnter={busy ? undefined : () => void downloadAll()}
        >
          <span
            className={[
              'flex items-center gap-2 rounded-full px-4 py-2 text-xs font-semibold',
              pending.length === 0
                ? 'bg-white/10 text-zinc-500'
                : confirmAll
                  ? 'bg-amber-400 text-black'
                  : 'bg-white text-black',
            ].join(' ')}
          >
            {busy ? (
              <RefreshCw className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            {pending.length === 0
              ? '全部已入库'
              : confirmAll
                ? `确认全部下载 ${pending.length} 集`
                : `全部下载（${pending.length} 集）`}
          </span>
        </Focusable>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2">
        {episodes.slice(0, visible).map((episode) => {
          const job = jobFor(episode)
          const local = inLibrary(episode)
          const locked =
            local ||
            job?.status === 'done' ||
            job?.status === 'downloading' ||
            job?.status === 'queued'
          return (
            <Focusable
              key={episode.id}
              focusKey={`PICK-${episode.id}`}
              scale="sm"
              onEnter={locked ? undefined : () => onDownload(episode)}
            >
              <div className="flex items-center gap-3 rounded-xl px-3 py-2.5 transition hover:bg-white/5">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-white">{episode.title}</p>
                  <div className="mt-1 flex items-center gap-2 text-[11px] text-zinc-500">
                    {episode.durationSec ? <span>{fmtDuration(episode.durationSec)}</span> : null}
                    {job?.status === 'downloading' ? (
                      <span>
                        {job.progress}% · {fmtBytes(job.receivedBytes)}
                      </span>
                    ) : null}
                    {job?.error ? (
                      <span className="truncate text-amber-300/90">{job.error}</span>
                    ) : null}
                  </div>
                  {job && job.status !== 'done' ? (
                    <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/10">
                      <div
                        className={[
                          'h-full rounded-full transition-all duration-300',
                          job.status === 'error' ? 'bg-amber-400' : 'bg-emerald-400',
                        ].join(' ')}
                        style={{ width: `${job.progress}%` }}
                      />
                    </div>
                  ) : null}
                </div>
                {job ? (
                  <StatusChip job={job} />
                ) : local ? (
                  <span className="shrink-0 rounded-full bg-emerald-400/15 px-3 py-1 text-[11px] text-emerald-300">
                    已入库
                  </span>
                ) : (
                  <span className="rounded-full bg-white/10 px-3 py-1 text-[11px] text-white">
                    下载
                  </span>
                )}
              </div>
            </Focusable>
          )
        })}

        {visible < episodes.length ? (
          <div className="flex justify-center py-3">
            <Focusable scale="sm" onEnter={() => setVisible((value) => value + 100)}>
              <span className="rounded-full bg-white/10 px-4 py-1.5 text-[11px] text-white">
                显示更多
              </span>
            </Focusable>
          </div>
        ) : null}
      </div>
    </Overlay>
  )
}

function StatusChip({ job }: { job: DownloadJob }) {
  const label =
    job.status === 'downloading'
      ? `${job.progress}%`
      : job.status === 'queued'
        ? '排队中'
        : job.status === 'done'
          ? job.fromLibrary
            ? '已入库'
            : '已完成'
          : '重试'
  const className =
    job.status === 'done'
      ? 'bg-emerald-400/15 text-emerald-300'
      : job.status === 'error'
        ? 'bg-amber-400/15 text-amber-300'
        : 'bg-white/10 text-zinc-300'
  return (
    <span className={`shrink-0 rounded-full px-3 py-1 text-[11px] tabular-nums ${className}`}>
      {label}
    </span>
  )
}

function Chip({ children, tone = 'plain' }: { children: React.ReactNode; tone?: 'plain' | 'emerald' }) {
  return (
    <span
      className={cn(
        'shrink-0 rounded-full px-2 py-0.5 text-[10px]',
        tone === 'emerald' ? 'bg-emerald-400/15 text-emerald-300' : 'bg-white/10 text-zinc-400',
      )}
    >
      {children}
    </span>
  )
}

function Bar({ done, discovered }: { done: number; discovered: number }) {
  const pct = discovered ? Math.round((done / discovered) * 100) : 0
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
      <div
        className="h-full rounded-full bg-emerald-400 transition-all duration-500"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === 'ok'
      ? 'bg-emerald-400'
      : status === 'error'
        ? 'bg-amber-400'
        : status === 'running'
          ? 'bg-sky-400'
          : 'bg-zinc-500'
  return <span className={cn('h-2 w-2 shrink-0 rounded-full', color)} />
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium text-zinc-400">{label}</span>
      {children}
    </label>
  )
}

function findClue(data: FetchersResponse | undefined, id: number | null): Clue | undefined {
  if (!data || id === null) return undefined
  for (const fetcher of data.fetchers) {
    const clue = fetcher.clues.find((item) => item.id === id)
    if (clue) return clue
  }
  return undefined
}

const inputClass =
  'w-full rounded-xl bg-black/40 px-3 py-2.5 text-sm text-white outline-none ring-1 ring-white/10 transition focus:ring-white/40 disabled:opacity-50'
