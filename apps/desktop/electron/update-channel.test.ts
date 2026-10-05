import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, expect, test } from 'vitest'

import { readDesktopUpdateConfig, resolveDesktopUpdateBranch } from './update-channel'

const homes: string[] = []
afterEach(() => homes.splice(0).forEach(home => fs.rmSync(home, { recursive: true, force: true })))

function fixture() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-channel-'))
  homes.push(home)
  const config = path.join(home, 'updates.json')
  const remote = path.join(home, 'remote')
  const repo = path.join(home, 'checkout')
  fs.mkdirSync(remote)

  const git = (cwd: string, ...args: string[]) => {
    const result = spawnSync('git', args, {
      cwd,
      encoding: 'utf8',
      env: {
        ...process.env,
        HOME: home,
        GIT_CONFIG_GLOBAL: path.join(home, 'gitconfig'),
        GIT_CONFIG_NOSYSTEM: '1',
        GIT_TERMINAL_PROMPT: '0'
      }
    })

    if (result.status !== 0) {
      throw new Error(result.stderr)
    }

    return result.stdout.trim()
  }

  git(remote, 'init', '-b', 'main')
  git(
    remote,
    '-c',
    'user.name=Test',
    '-c',
    'user.email=test@example.invalid',
    'commit',
    '--allow-empty',
    '-m',
    'initial'
  )
  git(remote, 'branch', 'stable')
  git(home, 'clone', '--single-branch', '--branch', 'main', remote, repo)

  const runGit = async (args: string[], options: { cwd: string }) => {
    const result = spawnSync('git', args, {
      ...options,
      encoding: 'utf8',
      env: {
        ...process.env,
        HOME: home,
        GIT_CONFIG_GLOBAL: path.join(home, 'gitconfig'),
        GIT_CONFIG_NOSYSTEM: '1',
        GIT_TERMINAL_PROMPT: '0'
      }
    })

    return { code: result.status ?? 1, stdout: result.stdout, stderr: result.stderr }
  }

  return { config, remote, repo, git, runGit }
}

test('Desktop check and handoff follow enrollment, never checkout HEAD or a fallback branch', async () => {
  const { config, remote, repo, git, runGit } = fixture()
  expect(readDesktopUpdateConfig(config).branch).toBe('main')
  const policy = '{"branch":"stable","other":"keep"}'
  fs.writeFileSync(config, policy)
  expect(git(repo, 'branch', '--show-current')).toBe('main')
  expect(await resolveDesktopUpdateBranch(config, repo, 'git')).toBe('stable')
  git(remote, 'branch', '-D', 'stable')
  // refs/heads/other/stable must not satisfy a missing refs/heads/stable.
  git(remote, 'branch', 'other/stable')
  git(remote, 'branch', 'other/refs/heads/stable')
  await expect(resolveDesktopUpdateBranch(config, repo, runGit)).rejects.toThrow(/stable.*missing/i)
  expect(fs.readFileSync(config, 'utf8')).toBe(policy)
  git(repo, 'remote', 'set-url', 'origin', path.join(remote, 'gone'))
  await expect(resolveDesktopUpdateBranch(config, repo, runGit)).rejects.toThrow(/verify.*stable/i)
  expect(fs.readFileSync(config, 'utf8')).toBe(policy)
})

test('enrolled main refuses a deleted remote ref even with a stale tracking ref', async () => {
  const { config, remote, repo, git, runGit } = fixture()
  fs.writeFileSync(config, '{"branch":"main"}')
  git(remote, 'branch', '-m', 'main', 'retired')
  await expect(resolveDesktopUpdateBranch(config, repo, runGit)).rejects.toThrow(/main.*missing/)
})

test('absence is not an enrolled main branch', () => {
  const { config } = fixture()
  expect(readDesktopUpdateConfig(config)).toEqual({ branch: 'main', branchExplicit: false })
  fs.writeFileSync(config, '{"other":"keep"}')
  expect(readDesktopUpdateConfig(config).branchExplicit).toBe(false)
  fs.writeFileSync(config, '{"branch":"main"}')
  expect(readDesktopUpdateConfig(config).branchExplicit).toBe(true)
  fs.writeFileSync(config, '\ufeff{"branch":"stable"}')
  expect(readDesktopUpdateConfig(config).branch).toBe('stable')
})

test('Desktop refuses malformed/unreadable selected-channel configuration', async () => {
  const { config, repo, runGit } = fixture()

  for (const content of [
    '{',
    'null',
    '[]',
    '{"branch":null}',
    '{"branch":3}',
    '{"branch":""}',
    '{"branch":" "}',
    '{"branch":"--all"}',
    '{"branch":"a:b"}',
    '{"branch":"a..b"}'
  ]) {
    fs.writeFileSync(config, content)
    expect(() => readDesktopUpdateConfig(config)).toThrow(/updates.json/)
    await expect(resolveDesktopUpdateBranch(config, repo, runGit)).rejects.toThrow(/updates.json/)
  }

  fs.writeFileSync(config, Buffer.from('{"branch":"st\xffable"}', 'latin1'))
  expect(() => readDesktopUpdateConfig(config)).toThrow(/updates.json/)
  fs.unlinkSync(config)
  fs.mkdirSync(config)
  expect(() => readDesktopUpdateConfig(config)).toThrow(/updates.json/)
})
