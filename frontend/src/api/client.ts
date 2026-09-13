/**
 * The one place a Core API error becomes something the UI can act on.
 *
 * The backend answers failures with `{detail, code}` (`backend/app/main.py`'s
 * exception handlers), and `setup_required` is the one code with a destination:
 * the app is not configured, so the whole UI has to move to the setup screen.
 */

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(message: string, status: number, code: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

type SetupListener = () => void

let setupListener: SetupListener | null = null

/** Called when the backend says the data directory has not been chosen yet. */
export function onSetupRequired(listener: SetupListener | null): void {
  setupListener = listener
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, withJsonBody(init))
  if (!response.ok) {
    throw await toApiError(response)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

/**
 * The headers a call goes out with, built in exactly one place.
 *
 * This used to be built twice - once here and once in `streamRun` - and the
 * second copy dropped the content type. A body sent without
 * `Content-Type: application/json` is not parsed as JSON at all: FastAPI hands
 * the raw bytes to its validator, so the error that comes back describes a
 * shape the caller never sent. Building the object once is the whole fix.
 */
export function withJsonBody(init: RequestInit, accept?: string): RequestInit {
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string> | undefined),
  }
  if (init.body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }
  if (accept !== undefined) {
    headers.Accept = accept
  }
  return { ...init, headers }
}

export async function toApiError(response: Response): Promise<ApiError> {
  let detail = `请求失败（HTTP ${response.status}）`
  let code = ''
  try {
    const payload = (await response.json()) as { detail?: unknown; code?: string }
    code = payload.code ?? ''
    if (typeof payload.detail === 'string') {
      detail = payload.detail
    } else if (Array.isArray(payload.detail)) {
      // FastAPI's request-validation shape. Not worth rendering field by field;
      // the message only has to be honest about what happened.
      detail = '请求内容不符合要求。'
    }
  } catch {
    // A non-JSON body (a proxy error page, say) leaves the status-line message.
  }
  if (code === 'setup_required') {
    setupListener?.()
  }
  return new ApiError(detail, response.status, code)
}

export interface Health {
  configured: boolean
  status: string
  data_directory: string | null
  /**
   * Why a directory that *was* chosen could not be opened - it was deleted, or
   * its drive is not mounted. Kept apart from `data_directory`, because the path
   * is still worth showing: it is the one the user chose, and it is the one that
   * is missing.
   */
  data_directory_error: string | null
}

/**
 * Tells the app whether it has been set up yet, and where the data lives.
 *
 * `GET /api/health` is the only route that never raises `setup_required`, which
 * makes it the one thing safe to ask before anything is configured.
 */
export async function checkHealth(): Promise<Health> {
  const response = await fetch('/api/health')
  if (!response.ok) {
    throw new ApiError('无法连接 Jarvis 服务。', response.status, 'unreachable')
  }
  return (await response.json()) as Health
}
