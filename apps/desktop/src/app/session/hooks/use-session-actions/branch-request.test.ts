import { JsonRpcGatewayError } from '@hermes/shared'
import { describe, expect, it, vi } from 'vitest'

import { type BranchRequestMethod, requestBranchWithLegacyParams } from './branch-request'

// Old gateway admission (contracts/registry.py), before the branch handler runs.
const outOfSync =
  ' — the client and the Hermes backend are out of sync (different versions); run `hermes update` and restart both'

const rejection = (method: string, field = 'idempotency_key', code = 4000) =>
  new JsonRpcGatewayError(`invalid params for ${method}: ${field}: Extra inputs are not permitted${outOfSync}`, {
    code
  })

const cases: Array<{ method: BranchRequestMethod; params: Record<string, unknown> }> = [
  { method: 'session.branch', params: { session_id: 'runtime-parent', count: 2 } },
  { method: 'session.branch_whole', params: { session_id: 'runtime-parent' } },
  {
    method: 'session.branch_stored',
    params: { parent_session_id: 'stored-parent', source: 'desktop', cols: 96, cwd: '/work', profile: 'research' }
  },
  {
    method: 'session.create',
    params: {
      parent_session_id: 'stored-parent',
      source: 'desktop',
      cols: 96,
      cwd: '/work',
      profile: 'research',
      messages: [{ role: 'user', content: 'preserve this seed' }]
    }
  }
]

describe('branch request admission compatibility', () => {
  it.each(cases)(
    'keeps idempotency on new $method and drops only its rejected key on old admission',
    async ({ method, params }) => {
      const keyed = Object.freeze({ ...params, idempotency_key: 'same-attempt' })
      const child = { session_id: 'child', stored_session_id: 'stored-child' }
      const current = vi.fn().mockResolvedValue(child)

      await expect(requestBranchWithLegacyParams(current, method, keyed)).resolves.toBe(child)
      expect(current.mock.calls).toEqual([[method, keyed]])

      const old = vi.fn().mockRejectedValueOnce(rejection(method)).mockResolvedValue(child)

      await expect(requestBranchWithLegacyParams(old, method, keyed)).resolves.toBe(child)
      expect(old.mock.calls).toEqual([
        [method, keyed],
        [method, params]
      ])
      expect(keyed.idempotency_key).toBe('same-attempt')
    }
  )

  it('never replays an ambiguous failure, another field/method rejection, or a failed legacy retry', async () => {
    const method = 'session.branch'
    const keyed = { session_id: 'parent', count: 2, idempotency_key: 'same-attempt' }
    const admission = rejection(method)

    const failures: unknown[] = [
      new Error('request timed out'),
      new Error('transport disconnected after branch execution; response lost'),
      new JsonRpcGatewayError('idempotency_key failed after creating child', { code: 4000 }),
      new JsonRpcGatewayError('invalid params for session.branch: idempotency_key: invalid value', { code: 4000 }),
      rejection(method, 'count'),
      rejection(method, 'messages.0.idempotency_key'),
      rejection(method, 'idempotency_key_suffix'),
      rejection('session.branch_whole'),
      rejection(method, 'idempotency_key', -32000),
      new Error(admission.message),
      { code: '4000', message: admission.message },
      new JsonRpcGatewayError(`handler error: ${admission.message}`, { code: 4000 }),
      new JsonRpcGatewayError(`${admission.message}; another failure`, { code: 4000 }),
      null
    ]

    for (const failure of failures) {
      const request = vi.fn().mockRejectedValue(failure)

      await expect(requestBranchWithLegacyParams(request, method, keyed)).rejects.toBe(failure)
      expect(request.mock.calls).toEqual([[method, keyed]])
    }

    const unkeyed = { session_id: 'parent' }
    const noKey = vi.fn().mockRejectedValue(admission)

    await expect(requestBranchWithLegacyParams(noKey, method, unkeyed)).rejects.toBe(admission)
    expect(noKey.mock.calls).toEqual([[method, unkeyed]])

    for (const failure of [admission, ...failures]) {
      const retry = vi.fn().mockRejectedValueOnce(admission).mockRejectedValue(failure)

      await expect(requestBranchWithLegacyParams(retry, method, keyed)).rejects.toBe(failure)
      expect(retry.mock.calls).toEqual([
        [method, keyed],
        [method, { session_id: 'parent', count: 2 }]
      ])
    }
  })
})
