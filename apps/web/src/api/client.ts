import type {
  ContinueItem,
  DownloadJob,
  DownloadStatus,
  LibraryItem,
  Page,
  PlaybackResolve,
  VideoDetail,
  VideoSummary,
} from '@libre-tv/shared/types'

/** 会话失效时广播：App 收到后切回登录页 */
export const UNAUTHORIZED_EVENT = 'forager:unauthorized'

function onUnauthorized(status: number): void {
  if (status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: 'same-origin' })
  if (!res.ok) {
    onUnauthorized(res.status)
    const body = (await res.json().catch(() => ({}))) as { error?: string }
    throw new ApiError(body.error ?? `请求失败（${res.status}）`, res.status)
  }
  return (await res.json()) as T
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'same-origin',
  })
  if (!res.ok) {
    onUnauthorized(res.status)
    const data = (await res.json().catch(() => ({}))) as { error?: string }
    throw new ApiError(data.error ?? `请求失败（${res.status}）`, res.status)
  }
  return (await res.json()) as T
}

export interface SourceConfigField {
  key: string
  label: string
  type: 'text' | 'secret' | 'number' | 'select'
  placeholder?: string
  help?: string
  /** 非敏感字段的当前值 */
  value?: string | number
  /** 敏感字段是否已配置 */
  configured?: boolean
  /** 敏感字段的掩码，如 ****abcd */
  hint?: string
  /** select 类型的候选项 */
  options?: Array<{ value: string; label: string }>
}

export interface SourceConfig {
  source: string
  name: string
  canQrcode?: boolean
  fields: SourceConfigField[]
}

export interface TestResult {
  ok: boolean
  latencyMs: number
  message: string
}

/** 触发时机：manual 只认「立即运行」，其余按距上次运行的间隔判定 */
export type Trigger = 'manual' | '1h' | '1d' | '1w' | '1m'

export interface ClueStats {
  discovered: number
  done: number
  pending: number
  /** 补投次数耗尽、仍未入库 */
  failed: number
}

/** 线索：一条发现规则（来源 + 分区 / 关键词 / 画师 + 数量） */
export interface Clue {
  id: number
  fetcherId: number
  /** 由「我的收藏 / 我的关注」展开派生，不单独可删 */
  derived: boolean
  source: string
  kind: string
  partition: string
  keyword: string
  uploader: string
  /** 每轮取前 N 页；0 = 全部 */
  pages: number
  title: string
  lastRunAt: string | null
  status: string
  lastError: string | null
  stats: ClueStats
}

/** 采集器：若干线索的并集 + 触发时机 */
export interface Fetcher {
  id: number
  name: string
  trigger: Trigger
  quality: string
  enabled: boolean
  lastRunAt: string | null
  status: string
  lastError: string | null
  stats: ClueStats
  clues: Clue[]
}

/** 可建线索的源：声明自己露出哪些筛选项与分区 */
export interface ClueSource {
  id: string
  name: string
  kinds: string[]
  fields: string[]
  categories: Array<{ id: string; name: string }>
  qualityOptions: string[]
}

export interface FetchersResponse {
  fetchers: Fetcher[]
  total: ClueStats
  triggers: Array<{ value: Trigger; label: string }>
  pageOptions: Array<{ value: number; label: string }>
  sources: ClueSource[]
}

export interface DownloadsSnapshot {
  /** 进行中 + 最近结束的若干条（服务端已裁剪） */
  jobs: DownloadJob[]
  counts: Record<DownloadStatus, number>
}

export interface Facets {
  sources: Array<{ source: string; count: number }>
  categories: Array<{ source: string; category: string; count: number }>
  total: number
}

export interface Wallpaper {
  /** 文件名（不含扩展名） */
  slug: string
  /** 同源地址：图片已在服务端本地库里 */
  url: string
  /** 0 = 服务端还没下载好 */
  weight: number
}

export interface QrCode {
  key: string
  url: string
  image: string
}

