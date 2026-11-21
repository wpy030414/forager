import type { DownloadJob, VideoSummary } from '@libre-tv/shared/types'
import { useState } from 'react'
import { api, fmtBytes, fmtDuration, fmtViews } from '@/api/client'
import { Focusable } from './Focusable'

/** tvOS 海报卡片：竖版 2:3 海报 + 底部渐变标题 */
export function PosterCard({
  video,
  onSelect,
  focusKey,
  fluid = false,
  job,
  inLibrary = false,
}: {
  video: VideoSummary
  onSelect: () => void
  focusKey?: string
  /** 网格模式：占满父容器宽度 */
  fluid?: boolean
  /** 该条目的下载任务：卡片上显示独立进度 */
  job?: DownloadJob
  /** 本地库已有该最小单元 */
  inLibrary?: boolean
}) {
  const [failed, setFailed] = useState(false)
  const cover = api.image(video.source, video.coverUrl)

  return (
    <div className={fluid ? 'w-full' : 'w-52 shrink-0 md:w-56'}>
      <Focusable focusKey={focusKey} onEnter={onSelect} className="w-full">
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
              {video.title}
            </div>
          )}

          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-2/5 bg-gradient-to-t from-black/90 via-black/40 to-transparent" />

          {video.kind === 'series' ? (
            <span className="absolute left-2.5 top-2.5 rounded-md bg-emerald-400/90 px-2 py-0.5 text-[11px] font-semibold text-black">
              系列
            </span>
          ) : null}

          {video.durationSec ? (
            <span className="absolute right-2.5 top-2.5 rounded-md bg-black/70 px-2 py-0.5 text-xs font-medium tabular-nums text-white/90 backdrop-blur">
              {fmtDuration(video.durationSec)}
            </span>
          ) : null}

          <div className="absolute inset-x-0 bottom-0 p-4">
            <p className="line-clamp-2 text-[15px] font-semibold leading-snug text-white drop-shadow">
              {video.title}
            </p>
            {video.note ? (
              <p className="mt-1 line-clamp-1 text-[11px] text-zinc-300/90">{video.note}</p>
            ) : null}
          </div>

          {job ? <JobOverlay job={job} /> : null}
        </div>
      </Focusable>

      <div className="mt-2 flex items-center gap-2 px-1 text-xs text-zinc-400">
        <span className="truncate">{video.uploader ?? ''}</span>
        {video.views ? <span className="shrink-0">· {fmtViews(video.views)}</span> : null}
        {inLibrary ? <span className="shrink-0 text-emerald-300">· 已入库</span> : null}
      </div>
    </div>
  )
}

/** 卡片底部的独立下载进度：每个最小单元一条任务 */
function JobOverlay({ job }: { job: DownloadJob }) {
  const text =
    job.status === 'downloading'
      ? `${job.progress}% · ${fmtBytes(job.receivedBytes)}`
      : job.status === 'queued'
        ? '排队中'
        : job.status === 'done'
          ? job.fromLibrary
            ? '已入库'
            : '已完成'
          : '下载失败'
  return (
    <div className="absolute inset-x-0 bottom-0 z-10 bg-black/80 px-3 py-2 backdrop-blur">
      <div className="mb-1 flex items-center justify-between text-[10px] text-white/85">
        <span>{text}</span>
        <span className="text-white/50">{job.quality}</span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-white/15">
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
    </div>
  )
}

/** 横向海报行：tvOS 首页经典 shelf，标题 + 可左右滚动的卡片 */
export function PosterRow({
  title,
  videos,
  onSelect,
  focusKey,
}: {
  title: string
  videos: VideoSummary[]
  onSelect: (v: VideoSummary) => void
  focusKey?: string
}) {
  if (videos.length === 0) return null
  return (
    <section className="space-y-3" data-shelf={focusKey}>
      <h2 className="px-12 text-lg font-semibold tracking-tight text-zinc-300">{title}</h2>
      <div className="flex gap-4 overflow-x-auto px-12 pb-6 pt-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {videos.map((v, i) => (
          <PosterCard
            key={`${v.source}:${v.id}`}
            video={v}
            onSelect={() => onSelect(v)}
            focusKey={focusKey ? `${focusKey}-${i}` : undefined}
          />
        ))}
      </div>
    </section>
  )
}
