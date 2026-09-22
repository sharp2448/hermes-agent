import fs from 'node:fs'

import { isOfficialSshRemote, OFFICIAL_REPO_HTTPS_URL } from './update-remote'

interface GitResult {
  code: number
  stdout: string
  stderr: string
}

type RunGit = (args: string[], options: { cwd: string }) => Promise<GitResult>

export function validateUpdateBranch(branch: unknown, source: string): string {
  if (
    typeof branch !== 'string' ||
    !branch ||
    branch === 'HEAD' ||
    branch.startsWith('-') ||
    branch.endsWith('.') ||
    /[\s~^:?*[\\]/.test(branch) ||
    [...branch].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) ||
    branch.includes('..') ||
    branch.includes('@{') ||
    branch.split('/').some(part => !part || part.startsWith('.') || part.endsWith('.lock'))
  ) {
    throw new Error(`Invalid update branch in ${source}; select a non-empty Git branch name.`)
  }

  return branch
}

export function readDesktopUpdateConfig(configPath: string): { branch: string } {
  let parsed: unknown

  try {
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(fs.readFileSync(configPath))
    parsed = JSON.parse(text)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT' && !fs.lstatSync(configPath, { throwIfNoEntry: false })) {
      return { branch: 'main' }
    }

    throw new Error(`Cannot read update channel ${configPath}: ${error}`)
  }

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`Invalid update channel ${configPath}: expected a JSON object.`)
  }

  const config = parsed as Record<string, unknown>

  return { ...config, branch: validateUpdateBranch('branch' in config ? config.branch : 'main', configPath) }
}

/** Check and handoff use the same enrolled channel, never the checkout's HEAD. */
export async function resolveDesktopUpdateBranch(
  configPath: string,
  updateRoot: string,
  runGit: RunGit
): Promise<string> {
  const { branch } = readDesktopUpdateConfig(configPath)

  if (branch === 'main') {
    return branch
  }

  const origin = await runGit(['remote', 'get-url', 'origin'], { cwd: updateRoot })
  const remote = isOfficialSshRemote(origin.stdout.trim()) ? OFFICIAL_REPO_HTTPS_URL : 'origin'

  const probe = await runGit(['ls-remote', '--exit-code', '--heads', remote, `refs/heads/${branch}`], {
    cwd: updateRoot
  })

  if (probe.code === 2) {
    throw new Error(
      `Selected update branch '${branch}' is missing on origin; channel unchanged. Restore it or change ${configPath}.`
    )
  }

  if (probe.code !== 0) {
    throw new Error(
      `Cannot verify update branch '${branch}' on origin: ${probe.stderr.trim() || 'git ls-remote failed'}`
    )
  }

  return branch
}
