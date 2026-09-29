// Runtime credentials are never bundled or sent in query strings; storage is tab-scoped.
export const API = import.meta.env.DEV ? 'http://127.0.0.1:4436' : window.location.origin
const STORAGE_KEY = 'legilimens-control-token'
export const AUTH_REQUIRED = 'legilimens-auth-required'
let token = ''
let wsPort = 4435
let proxyPort = 4433

export function setControlToken(value: string) {
  token = value.trim()
  try {
    if (token) sessionStorage.setItem(STORAGE_KEY, token)
    else sessionStorage.removeItem(STORAGE_KEY)
  } catch { /* Memory-only access still works with browser storage disabled. */ }
}

const fragment = new URLSearchParams(window.location.hash.slice(1))
if (fragment.has('token')) {
  const initialToken = fragment.get('token') || ''
  // Remove credentials before React mounts or any API request is made.
  window.history.replaceState(null, '', window.location.pathname + window.location.search)
  setControlToken(initialToken)
} else {
  try { token = sessionStorage.getItem(STORAGE_KEY) || '' } catch { /* optional storage */ }
}

export function getControlToken() { return token }

export function requireAuthentication() {
  setControlToken('')
  window.dispatchEvent(new Event(AUTH_REQUIRED))
}

export async function apiFetch(path: string, init: RequestInit = {}) {
  if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Invalid API path')
  const requestToken = token
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${requestToken}`)
  const response = await fetch(API + path, {
    ...init, headers, credentials: 'omit', redirect: 'error', cache: 'no-store',
    signal: init.signal ?? AbortSignal.timeout(10000),
  })
  if (response.status === 401 && token === requestToken) requireAuthentication()
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`)
  }
  return response
}

export async function authenticateControl() {
  const response = await apiFetch('/health')
  const health = await response.json()
  const validPort = (value: unknown): value is number => Number.isInteger(value) && Number(value) > 0 && Number(value) <= 65535
  if (health.service !== 'legilimens' || health.status !== 'ok' || !validPort(health.wsPort) || !validPort(health.proxyPort)) {
    throw new Error('Unexpected backend response')
  }
  wsPort = health.wsPort
  proxyPort = health.proxyPort
}

export function captureUrl() { return `ws://127.0.0.1:${wsPort}/` }
export function proxyUrl() { return `https://127.0.0.1:${proxyPort}/` }
