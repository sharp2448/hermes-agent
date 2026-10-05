export type BranchRequestMethod = 'session.branch' | 'session.branch_whole' | 'session.branch_stored' | 'session.create'

/** Only old admission's exact extra-field rejection proves the handler never ran.
 * A timeout, lost response or domain error may follow a successful creation, so
 * those must keep the key and surface to the caller, never trigger a resend. */
export async function requestBranchWithLegacyParams<T>(
  request: (method: BranchRequestMethod, params: Record<string, unknown>) => Promise<T>,
  method: BranchRequestMethod,
  params: Record<string, unknown>
): Promise<T> {
  try {
    return await request(method, params)
  } catch (error) {
    const failure = error as { code?: unknown; message?: unknown } | null
    const rejection = `invalid params for ${method}: idempotency_key: Extra inputs are not permitted`

    const outOfSync =
      ' — the client and the Hermes backend are out of sync (different versions); run `hermes update` and restart both'

    if (
      !Object.hasOwn(params, 'idempotency_key') ||
      failure?.code !== 4000 ||
      (failure.message !== rejection && failure.message !== rejection + outOfSync)
    ) {
      throw error
    }

    const { idempotency_key: _key, ...legacyParams } = params

    // Do not catch this resend: any further failure must surface without replay.
    return request(method, legacyParams)
  }
}
