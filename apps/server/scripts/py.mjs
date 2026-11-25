#!/usr/bin/env node
// 统一 Python 入口：优先使用 apps/server/.venv，未建虚拟环境时回退到 PATH。
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'

const serverDir = resolve(import.meta.dirname, '..')
const venvPython = [
  join(serverDir, '.venv', 'Scripts', 'python.exe'),
  join(serverDir, '.venv', 'bin', 'python'),
].find(existsSync)

const command = venvPython ?? (process.platform === 'win32' ? 'python' : 'python3')
const result = spawnSync(command, process.argv.slice(2), {
  cwd: serverDir,
  stdio: 'inherit',
  // Windows 下 Python 的标准输出按控制台代码页（多为 GBK）编码，
  // 而 pnpm 子进程把管道当文件处理，中文日志到终端就成了乱码。
  // 统一走 UTF-8 通道：提交 git-bash / Windows Terminal，cmd.exe 需先 chcp 65001。
  env: { ...process.env, PYTHONUTF8: process.env.PYTHONUTF8 ?? '1' },
})

if (result.error) {
  console.error(`无法启动 Python：${result.error.message}`)
  process.exit(1)
}
process.exit(result.status ?? 1)
