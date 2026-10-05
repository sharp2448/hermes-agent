import fs from 'node:fs'

import { execGit } from './no-console-git'

interface GitResult {
  code: number | null
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

export function readDesktopUpdateConfig(configPath: string): { branch: string; branchExplicit: boolean } {
  let parsed: unknown

  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(fs.readFileSync(configPath))
    parsed = JSON.parse(text)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT' && !fs.lstatSync(configPath, { throwIfNoEntry: false })) {
      return { branch: 'main', branchExplicit: false }
    }

    throw new Error(`Cannot read update channel ${configPath}: ${error}`)
  }

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`Invalid update channel ${configPath}: expected a JSON object.`)
  }

  const config = parsed as Record<string, unknown>

  return {
    ...config,
    branch: validateUpdateBranch('branch' in config ? config.branch : 'main', configPath),
    branchExplicit: 'branch' in config
  }
}

/** Check and handoff use the same enrolled channel, never the checkout's HEAD. */
export async function resolveDesktopUpdateBranch(
  configPath: string,
  updateRoot: string,
  git: RunGit | string
): Promise<string> {
  const { branch, branchExplicit } = readDesktopUpdateConfig(configPath)

  if (!branchExplicit) {
    return branch
  }

  const env = { ...process.env, GIT_TERMINAL_PROMPT: '0', GCM_INTERACTIVE: 'Never' }
  for (const key of ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE', 'GIT_NAMESPACE']) {
    delete env[key]
  }
  const runGit: RunGit =
    typeof git === 'string' ? (args, options) => execGit(git, args, { ...options, env, timeoutMs: 30000 }) : git
  const probe = await runGit(['ls-remote', '--exit-code', '--heads', 'origin', `refs/heads/${branch}`], {
    cwd: updateRoot
  })

  const advertised = probe.stdout.split('\n').some(line => {
    const [sha, ref] = line.trim().split(/\s+/)
    return /^[0-9a-f]{40}$/i.test(sha || '') && ref === `refs/heads/${branch}`
  })
  if (probe.code === 2 || (probe.code === 0 && !advertised)) {
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
