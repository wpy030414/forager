import type { DownloadJob, VideoDetail, VideoEpisode, VideoSummary } from '@libre-tv/shared/types'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { setFocus } from '@noriginmedia/norigin-spatial-navigation'
import { ChevronDown, ChevronRight, Download, Plus, RefreshCw, Trash2, X } from 'lucide-react'
import { api, fmtBytes, fmtDuration } from '@/api/client'
import type { TreeGroup, TreeNode, TreeResponse } from '@/api/client'
import { Focusable } from '@/components/Focusable'
import { PosterCard } from '@/components/PosterCard'

const KIND_LABELS: Record<string, string> = {
  query: '关键词',
  partition: '分区',
  uploader: '发布者',
  favorites: '我的收藏',
  following: '我的关注',
}

const EXPAND_KINDS = new Set(['favorites', 'following'])

const INTERVALS = [
  { value: 1800, label: '30 分钟' },
  { value: 3600, label: '1 小时' },
  { value: 10800, label: '3 小时' },
  { value: 21600, label: '6 小时' },
  { value: 43200, label: '12 小时' },
  { value: 86400, label: '24 小时' },
]

const jobKey = (source: string, id: string) => `${source}:${id}`

/**
 * 线索：每条线索 = 一条持续发现规则。
 * 左侧选线索，右侧浏览源站在线内容；点视频按最高清晰度入库，
 * 系列（番剧季度 / UGC 分P）可继续下探，逐集选择或全部下载。
 */
