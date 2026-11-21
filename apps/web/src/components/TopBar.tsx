import { useEffect, useState } from 'react'
import { Settings } from 'lucide-react'
import { api } from '@/api/client'
import { Focusable } from './Focusable'

export interface TabItem {
  key: string
  label: string
}

/**
 * tvOS 顶部栏：左侧导航，右侧「状态 · 时间 · 设置」。
 * 状态与下载数保持静默轮询，失败不影响界面。
 */
export function TopBar({
  tabs,
  active,
  settingsActive,
  onSelect,
  onSettings,
}: {
  tabs: TabItem[]
  active: string
  settingsActive: boolean
  onSelect: (key: string) => void
  onSettings: () => void
}) {
  const [clock, setClock] = useState(formatClock())
  const [offline, setOffline] = useState(false)
  const [activeDownloads, setActiveDownloads] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setClock(formatClock()), 10_000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const [mode, jobs] = await Promise.all([api.mode(), api.downloads()])
        if (!alive) return
        setOffline(mode.offline)
        setActiveDownloads(
          jobs.filter((job) => job.status === 'downloading' || job.status === 'queued').length,
        )
      } catch {
        /* 离线/服务异常时保持静默 */
      }
    }
    void tick()
    const id = setInterval(tick, 4000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  return (
    <header className="fixed inset-x-0 top-0 z-40 bg-gradient-to-b from-black/85 via-black/45 to-transparent pb-10">
      <div className="flex items-center justify-between px-12 py-5">
        <nav className="flex items-center gap-7">
          <span className="mr-2 text-[11px] font-semibold tracking-[0.32em] text-white/35">
            FORAGER
          </span>
          {tabs.map((tab) => (
            <Tab
              key={tab.key}
              tab={tab}
              active={active === tab.key}
              onSelect={() => onSelect(tab.key)}
            />
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <span className="flex items-center gap-2 rounded-full bg-white/5 px-3 py-1.5 text-[11px] text-white/55 backdrop-blur">
            <span
              className={`h-1.5 w-1.5 rounded-full ${offline ? 'bg-amber-400' : 'bg-emerald-400'}`}
            />
            {offline ? '离线' : '在线'}
            {activeDownloads > 0 ? <span className="text-white/80">↓ {activeDownloads}</span> : null}
          </span>

          <span className="rounded-full bg-white/10 px-4 py-1.5 text-sm font-medium tabular-nums text-white/85 backdrop-blur">
            {clock}
          </span>

          <Focusable focusKey="TOP-SETTINGS" scale="sm" onEnter={onSettings}>
            <span
              className={[
                'flex h-9 w-9 items-center justify-center rounded-full text-white/75 backdrop-blur transition',
                settingsActive ? 'bg-white text-black' : 'bg-white/10',
              ].join(' ')}
            >
              <Settings className="h-4 w-4" />
            </span>
          </Focusable>
        </div>
      </div>
    </header>
  )
}

function Tab({ tab, active, onSelect }: { tab: TabItem; active: boolean; onSelect: () => void }) {
  return (
    <Focusable focusKey={`TAB-${tab.key}`} scale="sm" onEnter={onSelect}>
      <span
        className={[
          'block rounded-full px-1 pb-0.5 text-lg font-semibold transition-all duration-200',
          active ? 'text-white' : 'text-zinc-500',
        ].join(' ')}
      >
        {tab.label}
      </span>
    </Focusable>
  )
}

function formatClock(): string {
  return new Date().toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}
