import { useCallback, useEffect, useMemo, useState } from 'react'
import { Plus, RefreshCw, Trash2 } from 'lucide-react'
import { api } from '@/api/client'
import type { TreeGroup, TreeNode, TreeResponse } from '@/api/client'
import { Focusable } from '@/components/Focusable'

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

/**
 * 线索树：每条线索 = 一条持续发现规则。
 * 收藏/关注类线索会在同步时自动展开成「发布者」子线索。
 */
export function TreePage() {
  const [tree, setTree] = useState<TreeResponse>()
  const [error, setError] = useState<string>()
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      setTree(await api.tree())
      setError(undefined)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    void load()
    const id = setInterval(() => void load(), 5000)
    return () => clearInterval(id)
  }, [load])

  const sync = async (node: TreeNode) => {
    setBusyId(node.id)
    try {
      await api.syncNode(node.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
      void load()
    }
  }

  const remove = async (node: TreeNode) => {
    setBusyId(node.id)
    try {
      await api.deleteNode(node.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
      void load()
    }
  }

  const total = tree?.total ?? { discovered: 0, done: 0 }

  return (
    <div className="space-y-8 px-12 pb-24 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white">线索树</h1>
          <p className="mt-1 text-sm text-zinc-400">
            按规则持续发现新内容 · 总进度 {total.done}/{total.discovered}
          </p>
        </div>
        <div className="w-64">
          <Bar done={total.done} discovered={total.discovered} />
        </div>
      </div>

      {error ? <p className="text-sm text-amber-300">{error}</p> : null}

      {tree ? <CreateForm groups={tree.sources} onCreated={load} /> : null}

      <div className="space-y-10">
        {(tree?.sources ?? []).map((group) => (
          <section key={group.source} className="space-y-4">
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 pb-3">
              <div className="flex items-center gap-3">
                <h2 className="text-lg font-semibold text-white">{group.name}</h2>
                <span className="rounded-full bg-white/10 px-2 py-0.5 text-[11px] text-zinc-400">
                  {group.nodes.length} 条线索
                </span>
              </div>
              <div className="flex items-center gap-3 text-xs text-zinc-400">
                <span>
                  {group.total.done}/{group.total.discovered}
                </span>
                <div className="w-40">
                  <Bar done={group.total.done} discovered={group.total.discovered} />
                </div>
              </div>
            </header>

            {group.nodes.length === 0 ? (
              <p className="rounded-2xl border border-dashed border-white/10 px-5 py-8 text-center text-xs text-zinc-500">
                还没有线索。在上面选「{group.name}」，挑一种类型开始。
              </p>
            ) : (
              <div className="space-y-3">
                {group.nodes.map((node) => (
                  <NodeRow
                    key={node.id}
                    node={node}
                    depth={0}
                    busyId={busyId}
                    onSync={sync}
                    onDelete={remove}
                  />
                ))}
              </div>
            )}
          </section>
        ))}
      </div>
    </div>
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

  const group = useMemo(() => groups.find((g) => g.source === source), [groups, source])
  const kinds = group?.kinds ?? []
  const needsValue = !EXPAND_KINDS.has(kind)
  const categoryOptions = kind === 'partition' ? (group?.categories ?? []) : []

  const pickSource = (next: string) => {
    const target = groups.find((g) => g.source === next)
    setSource(next)
    setKind(target?.kinds[0] ?? 'query')
    setValue('')
    setQuality('')
  }

  const submit = async () => {
    if (needsValue && !value.trim()) {
      setError('请填写线索值')
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
    <div className="rounded-3xl bg-white/[0.06] p-6 ring-1 ring-white/10">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-white">
        <Plus className="h-4 w-4" />
        新建线索
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <Field label="来源">
          <select
            value={source}
            onChange={(e) => pickSource(e.target.value)}
            className={inputClass}
          >
            {groups.map((g) => (
              <option key={g.source} value={g.source}>
                {g.name}
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
                <option value="">选择分区…</option>
                {categoryOptions.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={kind === 'uploader' ? '如 123456 或 UP 名' : '如 studio 名'}
                className={inputClass}
              />
            )}
          </Field>
        ) : (
          <Field label="说明">
            <p className="rounded-xl bg-black/20 px-4 py-2.5 text-xs text-zinc-400">
              同步时自动展开成发布者子线索
            </p>
          </Field>
        )}

        <Field label="巡检间隔">
          <select
            value={intervalSec}
            onChange={(e) => setIntervalSec(Number(e.target.value))}
            className={inputClass}
          >
            {INTERVALS.map((i) => (
              <option key={i.value} value={i.value}>
                {i.label}
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
            {(group?.qualityOptions ?? []).map((q) => (
              <option key={q} value={q}>
                {q}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="mt-4 flex items-center justify-between">
        <span className="text-xs text-amber-300">{error ?? ''}</span>
        <Focusable scale="sm" onEnter={busy ? undefined : submit}>
          <span className="flex items-center gap-2 rounded-full bg-white px-5 py-2 text-xs font-semibold text-black">
            <Plus className="h-3.5 w-3.5" />
            {busy ? '创建中…' : '创建线索'}
          </span>
        </Focusable>
      </div>
    </div>
  )
}

function NodeRow({
  node,
  depth,
  busyId,
  onSync,
  onDelete,
}: {
  node: TreeNode
  depth: number
  busyId: number | null
  onSync: (node: TreeNode) => void
  onDelete: (node: TreeNode) => void
}) {
  const total = node.total ?? { discovered: node.discovered, done: node.done }
  const busy = busyId === node.id

  return (
    <div style={{ marginLeft: depth * 24 }} className="space-y-2">
      <div className="rounded-2xl bg-white/[0.04] p-4 ring-1 ring-white/5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <StatusDot status={node.status} title={node.lastError ?? undefined} />
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

          <div className="flex items-center gap-2">
            <Focusable scale="sm" onEnter={busy ? undefined : () => onSync(node)}>
              <span className="flex items-center gap-1.5 rounded-full bg-white/10 px-3 py-1.5 text-[11px] font-medium text-white">
                <RefreshCw className={`h-3 w-3 ${busy ? 'animate-spin' : ''}`} />
                同步
              </span>
            </Focusable>
            <Focusable scale="sm" onEnter={busy ? undefined : () => onDelete(node)}>
              <span className="flex items-center gap-1.5 rounded-full bg-white/5 px-3 py-1.5 text-[11px] font-medium text-zinc-400">
                <Trash2 className="h-3 w-3" />
                删除
              </span>
            </Focusable>
          </div>
        </div>

        <div className="mt-3 flex items-center gap-3">
          <div className="flex-1">
            <Bar done={total.done} discovered={total.discovered} />
          </div>
          <span className="shrink-0 text-[11px] tabular-nums text-zinc-400">
            {total.done}/{total.discovered}
          </span>
        </div>

        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-zinc-500">
          <span>每 {Math.round(node.intervalSec / 60)} 分钟巡检</span>
          <span>
            最近：{node.lastRunAt ? node.lastRunAt.replace('T', ' ').slice(0, 16) : '未运行'}
          </span>
          {node.pending > 0 ? (
            <span className="text-amber-300/80">待下载 {node.pending}</span>
          ) : null}
          {node.lastError ? (
            <span className="text-amber-300/80">错误：{node.lastError}</span>
          ) : null}
        </div>
      </div>

      {node.children.map((child) => (
        <NodeRow
          key={child.id}
          node={child}
          depth={depth + 1}
          busyId={busyId}
          onSync={onSync}
          onDelete={onDelete}
        />
      ))}
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

function StatusDot({ status, title }: { status: string; title?: string }) {
  const color =
    status === 'ok' ? 'bg-emerald-400' : status === 'error' ? 'bg-amber-400' : 'bg-zinc-500'
  return <span title={title} className={`h-2 w-2 shrink-0 rounded-full ${color}`} />
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium text-zinc-400">{label}</span>
      {children}
    </label>
  )
}

const inputClass =
  'w-full rounded-xl bg-black/40 px-3 py-2.5 text-sm text-white outline-none ring-1 ring-white/10 transition focus:ring-white/40'
