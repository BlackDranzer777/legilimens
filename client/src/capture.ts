import type { TrafficEvent } from './store/useStore'

export const MAX_CAPTURE_FILE_BYTES = 16 * 1024 * 1024
export const MAX_CAPTURE_EVENTS = 500
const MAX_EVENT_CHARS = 1024 * 1024
const encoder = new TextEncoder()

export interface CaptureEvent {
  id: string
  type: TrafficEvent['type']
  direction: TrafficEvent['direction']
  payload: string
  payloadEncoding: 'utf8' | 'base64' | null
  rawSize: number
  timestamp: number
  latency: number
  sessionId: string | null
  streamId: string | null
  target: string | null
  flag: NonNullable<TrafficEvent['flag']>
}

export interface CaptureFile {
  format: 'legilimens-capture'
  version: 1
  source: 'retained-browser-events'
  exportedAt: number
  observationStartedAt: number
  loss: {
    completeness: 'not-confirmed'
    evictedEvents: number
    evictedStreams: number
    omittedStreamChunks: number
    warning: string | null
  }
  events: CaptureEvent[]
}

function invalid(message: string): never { throw new Error(`Invalid capture: ${message}`) }
function object(value: unknown, keys: string[], name: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid(`${name} must be an object`)
  const record = value as Record<string, unknown>
  if (Object.keys(record).length !== keys.length || keys.some((key) => !Object.prototype.hasOwnProperty.call(record, key))
    || Object.keys(record).some((key) => !keys.includes(key))) invalid(`${name} has missing or unknown fields`)
  return record
}
function integer(value: unknown, name: string, max = Number.MAX_SAFE_INTEGER): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0 || Number(value) > max) invalid(`${name} must be a bounded nonnegative integer`)
  return value as number
}
function string(value: unknown, name: string, max: number): string {
  if (typeof value !== 'string' || value.length > max) invalid(`${name} must be text of at most ${max} characters`)
  return value
}
function nullableString(value: unknown, name: string, max = 1024): string | null {
  return value === null ? null : string(value, name, max)
}
function member<T extends string>(value: unknown, choices: readonly T[], name: string): T {
  if (!choices.includes(value as T)) invalid(`unsupported ${name}`)
  return value as T
}

