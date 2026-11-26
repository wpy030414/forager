import { createContext, type WheelEvent as ReactWheelEvent } from 'react'
import { nav } from '@/lib/norigin'

/**
 * 全局 Focus Manager：页面与组件只声明「我是谁、从哪来的」，
 * 不直接碰 norigin 的 setFocus，也不各自写 onFocus + scrollIntoView。
 *
 * - 焦点恢复：每次焦点转移都记下上一站；获焦元素被卸载（轮询刷新、
 *   删除、弹层关闭）时按「同货架相邻卡 → 上一站 → 页面落点 → 第一个」回退；
 *   删改操作也可以先用 focusOrFallback 指定明确的去处。
 * - 每个 Focusable 挂载时拿到新的实例 key（React 复用不会让 norigin 的注册表错位），
 *   DOM 上的语义 key（data-focus-key）负责给这里做映射。
 * - 横向货架：焦点落进货架里的卡片时，货架自己横向滚动到居中
 *   （不通过 scrollIntoView —— 那会把窗口一起滚），窗口只在卡片跑出视口时纵向跟随。
 * - 页面切换：每个页面有稳定的 pageKey；离开时记住焦点，带着页面记忆回来时还原。
 *   跨重启也能接上：每个页面记下焦点所在卡片的「位置指纹」（第几个货架、第几张），
 *   下次渲染出同样位置的那张卡时自动接上。
 * - Tab / 方向键：方向键交给 norigin 做空间导航；Tab 在应用内化成
 *   「下一个 / 上一个可聚焦元素」，和方向键共用同一份焦点状态，
 *   不会出现浏览器原生焦点与空间焦点各指一边。
 */

export const FocusActiveContext = createContext(true)

const PAGE_LANDINGS = new Map<string, string>()
const PAGE_MEMORY = new Map<string, string>()
/** 页面 → 该页 focusKey 的公共前缀（focusKey 不一定以页面名开头，如 HOME-* / SRC-*） */
const PAGE_SCOPE = new Map<string, string>()
/** 每个 focusKey 最近一次获焦的时长：找同位置替补时优先「用户的常驻卡」 */
const DWELL = new Map<string, number>()

let current = ''
let previous = ''
let listener: ((key: string) => void) | null = null

// ---------------------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------------------

/** App 挂载时调用一次；返回卸载清理函数。 */
export function startFocusManager(): () => void {
  nav.init({
    debug: false,
    visualDebug: false,
    shouldFocusDOMNode: true,
    shouldUseNativeEvents: false,
  })

  const onNativeFocus = (event: FocusEvent) => {
    // 浏览器原生焦点漂回 body（点到空白处）时，空间焦点不能跟着丢
    if (event.target === document.body && current && !nav.exists(current)) {
      void restoreFocus()
    }
  }
  const onKey = (event: KeyboardEvent) => {
    if (event.key !== 'Tab' || event.altKey || event.ctrlKey || event.metaKey) return
    // 输入框：Tab 留给浏览器，但输入框不是空间焦点，映射到它的邻居
    if (isTextField(document.activeElement)) return
    if (!current) return
    event.preventDefault()
    void step(event.shiftKey ? -1 : 1)
  }

  document.addEventListener('focusin', onNativeFocus)
  document.addEventListener('keydown', onKey)
  return () => {
    document.removeEventListener('focusin', onNativeFocus)
    document.removeEventListener('keydown', onKey)
    nav.destroy()
  }
}

/** App 订阅焦点变化（决定要不要还原页面记忆） */
export function observeFocus(fn: ((key: string) => void) | null): void {
  listener = fn
}

/**
 * 开新会话时清空记忆：HTTP 会话过期后焦点记忆里的位置对不上新内容，
 * 按记忆接上只会落在别处；重新登录后由各页落脚点决定。
 */
export function resetFocusMemory(): void {
  PAGE_MEMORY.clear()
  PAGE_SLOTS.clear()
  SLOTS.clear()
  if (nav.exists(current)) return
  current = ''
  previous = ''
}

