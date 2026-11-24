import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '@/lib/utils'

/**
 * 全屏弹层：进场 460ms（幕帘淡入 + 面板上浮），退场 200ms。
 * 退场不立即卸载——先播动画，`animationend` 后再撤 DOM，
 * 这样 reduce-motion 的 0.001ms 兜底也照常触发。
 */
export function Overlay({
  open,
  onClose,
  onEntered,
  onExited,
  children,
  className,
}: {
  open: boolean
  onClose: () => void
  /** 入场动画结束（此时面板尺寸已稳定，适合设焦点） */
  onEntered?: () => void
  /** 退场动画结束、DOM 即将移除 */
  onExited?: () => void
  children: ReactNode
  className?: string
}) {
  const [mounted, setMounted] = useState(open)
  const [closing, setClosing] = useState(false)
  const entered = useRef(false)

  useEffect(() => {
    if (open) {
      entered.current = false
      setMounted(true)
      setClosing(false)
    } else if (mounted) {
      setClosing(true)
    }
  }, [open, mounted])

  if (!mounted) return null

  return createPortal(
    <div
      className={cn(
        'fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 md:p-8',
        'backdrop-blur-2xl',
        closing ? 'animate-veil-out' : 'animate-veil-in',
      )}
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      onAnimationEnd={(event) => {
        if (event.target !== event.currentTarget) return
        if (closing) {
          setMounted(false)
          onExited?.()
        } else if (!entered.current) {
          entered.current = true
          onEntered?.()
        }
      }}
    >
      <div
        className={cn(
          'glass-panel flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-[28px]',
          closing ? 'animate-panel-out' : 'animate-panel-in',
          className,
        )}
        onClick={(event) => event.stopPropagation()}
      >
        {children}
      </div>
    </div>,
    document.body,
  )
}
