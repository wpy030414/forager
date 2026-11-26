import {
  destroy,
  doesFocusableExist,
  getCurrentFocusKey,
  init,
  setFocus as navSetFocus,
} from '@noriginmedia/norigin-spatial-navigation'

/**
 * norigin（空间导航）的唯一出口。Focusable 与 Focus Manager 都只经由这里访问，
 * 不直接 import norigin——测试或排障时可以在这里整体换成替身。
 *
 * 为什么要包一层：norigin 的注册表以「key」为准，同一个 key 被 React 复用到
 * 另一个节点（页面切换、列表重排）时，注册表里留着的还是旧节点和旧布局。
 * 所以 Focusable 每次挂载都用新 key，DOM 上的语义 key 只给这里做映射。
 */
export const nav = {
  init,
  destroy,
  setFocus: (key: string) => navSetFocus(key),
  exists: (key: string) => !!key && doesFocusableExist(key),
  current: () => getCurrentFocusKey(),
}
