import { useLayoutEffect, useRef, type ReactNode, type RefObject } from 'react'
import { cn } from '@/lib/utils'
import { shelfWheel } from '@/lib/focus'

/**
 * 横向货架：焦点跟随滚动由 Focus Manager 统一承担——
 * 获焦卡片自己找到最近的 `[data-shelf-scroll]` 容器做横向居中，窗口只在必要时纵向跟随。
 *
 * 这里的 ref 并不被全局读取，独立成 hook 是为了让「一个货架 = 一个滚动容器」显式可读，
 * 并按容器宽度给出右缘渐隐（暗示还有内容）。
 */
export function useShelfScroll<T extends HTMLElement>(): {
  ref: RefObject<T | null>
} {
  const ref = useRef<T | null>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => {
      const more = el.scrollLeft + el.clientWidth < el.scrollWidth - 2
      el.classList.toggle('shelf-right-fade', more)
    }
    update()
    el.addEventListener('scroll', update, { passive: true })
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => {
      el.removeEventListener('scroll', update)
      observer.disconnect()
    }
  }, [])

  return { ref }
}

/** 货架容器：横向滚动 + 换来的滚轮 / 触控板横滚只滚自己 */
export function ShelfRow({
  shelfRef,
  className,
  children,
}: {
  shelfRef: RefObject<HTMLDivElement | null>
  className?: string
  children: ReactNode
}) {
  return (
    <div
      ref={shelfRef}
      data-shelf
      data-shelf-scroll
      onWheel={shelfWheel}
      className={cn(
        'shelf-scroll shelf-scroll-x flex [scrollbar-width:none] [&::-webkit-scrollbar]:hidden',
        className,
      )}
    >
      {children}
    </div>
  )
}