/** 主键（遥控器确认 / Enter）在可聚焦元素之外按下：拿回焦点 */
export function reclaimFocus(): void {
  if (current && mounted(current)) {
    void focus(current)
    return
  }
  void restoreFocus()
}

// ---------------------------------------------------------------------------
// 焦点转移
// ---------------------------------------------------------------------------

/** Focusable 获焦 / 失焦时调用 */
export function notifyFocus(key: string, instance = ''): void {
  if (key === current) return
  if (current) {
    settle(current)
    previous = current
  }
  current = key
  DWELL.set(key, performance.now())
  const slot = slotOf(instance, key)
  if (slot) SLOTS.set(key, slot)
  listener?.(key)
}

export function notifyBlur(key: string): void {
  // 有人接棒了（notifyFocus 先到）：这次失焦只是转移
  if (current !== key) return
  settle(key)
  previous = key
  current = ''
}

function settle(key: string) {
  DWELL.set(key, Math.max(0, performance.now() - (DWELL.get(key) ?? performance.now())))
}

/**
 * 页面语义 key → norigin 实例 key。Focusable 每次挂载都用新的实例 key（见 Focusable.tsx），
 * DOM 上的 data-focus-key 是语义 key，这里按「最后挂载的那个」找实例。
 */
function instanceFor(key: string): string | undefined {
  const all = document.querySelectorAll<HTMLElement>(
    `[data-focus-key="${CSS.escape(key)}"][data-instance]`,
  )
  const last = all[all.length - 1]
  return last?.dataset.instance
}

/** 这个语义 key 现在有没有可聚焦的 DOM 元素（能找元素就能设焦点） */
function mounted(key: string): boolean {
  return instanceFor(key) !== undefined
}

/** 等某个 key 挂载出来（最多等 frames 帧）后设焦点；不传旧表现则静默失败 */
export async function focusWhenMounted(
  key: string,
  frames = 24,
  isCancelled: () => boolean = () => false,
): Promise<boolean> {
  for (let left = frames; left > 0 && !isCancelled(); left -= 1) {
    if (mounted(key)) {
      await focus(key)
      return !isCancelled()
    }
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
  }
  return false
}

/** 程序化设焦点（传语义 key）。目标不存在时静默失败，由调用方决定回退。 */
export function focus(key: string): Promise<void> {
  const instance = instanceFor(key)
  if (!instance) return Promise.resolve()
  return nav.setFocus(instance).catch(() => {})
}

/** 能设就设，设不了依次退到 fallback → 页面落脚 → 上一站 → 第一个可聚焦元素 */
export function focusOrFallback(key: string | undefined, fallback?: string): Promise<void> {
  const target = firstExisting(key, fallback, landing(), previous, firstFocusable())
  return target ? focus(target) : Promise.resolve()
}

/** 焦点落空后的恢复：同货架相邻卡 → 上一站 → 页面落脚 → 第一个 */
export function restoreFocus(): Promise<void> {
  const lost = current || previous || nav.current()
  const target = firstExisting(
    siblingOf(lost),
    previous !== lost ? previous : undefined,
    landing(),
    firstFocusable(),
  )
  return target ? focus(target) : Promise.resolve()
}

function firstExisting(...keys: Array<string | undefined>): string | undefined {
  return keys.find((key): key is string => Boolean(key && mounted(key)))
}

function firstFocusable(): string | undefined {
  return document.querySelector<HTMLElement>('[data-focus-key]')?.dataset.focusKey
}

function landing(): string | undefined {
  const page = pageOfFocus(current) ?? current
  return PAGE_LANDINGS.get(page)
}

/** focusKey 的页面名：取注册时登记的归属，没登记就按「-」切第一段 */
export function pageOfFocus(key: string): string | undefined {
  for (const [page, prefix] of PAGE_SCOPE) {
    if (prefix && key.startsWith(prefix)) return page
  }
  return key ? key.split(/[-/]/)[0] : undefined
}

/** 把 focusKey 的页面前缀登记下来（Focusable 在 page 下渲染时自动做） */
export function scopeKey(page: string, key: string): void {
  if (!key) return
  const existing = PAGE_SCOPE.get(page)
  if (!existing || key.length < existing.length) PAGE_SCOPE.set(page, key)
}

