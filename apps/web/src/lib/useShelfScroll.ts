import { useCallback, useRef } from 'react'

/**
 * 横向货架的焦点跟随滚动。
 *
 * 直接 `scrollIntoView({inline:'center'})` 会连窗口一起滚（block 的默认行为），
 * 所以这里拆成两件事：
 * - 横向：只滚货架容器，把卡片居中；
 * - 纵向：仅当卡片跑出视口上/下沿时，窗口滚到刚好可见的位置。
 *
 * 横向位置用 `offsetLeft` 计算，不受卡片自身 transform（焦点放大/倾斜）影响。
 */
export function useShelfScroll<T extends HTMLElement>() {
  const ref = useRef<T>(null)

  const scrollToChild = useCallback((index: number) => {
    const el = ref.current
    const child = el?.children[index] as HTMLElement | undefined
    if (!el || !child) return

    const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const behavior: ScrollBehavior = smooth ? 'smooth' : 'auto'

    const left = child.offsetLeft - (el.clientWidth - child.offsetWidth) / 2
    el.scrollTo({ left: Math.max(0, left), behavior })

    const rect = child.getBoundingClientRect()
    const topLimit = 96
    const bottomLimit = window.innerHeight - 32
    if (rect.top < topLimit) {
      window.scrollBy({ top: rect.top - topLimit - 24, behavior })
    } else if (rect.bottom > bottomLimit) {
      window.scrollBy({ top: rect.bottom - bottomLimit + 24, behavior })
    }
  }, [])

  return { ref, scrollToChild }
}
