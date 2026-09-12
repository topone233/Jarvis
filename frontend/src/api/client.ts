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

export function withJsonBody(init: RequestInit): RequestInit {
  if (init.body === undefined) {
    return init
  }
  return { ...init, headers: { 'Content-Type': 'application/json', ...init.headers } }
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

/**
 * Tells the app whether it has been set up yet.
 *
 * `GET /api/health` is the only route that never raises `setup_required`, which
 * makes it the one thing safe to ask before anything is configured.
 */
export async function checkHealth(): Promise<{
  configured: boolean
  status: string
}> {
  const response = await fetch('/api/health')
  if (!response.ok) {
    throw new ApiError('无法连接 Jarvis 服务。', response.status, 'unreachable')
  }
  return (await response.json()) as { configured: boolean; status: string }
}