export function TreePage() {
  const [tree, setTree] = useState<TreeResponse>()
  const [error, setError] = useState<string>()
  const [busyId, setBusyId] = useState<number | null>(null)

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [items, setItems] = useState<VideoSummary[]>([])
  const [browsePage, setBrowsePage] = useState(1)
  const [browseHasMore, setBrowseHasMore] = useState(false)
  const [browseBusy, setBrowseBusy] = useState(false)
  const [browseError, setBrowseError] = useState<string>()

  const [picker, setPicker] = useState<VideoDetail>()
  const [jobs, setJobs] = useState<DownloadJob[]>([])
  const [libraryKeys, setLibraryKeys] = useState<Set<string>>(new Set())

  const loadTree = useCallback(async () => {
    try {
      setTree(await api.tree())
      setError(undefined)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  const loadJobs = useCallback(async () => {
    try {
      setJobs(await api.downloads())
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
    void loadTree()
    const id = setInterval(() => void loadTree(), 5000)
    return () => clearInterval(id)
  }, [loadTree])

  useEffect(() => {
    void loadJobs()
    const id = setInterval(() => void loadJobs(), 2000)
    return () => clearInterval(id)
  }, [loadJobs])

  useEffect(() => {
    void loadLibrary()
    const id = setInterval(() => void loadLibrary(), 4000)
    return () => clearInterval(id)
  }, [loadLibrary])

  const selected = useMemo(() => findNode(tree, selectedId), [tree, selectedId])

  useEffect(() => {
    if (selectedId !== null && tree && !findNode(tree, selectedId)) {
      setSelectedId(null)
      setItems([])
    }
  }, [tree, selectedId])

  const jobsByKey = useMemo(() => new Map(jobs.map((job) => [job.key, job])), [jobs])

  const loadBrowse = useCallback(async (node: TreeNode, page = 1) => {
    setBrowseBusy(true)
    setBrowseError(undefined)
    try {
      const res = await api.browseNode(node.id, page)
      setItems((prev) => (page === 1 ? res.items : [...prev, ...res.items]))
      setBrowsePage(res.page)
      setBrowseHasMore(res.hasMore)
    } catch (e) {
      setBrowseError((e as Error).message)
    } finally {
      setBrowseBusy(false)
    }
  }, [])

  const selectNode = (node: TreeNode) => {
    if (EXPAND_KINDS.has(node.kind)) return
    if (selectedId === node.id) {
      setSelectedId(null)
      setItems([])
      return
    }
    setSelectedId(node.id)
    setItems([])
    setBrowseHasMore(false)
    setBrowseError(undefined)
    void loadBrowse(node, 1)
  }

  const sync = async (node: TreeNode) => {
    setBusyId(node.id)
    try {
      await api.syncNode(node.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
      void loadTree()
      void loadJobs()
    }
  }

  const remove = async (node: TreeNode) => {
    setBusyId(node.id)
    try {
      await api.deleteNode(node.id)
      if (selectedId === node.id) {
        setSelectedId(null)
        setItems([])
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
      void loadTree()
    }
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
      void loadJobs()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const downloadEpisode = async (source: string, episode: VideoEpisode) => {
    try {
      await api.download(source, episode.id)
      void loadJobs()
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
    void loadJobs()
  }

  const retryJob = async (job: DownloadJob) => {
    try {
      await api.download(job.source, job.id, job.quality === 'auto' ? undefined : job.quality)
      void loadJobs()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const cancelJob = async (job: DownloadJob) => {
    try {
      await api.cancelDownload(job.key)
      void loadJobs()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  useEffect(() => {
    if (!picker) return
    const id = setTimeout(() => {
      void setFocus('PICKER-ALL').catch(() => {})
    }, 80)
    return () => clearTimeout(id)
  }, [picker])

  const closePicker = () => {
    setPicker(undefined)
    if (selectedId !== null) {
      setTimeout(() => {
        void setFocus(`NODE-${selectedId}`).catch(() => {})
      }, 60)
    }
  }

  const total = tree?.total ?? { discovered: 0, done: 0 }

  return (
    <div className="space-y-6 px-8 pb-24 pt-28 xl:px-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-2xl font-bold text-white">线索</h1>
        <div className="w-64">
          <Bar done={total.done} discovered={total.discovered} />
        </div>
      </div>

      {error ? <p className="text-sm text-amber-300">{error}</p> : null}

      {tree ? <CreateForm groups={tree.sources} onCreated={loadTree} /> : null}

      <DownloadPanel
        jobs={jobs}
        onRetry={(job) => void retryJob(job)}
        onCancel={(job) => void cancelJob(job)}
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(300px,380px)_1fr]">
        <aside className="space-y-6">
          {(tree?.sources ?? []).map((group) => (
            <SourceGroup
              key={group.source}
              group={group}
              selectedId={selectedId}
              busyId={busyId}
              onSelect={selectNode}
              onSync={(node) => void sync(node)}
              onDelete={(node) => void remove(node)}
            />
          ))}
        </aside>

        <BrowsePanel
          node={selected}
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
          onDownload={(episode) => void downloadEpisode(picker.source, episode)}
          onDownloadAll={(episodes) => downloadAll(picker.source, episodes)}
        />
      ) : null}
    </div>
  )
}

function SourceGroup({
  group,
  selectedId,
  busyId,
  onSelect,
  onSync,
  onDelete,
}: {
  group: TreeGroup
  selectedId: number | null
  busyId: number | null
  onSelect: (node: TreeNode) => void
  onSync: (node: TreeNode) => void
  onDelete: (node: TreeNode) => void
}) {
  if (group.nodes.length === 0) return null
  return (
    <section className="space-y-3">
      <header className="flex items-center justify-between border-b border-white/5 pb-2">
        <h2 className="text-base font-semibold text-white">{group.name}</h2>
        <span className="text-[11px] text-zinc-500">
          {group.nodes.length} · {group.total.done}/{group.total.discovered}
        </span>
      </header>
      <div className="space-y-3">
        {group.nodes.map((node) => (
          <RuleNode
            key={node.id}
            node={node}
            depth={0}
            selectedId={selectedId}
            busyId={busyId}
            onSelect={onSelect}
            onSync={onSync}
            onDelete={onDelete}
          />
        ))}
      </div>
    </section>
  )
}

function RuleNode({
  node,
  depth,
  selectedId,
  busyId,
  onSelect,
  onSync,
  onDelete,
}: {
  node: TreeNode
  depth: number
  selectedId: number | null
  busyId: number | null
  onSelect: (node: TreeNode) => void
  onSync: (node: TreeNode) => void
  onDelete: (node: TreeNode) => void
}) {
  const total = node.total ?? { discovered: node.discovered, done: node.done }
  const busy = busyId === node.id
  const selected = selectedId === node.id

  return (
    <div style={{ marginLeft: depth * 14 }} className="space-y-1.5">
      <Focusable focusKey={`NODE-${node.id}`} onEnter={() => onSelect(node)} scale="sm">
        <div
          className={[
            'rounded-2xl p-3 ring-1 transition',
            selected ? 'bg-emerald-400/10 ring-emerald-400/60' : 'bg-white/[0.04] ring-white/5',
          ].join(' ')}
        >
          <div className="flex items-center gap-2">
            <StatusDot status={node.status} />
            <span className="truncate text-sm font-semibold text-white">{node.title}</span>
            <span className="shrink-0 rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-zinc-400">
              {KIND_LABELS[node.kind] ?? node.kind}
            </span>
            {node.quality ? (
              <span className="shrink-0 rounded-full bg-emerald-400/15 px-2 py-0.5 text-[10px] text-emerald-300">
                {node.quality}
              </span>
            ) : null}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <div className="flex-1">
              <Bar done={total.done} discovered={total.discovered} />
            </div>
            <span className="shrink-0 text-[10px] tabular-nums text-zinc-500">
              {total.done}/{total.discovered}
            </span>
          </div>
          {node.lastError ? (
            <p className="mt-1 line-clamp-1 text-[10px] text-amber-300/80">{node.lastError}</p>
          ) : null}
        </div>
      </Focusable>

      <div className="flex items-center gap-2 pl-1">
        <Focusable scale="sm" onEnter={busy ? undefined : () => onSync(node)}>
          <span className="flex items-center gap-1 rounded-full bg-white/10 px-2.5 py-1 text-[10px] text-white">
            <RefreshCw className={`h-3 w-3 ${busy ? 'animate-spin' : ''}`} />
            同步
          </span>
        </Focusable>
        <Focusable scale="sm" onEnter={busy ? undefined : () => onDelete(node)}>
          <span className="flex items-center gap-1 rounded-full bg-white/5 px-2.5 py-1 text-[10px] text-zinc-400">
            <Trash2 className="h-3 w-3" />
            删除
          </span>
        </Focusable>
        {node.pending > 0 ? (
          <span className="text-[10px] text-amber-300/70">待下载 {node.pending}</span>
        ) : null}
      </div>

      {node.children.map((child) => (
        <RuleNode
          key={child.id}
          node={child}
          depth={depth + 1}
          selectedId={selectedId}
          busyId={busyId}
          onSelect={onSelect}
          onSync={onSync}
          onDelete={onDelete}
        />
      ))}
    </div>
  )
}

function BrowsePanel({
  node,
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
  node?: TreeNode
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
  if (!node) {
    return <section className="min-h-[320px]" />
  }

  return (
    <section className="min-h-[320px] space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 pb-3">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-white">{node.title}</h2>
          <span className="text-[11px] text-zinc-500">
            {KIND_LABELS[node.kind] ?? node.kind} · {items.length}
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
              focusKey={`BROWSE-${node.id}-${index}`}
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
  jobs,
  onRetry,
  onCancel,
}: {
  jobs: DownloadJob[]
  onRetry: (job: DownloadJob) => void
  onCancel: (job: DownloadJob) => void
}) {
  const [open, setOpen] = useState(false)
  if (jobs.length === 0) return null

  const active = jobs.filter(
    (job) => job.status === 'downloading' || job.status === 'queued',
  ).length
  const failed = jobs.filter((job) => job.status === 'error').length
  const done = jobs.filter((job) => job.status === 'done').length

  return (
    <section className="rounded-3xl bg-white/[0.04] ring-1 ring-white/10">
      <Focusable scale="sm" onEnter={() => setOpen((value) => !value)}>
        <div className="flex items-center justify-between px-5 py-3">
          <span className="flex items-center gap-2 text-sm font-semibold text-white">
            {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            下载任务
            <span className="rounded-full bg-white/10 px-2 py-0.5 text-[10px] font-normal text-zinc-300">
              进行中 {active} · 已完成 {done} · 失败 {failed}
            </span>
          </span>
        </div>
      </Focusable>

      {open ? (
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
                <Focusable scale="sm" onEnter={() => onRetry(job)}>
                  <span className="rounded-full bg-white/10 px-3 py-1 text-[11px] text-white">
                    重试
                  </span>
                </Focusable>
              ) : job.status === 'queued' || job.status === 'downloading' ? (
                <Focusable scale="sm" onEnter={() => onCancel(job)}>
                  <span className="rounded-full bg-white/5 px-3 py-1 text-[11px] text-zinc-400">
                    取消
                  </span>
                </Focusable>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  )
}

function EpisodePicker({
  detail,
  jobsByKey,
  libraryKeys,
  onClose,
  onDownload,
  onDownloadAll,
}: {
  detail: VideoDetail
  jobsByKey: Map<string, DownloadJob>
  libraryKeys: Set<string>
  onClose: () => void
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
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm md:p-8"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-3xl bg-zinc-950 ring-1 ring-white/15"
        onClick={(event) => event.stopPropagation()}
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
      </div>
    </div>
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

function CreateForm({
  groups,
  onCreated,
}: {
  groups: TreeGroup[]
  onCreated: () => void | Promise<void>
}) {
  const [source, setSource] = useState(groups[0]?.source ?? '')
  const [kind, setKind] = useState(groups[0]?.kinds[0] ?? 'query')
  const [value, setValue] = useState('')
  const [intervalSec, setIntervalSec] = useState(3600)
  const [quality, setQuality] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()

  const group = useMemo(() => groups.find((item) => item.source === source), [groups, source])
  const kinds = group?.kinds ?? []
  const needsValue = !EXPAND_KINDS.has(kind)
  const categoryOptions = kind === 'partition' ? (group?.categories ?? []) : []

  const pickSource = (next: string) => {
    const target = groups.find((item) => item.source === next)
    setSource(next)
    setKind(target?.kinds[0] ?? 'query')
    setValue('')
    setQuality('')
  }

  const submit = async () => {
    if (needsValue && !value.trim()) {
      setError('缺少线索值')
      return
    }
    setBusy(true)
    setError(undefined)
    try {
      await api.createNode({ source, kind, value, intervalSec, quality: quality || undefined })
      setValue('')
      await onCreated()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-3xl bg-white/[0.06] p-5 ring-1 ring-white/10">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-white">
        <Plus className="h-4 w-4" />
        新建线索
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
        <Field label="来源">
          <select
            value={source}
            onChange={(e) => pickSource(e.target.value)}
            className={inputClass}
          >
            {groups.map((item) => (
              <option key={item.source} value={item.source}>
                {item.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="类型">
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value)
              setValue('')
            }}
            className={inputClass}
          >
            {kinds.map((k) => (
              <option key={k} value={k}>
                {KIND_LABELS[k] ?? k}
              </option>
            ))}
          </select>
        </Field>

        {needsValue ? (
          <Field
            label={
              kind === 'partition' ? '分区' : kind === 'uploader' ? '发布者 ID / 名称' : '关键词'
            }
          >
            {categoryOptions.length > 0 ? (
              <select
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className={inputClass}
              >
                <option value=""></option>
                {categoryOptions.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                className={inputClass}
              />
            )}
          </Field>
        ) : null}

        <Field label="巡检间隔">
          <select
            value={intervalSec}
            onChange={(e) => setIntervalSec(Number(e.target.value))}
            className={inputClass}
          >
            {INTERVALS.map((interval) => (
              <option key={interval.value} value={interval.value}>
                {interval.label}
              </option>
            ))}
          </select>
        </Field>

        <Field label="清晰度">
          <select
            value={quality}
            onChange={(e) => setQuality(e.target.value)}
            className={inputClass}
          >
            <option value="">跟随默认</option>
            {(group?.qualityOptions ?? []).map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="mt-4 flex items-center justify-between">
        <span className="text-xs text-amber-300">{error ?? ''}</span>
        <Focusable scale="sm" onEnter={busy ? undefined : submit}>
          <span className="flex items-center gap-2 rounded-full bg-white px-5 py-2 text-xs font-semibold text-black">
            {busy ? (
              <RefreshCw className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Plus className="h-3.5 w-3.5" />
            )}
            创建线索
          </span>
        </Focusable>
      </div>
    </div>
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
    status === 'ok' ? 'bg-emerald-400' : status === 'error' ? 'bg-amber-400' : 'bg-zinc-500'
  return <span className={`h-2 w-2 shrink-0 rounded-full ${color}`} />
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium text-zinc-400">{label}</span>
      {children}
    </label>
  )
}

function findNode(tree: TreeResponse | undefined, id: number | null): TreeNode | undefined {
  if (!tree || id === null) return undefined
  const walk = (nodes: TreeNode[]): TreeNode | undefined => {
    for (const node of nodes) {
      if (node.id === id) return node
      const found = walk(node.children)
      if (found) return found
    }
    return undefined
  }
  for (const group of tree.sources) {
    const found = walk(group.nodes)
    if (found) return found
  }
  return undefined
}

const inputClass =
  'w-full rounded-xl bg-black/40 px-3 py-2.5 text-sm text-white outline-none ring-1 ring-white/10 transition focus:ring-white/40'