// ---------------------------------------------------------------------------
// 同位置替补
// ---------------------------------------------------------------------------

/** 和 key 同一个货架（没有货架就看整页）里驻留最久的那张卡 */
function siblingOf(key: string): string | undefined {
  const el = key ? elFor(key) : null
  const shelf = el?.closest('[data-shelf]') ?? document.querySelector('[data-shelf]')
  if (!shelf) return undefined
  const keys = [...shelf.querySelectorAll<HTMLElement>('[data-focus-key]')]
    .map((node) => node.dataset.focusKey ?? '')
    .filter((k) => k && k !== key && mounted(k))
  return keys.sort((a, b) => (DWELL.get(b) ?? 0) - (DWELL.get(a) ?? 0))[0]
}

function elFor(key: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-focus-key="${CSS.escape(key)}"]`)
}

// ---------------------------------------------------------------------------
// Tab / 上一个、下一个
// ---------------------------------------------------------------------------

function step(delta: 1 | -1): Promise<void> {
  const keys = [...document.querySelectorAll<HTMLElement>('[data-focus-key]')]
    .map((el) => el.dataset.focusKey ?? '')
    .filter((key) => key && mounted(key))
  if (keys.length === 0) return Promise.resolve()
  const index = keys.indexOf(current)
  const next = keys[(index + delta + keys.length) % keys.length]
  return focus(next)
}

function isTextField(el: Element | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  if (el.isContentEditable || el.tagName === 'TEXTAREA') return true
  if (el.tagName === 'INPUT') {
    const type = (el as HTMLInputElement).type
    return !['button', 'checkbox', 'radio', 'submit', 'reset', 'range'].includes(type)
  }
  return false
}

// ---------------------------------------------------------------------------
// 页面：落脚点与记忆
// ---------------------------------------------------------------------------

export function setPageLanding(page: string, key: string): void {
  PAGE_LANDINGS.set(page, key)
}

/** 离开页面时记下焦点；回来（前进/后退）时原样还原 */
export function rememberPage(page: string, key: string): void {
  if (key && pageOfFocus(key) === page) {
    PAGE_MEMORY.set(page, key)
    const slot = SLOTS.get(key)
    if (slot) PAGE_SLOTS.set(page, slot)
  }
}

export function forgetPage(page: string): void {
  PAGE_MEMORY.delete(page)
}

/** 页面记下的焦点所在「位置」：第几个货架里的第几张卡 */
const PAGE_SLOTS = new Map<string, Slot>()
/** 语义 key → 它当时所在的位置（跨重启 / DOM 重建后按位置接上） */
const SLOTS = new Map<string, Slot>()

interface Slot {
  shelf: number
  item: number
  /** focusKey 的末段序号，退居其次的线索 */
  index: number
}

/** 获焦那一刻的位置：用实例 key（DOM 的 data-instance）定位，不依赖语义 key 唯一 */
function slotOf(instance: string, key: string): Slot | undefined {
  const el = document.querySelector<HTMLElement>(`[data-instance="${CSS.escape(instance)}"]`)
  const shelves = [...document.querySelectorAll<HTMLElement>('[data-shelf]')]
  const shelf = el?.closest<HTMLElement>('[data-shelf]')
  if (!el || !shelf) return undefined
  const items = [...shelf.querySelectorAll<HTMLElement>('[data-instance]')]
  return {
    shelf: shelves.indexOf(shelf),
    item: items.indexOf(el),
    index: Number(key.match(/(\d+)$/)?.[1] ?? -1),
  }
}

/** 按页面记下的位置，找当前已经注册出来的那张卡 */
function resumeSlot(page: string): string | undefined {
  const slot = PAGE_SLOTS.get(page)
  if (!slot) return undefined
  const shelf = document.querySelectorAll<HTMLElement>('[data-shelf]')[slot.shelf]
  const items = shelf ? [...shelf.querySelectorAll<HTMLElement>('[data-focus-key]')] : []
  const keys = items.map((el) => el.dataset.focusKey ?? '')
  const at = keys[slot.item] ?? keys.find((key) => key.endsWith(`-${slot.index}`))
  return at && mounted(at) ? at : undefined
}

/** 该页第一个（DOM 顺序）已注册的元素：二级页就是左上角返回键 */
function firstInPage(page: string): string | undefined {
  const prefix = PAGE_SCOPE.get(page)
  const candidates = [...document.querySelectorAll<HTMLElement>('[data-focus-key]')]
  for (const el of candidates) {
    const key = el.dataset.focusKey ?? ''
    if (!key || !mounted(key)) continue
    if (key.startsWith('TOP-') || (prefix ? key.startsWith(prefix) : pageOfFocus(key) === page)) {
      return key
    }
  }
  return undefined
}

function pageMemory(page: string): string | undefined {
  return PAGE_MEMORY.get(page)
}

/**
 * 进入页面（App 在页面切换时调用一次）。
 *
 * 新页的卡片要等数据回来才注册，所以分两段时间等：
 * 1. 页面记忆（按返回回来时）先等几帧，等得到就直接还原；
 * 2. 剩下的时间等该页落脚点（页内第一张卡）。等不到再退到页内第一个元素（返回键）。
 * 等待时只轮询注册表，不自己设焦点——退场层的 norigin 自动回焦可能挤进来。
 * 返回是否把焦点设上了。
 */
export async function enterPage(
  page: string,
  isCancelled: () => boolean = () => false,
): Promise<boolean> {
  const waitFrames = (frames: number) =>
    new Promise<boolean>((resolve) => {
      let left = frames
      const tick = () => {
        if (isCancelled()) return resolve(false)
        if (left-- <= 0) return resolve(true)
        requestAnimationFrame(tick)
      }
      tick()
    })
  // 记忆阶段只等 4 帧（按返回回来时卡片通常已在），落脚点阶段等满 24 帧（等数据）
  const until = async (useMemory: boolean) => {
    const remembered = pageMemory(page)
    const target = useMemory ? (remembered ?? PAGE_LANDINGS.get(page)) : PAGE_LANDINGS.get(page)
    for (let left = useMemory ? 4 : 24; left > 0 && !isCancelled(); left -= 1) {
      if (target && mounted(target)) return target
      if (useMemory && remembered && mounted(remembered)) return remembered
      await waitFrames(1)
    }
    return undefined
  }

  // 顺序：记忆 → 位置记忆（跨重启）→ 落脚点 → 页内第一个（返回键）→ 全局第一个。
  // 位置记忆排在落脚点前：等数据期间返回过首页再回来时，要接着上次那张卡，而不是跳回第一张。
  const key =
    (await until(true)) ??
    resumeSlot(page) ??
    (await until(false)) ??
    firstInPage(page) ??
    firstFocusable()
  if (!key || isCancelled()) return false
  await focus(key)
  return true
}

// ---------------------------------------------------------------------------
// 横向货架
// ---------------------------------------------------------------------------

/** 货架自带的横向位移（方向键换来的鼠标滚轮 / 触控板横滚），不影响焦点 */
export function shelfWheel(event: ReactWheelEvent<HTMLElement>): void {
  if (!event.shiftKey && Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return
  const shelf = event.currentTarget as HTMLElement
  const delta = event.shiftKey ? event.deltaY : event.deltaX
  shelf.scrollLeft += delta
}

export function scrollShelfTo(el: HTMLElement, shelf: HTMLElement): void {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const behavior: ScrollBehavior = reduced ? 'auto' : 'smooth'

  // 横向：只滚货架容器，把卡片居中；offsetLeft 不受卡片自身 transform（放大/倾斜）影响
  const left = el.offsetLeft - (shelf.clientWidth - el.offsetWidth) / 2
  shelf.scrollTo({ left: Math.max(0, left), behavior })

  // 纵向：仅当卡片跑出视口上下沿时，窗口滚到刚好可见
  const rect = el.getBoundingClientRect()
  const topLimit = 96
  const bottomLimit = window.innerHeight - 32
  if (rect.top < topLimit) window.scrollBy({ top: rect.top - topLimit - 24, behavior })
  else if (rect.bottom > bottomLimit)
    window.scrollBy({ top: rect.bottom - bottomLimit + 24, behavior })
}