export function parseCapture(text: string): CaptureFile {
  if (text.length > MAX_CAPTURE_FILE_BYTES || encoder.encode(text).length > MAX_CAPTURE_FILE_BYTES) {
    invalid('file exceeds 16 MiB')
  }
  let value: unknown
  try { value = JSON.parse(text) } catch { invalid('file is not valid JSON') }
  const root = object(value, ['format', 'version', 'source', 'exportedAt', 'observationStartedAt', 'loss', 'events'], 'document')
  if (root.format !== 'legilimens-capture' || root.version !== 1 || root.source !== 'retained-browser-events') {
    invalid('unsupported format or version')
  }
  const exportedAt = integer(root.exportedAt, 'exportedAt', 8640000000000000)
  const observationStartedAt = integer(root.observationStartedAt, 'observationStartedAt', 8640000000000000)
  const loss = object(root.loss, ['completeness', 'evictedEvents', 'evictedStreams', 'omittedStreamChunks', 'warning'], 'loss')
  if (loss.completeness !== 'not-confirmed') invalid('completeness cannot be asserted')
  if (!Array.isArray(root.events) || root.events.length > MAX_CAPTURE_EVENTS) invalid('at most 500 events are supported')
  const ids = new Set<string>()
  const events = root.events.map((raw, i): CaptureEvent => {
    const e = object(raw, ['id', 'type', 'direction', 'payload', 'payloadEncoding', 'rawSize', 'timestamp', 'latency', 'sessionId', 'streamId', 'target', 'flag'], `event ${i}`)
    const id = string(e.id, 'event id', 128)
    if (!id || ids.has(id)) invalid('empty or duplicate event id')
    ids.add(id)
    const payload = string(e.payload, 'payload', MAX_EVENT_CHARS)
    const encoding = e.payloadEncoding === null ? null : member(e.payloadEncoding, ['utf8', 'base64'], 'payload encoding')
    if (encoding === 'base64') {
      // Validate canonical encoding, including unused padding bits, without decoding a large buffer.
      if (payload.length % 4 !== 0 || /[^A-Za-z0-9+/=]/.test(payload)) invalid('malformed Base64 payload')
      const padding = payload.endsWith('==') ? 2 : payload.endsWith('=') ? 1 : 0
      if (payload.slice(0, payload.length - padding).includes('=')) invalid('malformed Base64 padding')
      const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
      if (padding && (alphabet.indexOf(payload[payload.length - padding - 1]) & (padding === 2 ? 15 : 3))) invalid('noncanonical Base64 payload')
    }
    if (encoding === 'utf8') {
      // Unpaired UTF-16 surrogates cannot round-trip as UTF-8 bytes.
      for (let j = 0; j < payload.length; j++) {
        const code = payload.charCodeAt(j)
        if (code >= 0xd800 && code <= 0xdbff) {
          const next = payload.charCodeAt(++j)
          if (!(next >= 0xdc00 && next <= 0xdfff)) invalid('invalid UTF-8 text')
        } else if (code >= 0xdc00 && code <= 0xdfff) invalid('invalid UTF-8 text')
      }
    }
    return {
      id, type: member(e.type, ['datagram', 'stream', 'connection', 'attack'], 'event type'),
      direction: member(e.direction, ['incoming', 'outgoing'], 'direction'), payload, payloadEncoding: encoding,
      rawSize: integer(e.rawSize, 'rawSize'), timestamp: integer(e.timestamp, 'timestamp', 8640000000000000),
      latency: integer(e.latency, 'latency'), sessionId: nullableString(e.sessionId, 'sessionId', 128),
      streamId: nullableString(e.streamId, 'streamId', 128), target: nullableString(e.target, 'target'),
      flag: member(e.flag, ['normal', 'suspicious', 'tampered', 'replay'], 'flag'),
    }
  })
  return {
    format: 'legilimens-capture', version: 1, source: 'retained-browser-events', exportedAt, observationStartedAt,
    loss: { completeness: 'not-confirmed', evictedEvents: integer(loss.evictedEvents, 'evictedEvents'),
      evictedStreams: integer(loss.evictedStreams, 'evictedStreams'),
      omittedStreamChunks: integer(loss.omittedStreamChunks, 'omittedStreamChunks'),
      warning: nullableString(loss.warning, 'warning') }, events,
  }
}

interface CaptureSource {
  events: TrafficEvent[]
  observationStartedAt: number
  evictedEvents: number
  evictedStreams: number
  resourceWarning: string
  streams: Record<string, { omittedChunks?: number }>
}

export function exportCapture(source: CaptureSource, now = Date.now()): string {
  // Explicit allowlist: never serialize the store, auth state, configs, or private keys.
  const capture: CaptureFile = {
    format: 'legilimens-capture', version: 1, source: 'retained-browser-events', exportedAt: now,
    observationStartedAt: source.observationStartedAt,
    loss: { completeness: 'not-confirmed', evictedEvents: source.evictedEvents, evictedStreams: source.evictedStreams,
      omittedStreamChunks: Object.values(source.streams).reduce((n, s) => n + (s.omittedChunks ?? 0), 0),
      warning: source.resourceWarning || null },
    events: source.events.map((e) => ({ id: e.id, type: e.type, direction: e.direction, payload: e.payload,
      payloadEncoding: e.payloadEncoding ?? null, rawSize: e.rawSize, timestamp: e.timestamp, latency: e.latency,
      sessionId: e.sessionId ?? null, streamId: e.streamId ?? null, target: e.target || null, flag: e.flag ?? 'normal' })),
  }
  const text = JSON.stringify(capture)
  parseCapture(text)
  return text
}

export async function readCaptureFile(file: Pick<File, 'size' | 'arrayBuffer'>): Promise<CaptureFile> {
  if (file.size > MAX_CAPTURE_FILE_BYTES) invalid('file exceeds 16 MiB')
  const bytes = await file.arrayBuffer()
  if (bytes.byteLength > MAX_CAPTURE_FILE_BYTES) invalid('file exceeds 16 MiB')
  let text: string
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes) } catch { invalid('file is not UTF-8') }
  return parseCapture(text)
}

export function offlineEvents(capture: CaptureFile): TrafficEvent[] {
  return capture.events.map((e) => ({ ...e, sessionId: e.sessionId ?? undefined,
    streamId: e.streamId ?? undefined, target: e.target ?? undefined,
    payloadEncoding: e.payloadEncoding ?? undefined, replayable: false }))
}
