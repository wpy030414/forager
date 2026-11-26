import { useEffect, useState } from 'react'
import { UNAUTHORIZED_EVENT } from '@/api/client'

/**
 * 会话是否已登录（模块级共享）：背景这类「装饰性请求」在登录前不该发出，
 * 否则登录页会先打一串 401。
 */
let authed = false
const listeners = new Set<(value: boolean) => void>()

export function setAuthed(value: boolean): void {
  authed = value
  for (const fn of listeners) fn(value)
}

export function useAuthed(): boolean {
  const [value, setLocal] = useState(authed)
  useEffect(() => {
    listeners.add(setLocal)
    setLocal(authed)
    return () => {
      listeners.delete(setLocal)
    }
  }, [])
  return value
}

window.addEventListener(UNAUTHORIZED_EVENT, () => setAuthed(false))
