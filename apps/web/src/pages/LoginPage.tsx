import { useEffect, useRef, useState } from 'react'
import { api } from '@/api/client'
import { Focusable } from '@/components/Focusable'

/**
 * 登录页：不进 PageStage（登录前后没有「页面」可言，转场反而干扰）。
 * 账号是本地单账号，密码由服务端用 Argon2id 校验；忘记密码只能靠首启日志里的恢复码。
 */
export function LoginPage({ onSuccess }: { onSuccess: () => void }) {
  const [mode, setMode] = useState<'password' | 'recover'>('password')
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [recovery, setRecovery] = useState('')
  const [nextPassword, setNextPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const userRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    userRef.current?.focus()
  }, [mode])

  async function submit() {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      if (mode === 'password') {
        await api.login(username.trim(), password)
      } else {
        await api.recover(recovery.trim(), nextPassword)
      }
      setPassword('')
      setNextPassword('')
      setRecovery('')
      onSuccess()
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setBusy(false)
    }
  }

  const inputClass =
    'w-full rounded-2xl bg-white/[0.06] px-4 py-3 text-[15px] text-white outline-none ' +
    'ring-1 ring-white/10 transition placeholder:text-white/25 focus:bg-white/10 focus:ring-white/30'

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center px-6">
      <div className="w-full max-w-md rounded-[28px] bg-[#0b0d12]/80 p-8 ring-1 ring-white/10 backdrop-blur-2xl">
        <div className="mb-1 text-xs font-medium uppercase tracking-[0.22em] text-white/35">
          forager
        </div>
        <h1 className="mb-7 text-2xl font-semibold text-white">
          {mode === 'password' ? '主人，请登录喵' : '用恢复码重设密码'}
        </h1>

        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          {mode === 'password' ? (
            <>
              <label className="block">
                <span className="mb-1.5 block text-xs text-white/45">用户名</span>
                <input
                  ref={userRef}
                  className={inputClass}
                  value={username}
                  autoComplete="username"
                  onChange={(event) => setUsername(event.target.value)}
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs text-white/45">密码</span>
                <input
                  className={inputClass}
                  type="password"
                  value={password}
                  autoComplete="current-password"
                  onChange={(event) => setPassword(event.target.value)}
                />
              </label>
            </>
          ) : (
            <>
              <label className="block">
                <span className="mb-1.5 block text-xs text-white/45">恢复码</span>
                <input
                  ref={userRef}
                  className={inputClass}
                  value={recovery}
                  placeholder="XXXXXX-XXXXXX-XXXXXX-XXXXXX"
                  onChange={(event) => setRecovery(event.target.value)}
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-xs text-white/45">新密码（至少 8 位）</span>
                <input
                  className={inputClass}
                  type="password"
                  value={nextPassword}
                  autoComplete="new-password"
                  onChange={(event) => setNextPassword(event.target.value)}
                />
              </label>
            </>
          )}

          {error ? <p className="text-sm text-red-300/90">{error}</p> : null}

          <div className="flex gap-2 pt-1">
            <Focusable scale="sm" tilt="disabled" onEnter={() => void submit()}>
              <span className="rounded-full bg-white px-5 py-2 text-sm font-medium text-black">
                {busy ? '验证中…' : mode === 'password' ? '进入' : '重设并进入'}
              </span>
            </Focusable>
            <Focusable
              scale="sm"
              tilt="disabled"
              onEnter={() => {
                setMode(mode === 'password' ? 'recover' : 'password')
                setError('')
              }}
            >
              <span className="rounded-full glass px-5 py-2 text-sm text-white/80">
                {mode === 'password' ? '用恢复码' : '返回登录'}
              </span>
            </Focusable>
          </div>
        </form>
      </div>
    </div>
  )
}
