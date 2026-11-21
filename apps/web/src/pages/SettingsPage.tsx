import { useCallback, useEffect, useState } from 'react'
import { Check, Loader2, PlugZap, QrCode as QrIcon, RefreshCw } from 'lucide-react'
import type { QrCode, SourceConfig, SourceConfigField } from '@/api/client'
import { api } from '@/api/client'
import { Focusable } from '@/components/Focusable'

type Status = { kind: 'ok' | 'err' | 'info'; text: string }

/**
 * 设置：源站凭据与网络参数。
 * 敏感字段只显示掩码；B 站支持扫码登录，凭据由服务端直接落库，不经过前端。
 */
export function SettingsPage() {
  const [configs, setConfigs] = useState<SourceConfig[]>([])
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({})
  const [status, setStatus] = useState<Record<string, Status>>({})
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [error, setError] = useState<string>()

  const load = useCallback(async () => {
    try {
      setConfigs(await api.settingsSources())
      setError(undefined)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const setDraft = (source: string, key: string, value: string) =>
    setDrafts((d) => ({ ...d, [source]: { ...d[source], [key]: value } }))

  const mark = (source: string, s: Status) => setStatus((cur) => ({ ...cur, [source]: s }))
  const setRunning = (source: string, running: boolean) =>
    setBusy((cur) => ({ ...cur, [source]: running }))

  const save = async (cfg: SourceConfig, overrides?: Record<string, string>) => {
    const payload: Record<string, string> = {}
    for (const f of cfg.fields) {
      const drafted = drafts[cfg.source]?.[f.key]
      if (f.type === 'secret') {
        if (drafted) payload[f.key] = drafted
      } else {
        payload[f.key] = drafted ?? (f.value === undefined ? '' : String(f.value))
      }
    }
    Object.assign(payload, overrides ?? {})

    setRunning(cfg.source, true)
    try {
      await api.saveSourceSettings(cfg.source, payload)
      setDrafts((d) => ({ ...d, [cfg.source]: {} }))
      await load()
      mark(cfg.source, { kind: 'ok', text: '已保存并生效' })
    } catch (e) {
      mark(cfg.source, { kind: 'err', text: (e as Error).message })
    } finally {
      setRunning(cfg.source, false)
    }
  }

  const test = async (cfg: SourceConfig) => {
    setRunning(cfg.source, true)
    mark(cfg.source, { kind: 'info', text: '测试中' })
    try {
      const r = await api.testSource(cfg.source)
      mark(cfg.source, { kind: r.ok ? 'ok' : 'err', text: `${r.message}（${r.latencyMs}ms）` })
    } catch (e) {
      mark(cfg.source, { kind: 'err', text: (e as Error).message })
    } finally {
      setRunning(cfg.source, false)
    }
  }

  return (
    <div className="space-y-10 px-12 pb-24 pt-28">
      <h1 className="text-2xl font-bold text-white">设置</h1>

      {error ? <p className="text-sm text-amber-300">{error}</p> : null}

      <div className="grid gap-6 xl:grid-cols-2">
        {configs.map((cfg) => (
          <SourceCard
            key={cfg.source}
            cfg={cfg}
            draft={drafts[cfg.source] ?? {}}
            status={status[cfg.source]}
            busy={Boolean(busy[cfg.source])}
            onChange={(key, value) => setDraft(cfg.source, key, value)}
            onSave={() => void save(cfg)}
            onClear={(key) => void save(cfg, { [key]: '' })}
            onTest={() => void test(cfg)}
            onQrDone={load}
          />
        ))}
        {configs.length === 0 && !error ? (
          <>
            <div className="h-72 animate-pulse rounded-3xl bg-white/[0.06]" />
            <div className="h-72 animate-pulse rounded-3xl bg-white/[0.06]" />
          </>
        ) : null}
      </div>
    </div>
  )
}

function SourceCard({
  cfg,
  draft,
  status,
  busy,
  onChange,
  onSave,
  onClear,
  onTest,
  onQrDone,
}: {
  cfg: SourceConfig
  draft: Record<string, string>
  status?: Status
  busy: boolean
  onChange: (key: string, value: string) => void
  onSave: () => void
  onClear: (key: string) => void
  onTest: () => void
  onQrDone: () => void | Promise<void>
}) {
  const [qrOpen, setQrOpen] = useState(false)

  return (
    <section className="rounded-3xl bg-white/[0.06] p-6 ring-1 ring-white/10">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-white">{cfg.name}</h2>
          <span className="rounded-full bg-white/10 px-2 py-0.5 text-[11px] text-zinc-400">
            {cfg.source}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {cfg.canQrcode ? (
            <Focusable scale="sm" onEnter={() => setQrOpen((v) => !v)}>
              <span className="flex items-center gap-2 rounded-full bg-white/10 px-4 py-2 text-xs font-semibold text-white">
                <QrIcon className="h-3.5 w-3.5" />
                扫码登录
              </span>
            </Focusable>
          ) : null}
          <Focusable scale="sm" onEnter={busy ? undefined : onTest}>
            <span className="flex items-center gap-2 rounded-full bg-white/10 px-4 py-2 text-xs font-semibold text-white">
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <PlugZap className="h-3.5 w-3.5" />
              )}
              测试连接
            </span>
          </Focusable>
          <Focusable scale="sm" onEnter={busy ? undefined : onSave}>
            <span className="flex items-center gap-2 rounded-full bg-white px-4 py-2 text-xs font-semibold text-black">
              <Check className="h-3.5 w-3.5" />
              保存
            </span>
          </Focusable>
        </div>
      </header>

      {qrOpen && cfg.canQrcode ? <QrLogin source={cfg.source} onDone={onQrDone} /> : null}

      <div className="space-y-4">
        {cfg.fields.map((field) => (
          <FieldRow
            key={field.key}
            field={field}
            drafted={draft[field.key]}
            onChange={(v) => onChange(field.key, v)}
            onClear={() => onClear(field.key)}
          />
        ))}
      </div>

      {status ? (
        <p
          className={[
            'mt-4 text-xs',
            status.kind === 'ok'
              ? 'text-emerald-300'
              : status.kind === 'err'
                ? 'text-amber-300'
                : 'text-zinc-400',
          ].join(' ')}
        >
          {status.text}
        </p>
      ) : null}
    </section>
  )
}

function QrLogin({ source, onDone }: { source: string; onDone: () => void | Promise<void> }) {
  const [qr, setQr] = useState<QrCode>()
  const [phase, setPhase] = useState<'loading' | 'wait' | 'scanned' | 'done' | 'expired' | 'error'>(
    'loading',
  )
  const [message, setMessage] = useState('')

  const start = useCallback(async () => {
    setPhase('loading')
    setMessage('')
    try {
      setQr(await api.qrcode(source))
      setPhase('wait')
    } catch (e) {
      setPhase('error')
      setMessage((e as Error).message)
    }
  }, [source])

  useEffect(() => {
    void start()
  }, [start])

  useEffect(() => {
    if (!qr || (phase !== 'wait' && phase !== 'scanned')) return
    let alive = true
    const timer = setInterval(async () => {
      try {
        const r = await api.pollQrcode(source, qr.key)
        if (!alive) return
        if (r.status === 0) {
          setPhase('done')
          setMessage('登录成功')
          void onDone()
        } else if (r.status === 86090) {
          setPhase('scanned')
          setMessage('已扫码')
        } else if (r.status === 86038) {
          setPhase('expired')
          setMessage('二维码已过期')
        } else if (r.status === 86101) {
          setMessage('')
        } else {
          setMessage(r.message || '')
        }
      } catch {
        /* 轮询失败继续重试 */
      }
    }, 2000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [qr, phase, source, onDone])

  return (
    <div className="mb-5 flex items-center gap-5 rounded-2xl bg-black/30 p-4">
      {qr && phase !== 'expired' && phase !== 'error' ? (
        <img src={qr.image} alt="登录二维码" className="h-40 w-40 rounded-xl bg-white p-1" />
      ) : (
        <div className="flex h-40 w-40 items-center justify-center rounded-xl bg-white/5 text-xs text-zinc-500">
          {phase === 'loading' ? '生成中' : phase === 'expired' ? '已过期' : '不可用'}
        </div>
      )}
      <div className="space-y-2">
        <p className="text-sm font-medium text-white">{message}</p>
        {phase === 'expired' || phase === 'error' ? (
          <Focusable scale="sm" onEnter={() => void start()}>
            <span className="inline-flex items-center gap-2 rounded-full bg-white/10 px-3 py-1.5 text-[11px] font-medium text-white">
              <RefreshCw className="h-3 w-3" />
              刷新二维码
            </span>
          </Focusable>
        ) : null}
      </div>
    </div>
  )
}

function FieldRow({
  field,
  drafted,
  onChange,
  onClear,
}: {
  field: SourceConfigField
  drafted?: string
  onChange: (value: string) => void
  onClear: () => void
}) {
  const isSecret = field.type === 'secret'
  const value = isSecret
    ? (drafted ?? '')
    : (drafted ?? (field.value === undefined ? '' : String(field.value)))

  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between text-xs font-medium text-zinc-400">
        <span>{field.label}</span>
        {isSecret ? (
          field.configured ? (
            <button
              type="button"
              onClick={onClear}
              className="text-[11px] text-emerald-300/90 hover:text-emerald-200"
            >
              已配置 {field.hint} · 清除
            </button>
          ) : (
            <span className="text-[11px] text-zinc-500">未配置</span>
          )
        ) : null}
      </span>

      {field.type === 'select' ? (
        <select value={value} onChange={(e) => onChange(e.target.value)} className={inputClass}>
          {(field.options ?? []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          type={isSecret ? 'password' : field.type === 'number' ? 'number' : 'text'}
          autoComplete="off"
          spellCheck={false}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={inputClass}
        />
      )}
    </label>
  )
}

const inputClass =
  'w-full rounded-xl bg-black/40 px-4 py-2.5 text-sm text-white placeholder:text-zinc-600 outline-none ring-1 ring-white/10 transition focus:ring-white/40'
