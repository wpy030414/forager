import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { useFocusable } from '@noriginmedia/norigin-spatial-navigation'
import { cn } from '@/lib/utils'

/**
 * 焦点上下文：转场中退场的旧页需要整体失焦，否则它卸载时 norigin 会把焦点
 * 自动还给父节点，把入场页刚设好的焦点抢走。
 */
export const FocusActiveContext = createContext(true)

/**
 * 当前按下的方向（模块级）：onArrowPress 在导航之前同步触发，所以新卡获焦时
 * 读到的就是「焦点从哪个方向移过来」，用来决定初始倾斜角。
 */
let lastDir: 'up' | 'down' | 'left' | 'right' | null = null

/** 倾斜角：左右键给出 ±tiltMax 的 rotateY，上下键回正 */
function tiltFor(dir: 'up' | 'down' | 'left' | 'right' | null, max: number): number {
  if (dir === 'left') return max
  if (dir === 'right') return -max
  return 0
}

/**
 * tvOS 焦点组件：非焦点态无边框；获焦时 spring 放大 + 白描边 + 外发光 + 上浮。
 * 开启 tilt 时，卡片会按方向做 ±tiltMax 的 rotateY，并用 CSS 变量给
 * `[data-layer="img"|"text"]` 子元素下发位移，形成三层视差。
 *
 * 焦点态由空间导航驱动，鼠标悬停只做视觉补充（电视上鼠标不参与）。
 */
interface FocusableProps {
  children: ReactNode
  className?: string
  /** 遥控器确认键 / Enter */
  onEnter?: () => void
  /** 获焦时回调：首页背景联动、横向列表滚动等 */
  onFocus?: () => void
  /** 失焦时回调 */
  onBlur?: () => void
  /** 方向键在获焦组件上按下（自定义移动行为时用） */
  onArrow?: (dir: 'up' | 'down' | 'left' | 'right') => void
  focusKey?: string
  /** 大卡片用更大放大倍率 */
  scale?: 'sm' | 'md' | 'lg'
  /** 方向倾斜视差，默认开启；纯文字按钮建议 'disabled' */
  tilt?: boolean | 'disabled'
  /** 最大倾斜角（度） */
  tiltMax?: number
  /** 描边呼吸，网格里卡片很多时建议 'static' */
  ring?: 'breathing' | 'static'
  /** 圆角，需与子元素一致，描边层才能贴合 */
  radius?: string
}

export function Focusable({
  children,
  className,
  onEnter,
  onFocus,
  onBlur,
  onArrow,
  focusKey,
  scale = 'md',
  tilt = true,
  tiltMax = 1.2,
  ring = 'breathing',
  radius = '1rem',
}: FocusableProps) {
  const active = useContext(FocusActiveContext)
  const [hovered, setHovered] = useState(false)
  const [dir, setDir] = useState<number>(0)
  const entered = useRef(false)

  const { ref, focused } = useFocusable({
    focusKey,
    focusable: active,
    onEnterPress: onEnter ? () => onEnter() : undefined,
    onFocus: () => onFocus?.(),
    onBlur: () => onBlur?.(),
    onArrowPress: (next) => {
      const d = next as 'up' | 'down' | 'left' | 'right'
      lastDir = d
      // 按下时就先把倾斜做起来，与导航并行动画，避免「先放大、后倾斜」的两段感；
      // 但当前卡同时要先回正，这里只记录方向，倾斜交给新卡表现。
      entered.current = false
      setDir(0)
      onArrow?.(d)
      return true
    },
  })

  // 获焦瞬间读取上线方向，决定初始倾斜角
  useEffect(() => {
    if (!focused) {
      entered.current = false
      setDir(0)
      return
    }
    if (entered.current) return
    entered.current = true
    setDir(tilt === 'disabled' ? 0 : tiltFor(lastDir, tiltMax))
  }, [focused, tilt, tiltMax])

  const hot = focused || hovered
  const zoom = scale === 'lg' ? 1.1 : scale === 'sm' ? 1.04 : 1.08
  const deg = hot ? dir : 0

  return (
    <div
      ref={ref}
      data-focusable
      data-focused={hot ? '' : undefined}
      onClick={onEnter}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className={cn(
        'relative cursor-pointer outline-none transition-transform duration-[var(--dur-focus)] ease-spring',
        hot && 'z-10',
        className,
      )}
      style={{
        transform: `perspective(900px) rotateY(${deg}deg) scale(${hot ? zoom : 1})`,
        ['--tilt-px-img' as string]: `${deg * -5}px`,
        ['--tilt-px-text' as string]: `${deg * -12}px`,
        willChange: hot ? 'transform' : 'auto',
      }}
    >
      {children}

      {/* 描边 + 外发光：独立一层，只过渡 opacity，不牵动卡片本身的阴影计算 */}
      <div
        aria-hidden
        className={cn(
          'pointer-events-none absolute -inset-[3px] transition-opacity duration-[var(--dur-focus)] ease-spring',
          ring === 'breathing' && 'ring-breathe',
        )}
        style={{ borderRadius: radius, opacity: hot ? 1 : 0 }}
      >
        <div
          className="absolute inset-0 ring-[2.5px] ring-white/90"
          style={{ borderRadius: radius, filter: 'drop-shadow(0 18px 34px rgba(0,0,0,0.55))' }}
        />
        <div
          className="absolute inset-0 ring-[6px] ring-white"
          style={{ borderRadius: radius, opacity: 'var(--glow, 0.12)' }}
        />
      </div>
    </div>
  )
}