export interface QrPoll {
  status: number
  message: string
  saved?: boolean
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

export interface SessionStatus {
  authenticated: boolean
  username: string | null
}

export const api = {
  // ---- 会话 ----
  sessionStatus: () => get<SessionStatus>('/api/session/status'),
  login: (username: string, password: string) =>
    send<SessionStatus>('/api/session/login', 'POST', { username, password }),
  logout: () => send<{ ok: boolean }>('/api/session/logout', 'POST'),
  changePassword: (current: string, next: string) =>
    send<{ ok: boolean }>('/api/session/change-password', 'POST', { current, next }),
  recover: (recoveryCode: string, nextPassword: string) =>
    send<{ ok: boolean }>('/api/session/recover', 'POST', { recoveryCode, nextPassword }),

  listSources: () => get<Array<{ id: string; name: string }>>('/api/sources'),

  /** 首页背景：服务端首次运行时从 Unsplash 拉进本地库，这里只取就绪清单 */
  wallpapers: () => get<Wallpaper[]>('/api/wallpapers/index'),

  sourceCategories: (source: string) =>
    get<Array<{ id: string; name: string }>>(`/api/sources/${source}/categories`),

  list: (source: string, page = 1, category?: string) =>
    get<Page<VideoSummary>>(
      `/api/sources/${source}/list?page=${page}${category ? `&category=${encodeURIComponent(category)}` : ''}`,
    ),

  search: (source: string, kw: string, page = 1) =>
    get<Page<VideoSummary>>(
      `/api/sources/${source}/search?kw=${encodeURIComponent(kw)}&page=${page}`,
    ),

  detail: (source: string, id: string) => get<VideoDetail>(`/api/sources/${source}/detail/${id}`),

  /** 图片统一经服务端代理（源站防盗链 + 签名） */
  image: (source: string, url?: string) =>
    url ? `/api/sources/${source}/image?url=${encodeURIComponent(url)}` : undefined,

  // ---- 本地库 / 双播 ----
  library: () => get<LibraryItem[]>('/api/library/items'),
  libraryKeys: () => get<string[]>('/api/library/keys'),
  continueWatching: (limit = 12) => get<ContinueItem[]>(`/api/library/continue?limit=${limit}`),
  mode: () => get<{ offline: boolean }>('/api/library/mode'),
  setMode: (offline: boolean) =>
    send<{ offline: boolean }>('/api/library/mode', 'PUT', { offline }),
  play: (source: string, id: string, quality?: string) =>
    get<PlaybackResolve>(
      `/api/library/play/${source}/${id}${quality ? `?quality=${encodeURIComponent(quality)}` : ''}`,
    ),
  download: (source: string, id: string, quality?: string) =>
    send<DownloadJob>('/api/library/downloads', 'POST', { source, id, quality }),
  /** keys 用来精确查询当前浏览页那几张卡的任务（列表默认被裁剪） */
  downloads: (params: { limit?: number; keys?: string[] } = {}) => {
    const qs = new URLSearchParams()
    if (params.limit) qs.set('limit', String(params.limit))
    if (params.keys?.length) qs.set('keys', params.keys.join(','))
    const suffix = qs.toString()
    return get<DownloadsSnapshot>(`/api/library/downloads${suffix ? `?${suffix}` : ''}`)
  },
  cancelDownload: (key: string) => send<{ ok: true }>(`/api/library/downloads/${key}`, 'DELETE'),
  deleteItem: (key: string) => send<{ ok: true }>(`/api/library/items/${key}`, 'DELETE'),

  // ---- 进度 ----
  progress: (source: string, id: string) =>
    get<{ positionMs: number; durationMs: number | null } | null>(
      `/api/library/progress/${source}/${id}`,
    ),
  // ---- 库（分面筛选） ----
  libraryItems: (filters: { source?: string; category?: string; q?: string } = {}) => {
    const qs = new URLSearchParams()
    if (filters.source) qs.set('source', filters.source)
    if (filters.category) qs.set('category', filters.category)
    if (filters.q) qs.set('q', filters.q)
    const suffix = qs.toString()
    return get<LibraryItem[]>(`/api/library/items${suffix ? `?${suffix}` : ''}`)
  },
  facets: () => get<Facets>('/api/library/facets'),

  // ---- 线索：采集器 = 线索并集 + 触发时机 ----
  fetchers: () => get<FetchersResponse>('/api/fetchers'),
  createFetcher: (body: { name: string; trigger: Trigger; quality?: string }) =>
    send<Fetcher>('/api/fetchers', 'POST', body),
  deleteFetcher: (id: number) => send<{ ok: true }>(`/api/fetchers/${id}`, 'DELETE'),
  /** 立即运行：立刻返回，采集器在后台跑 */
  runFetcher: (id: number) =>
    send<{ ok: true; started: boolean }>(`/api/fetchers/${id}/run`, 'POST'),
  createClue: (body: {
    fetcherId: number
    source: string
    kind?: string
    partition?: string
    keyword?: string
    uploader?: string
    pages: number
  }) => send<Clue>('/api/clues', 'POST', body),
  deleteClue: (id: number) => send<{ ok: true }>(`/api/clues/${id}`, 'DELETE'),
  /** 只浏览该线索对应的在线内容，不记录、不下载 */
  clueItems: (id: number, page = 1) =>
    get<Page<VideoSummary>>(`/api/clues/${id}/items?page=${page}`),

  // ---- 上游扫码登录 ----
  qrcode: (source: string) => get<QrCode>(`/api/auth/${source}/qrcode`),
  // 轮询必须绕开一切缓存：加时间戳 + no-store
  pollQrcode: async (source: string, key: string): Promise<QrPoll> => {
    const url = `/api/auth/${source}/qrcode/poll?key=${encodeURIComponent(key)}&t=${Date.now()}`
    const res = await fetch(url, { cache: 'no-store' })
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { error?: string }
      throw new ApiError(body.error ?? `请求失败（${res.status}）`, res.status)
    }
    return (await res.json()) as QrPoll
  },

  // ---- 设置 ----
  settingsSources: () => get<SourceConfig[]>('/api/settings/sources'),
  saveSourceSettings: (source: string, values: Record<string, string>) =>
    send<{ ok: boolean; config?: SourceConfig }>(`/api/settings/sources/${source}`, 'PUT', values),
  testSource: (source: string) => send<TestResult>(`/api/settings/sources/${source}/test`, 'POST'),

  saveProgress: (source: string, id: string, positionMs: number, durationMs: number) =>
    send<{ ok: true }>(`/api/library/progress/${source}/${id}`, 'PUT', {
      positionMs,
      durationMs,
    }),
}

/** 把秒格式化为 1:02:03 */
export function fmtDuration(sec?: number): string {
  if (!sec || sec <= 0) return ''
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = Math.floor(sec % 60)
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`
}

export function fmtViews(n?: number): string {
  if (!n) return ''
  if (n >= 10_000) return `${(n / 10_000).toFixed(1)}萬次`
  return `${n}次`
}

export function fmtBytes(n?: number): string {
  if (!n) return ''
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(2)} GB`
  return `${(n / 1048576).toFixed(1)} MB`
}
