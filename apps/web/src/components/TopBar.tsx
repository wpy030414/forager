import { useEffect, useState } from 'react'
import { ChevronLeft, Settings } from 'lucide-react'
import { Focusable } from './Focusable'

/**
 * tvOS 顶部无导航：只有右上角的时间与设置；二级页面左上角给一个返回键。
 * 首页不出现 LOGO、tab、在线状态等任何常驻 chrome。
 */
export function TopBar({
  showBack,
  settingsActive,
  onBack,
  onSettings,
}: {
  showBack: boolean
  settingsActive: boolean
  onBack: () => void
  onSettings: () => void
}) {
  const [clock, setClock] = useState(formatClock())

  useEffect(() => {
    const id = setInterval(() => setClock(formatClock()), 10_000)
    return () => clearInterval(id)
  }, [])

  return (
    <header className="fixed inset-x-0 top-0 z-40 flex items-center justify-between px-12 py-6">
      <div>
        {showBack ? (
          <Focusable focusKey="TOP-BACK" scale="sm" onEnter={onBack}>
            <span className="flex h-9 w-9 items-center justify-center rounded-full glass text-white/85 transition duration-[var(--dur-focus-out)] hover:bg-white/15">
              <ChevronLeft className="h-4 w-4" />
            </span>
          </Focusable>
        ) : null}
      </div>

      <div className="flex items-center gap-3">
        <span className="rounded-full glass px-4 py-1.5 text-sm font-medium tabular-nums text-white/85">
          {clock}
        </span>
        <Focusable focusKey="TOP-SETTINGS" scale="sm" onEnter={onSettings}>
          <span
            className={[
              'flex h-9 w-9 items-center justify-center rounded-full glass transition duration-[var(--dur-focus-out)]',
              settingsActive ? 'bg-white text-black' : 'text-white/75',
            ].join(' ')}
          >
            <Settings className="h-4 w-4" />
          </span>
        </Focusable>
      </div>
    </header>
  )
}

function formatClock(): string {
  return new Date().toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}
