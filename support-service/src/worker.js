const REPO = 'ke7wil-bridge/allscan-reimagined'
export const LABELS = { bug: ['bug', 'asr-report'], question: ['question', 'asr-support'], feature: ['enhancement', 'asr-feedback'] }
const CONTENT_LIMIT = 30000
const SCREENSHOT_LIMIT = 2 * 1024 * 1024
const SCREENSHOT_COUNT_LIMIT = 3
const ATTACHMENT_KEY = /^support\/\d{4}-\d{2}-\d{2}\/[0-9a-f-]{36}\.(png|jpg|webp)$/i

export function redact(value = '') {
  return String(value)
    .replace(/-----BEGIN [^-]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----[\s\S]*?-----END [^-]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----/gi, '[REDACTED PRIVATE KEY]')
    .replace(/(authorization\s*:\s*(?:bearer|basic)\s+)[^\s"'<>]+/gi, '$1[REDACTED]')
    .replace(/((?:["']?(?:api[_-]?key|api[_-]?token|access[_-]?token|refresh[_-]?token|github[_-]?token|tgif[_-]?(?:password|token)|password|passwd|secret|cookie|session|phpsessid)["']?\s*[:=]\s*)["']?)[^"'\s&;,}]+(["']?)/gi, '$1[REDACTED]$2')
}

function cors() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  }
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { ...cors(), 'Content-Type': 'application/json' } })
}

function b64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')
}

function concatBytes(...parts) {
  const length = parts.reduce((total, part) => total + part.length, 0)
  const result = new Uint8Array(length)
  let offset = 0
  for (const part of parts) {
    result.set(part, offset)
    offset += part.length
  }
  return result
}

function derLength(length) {
  if (length < 0x80) return Uint8Array.of(length)
  const bytes = []
  for (let remaining = length; remaining > 0; remaining >>= 8) bytes.unshift(remaining & 0xff)
  return Uint8Array.of(0x80 | bytes.length, ...bytes)
}

function der(tag, contents) {
  return concatBytes(Uint8Array.of(tag), derLength(contents.length), contents)
}

function pkcs1ToPkcs8(pkcs1) {
  const version = Uint8Array.of(0x02, 0x01, 0x00)
  const rsaAlgorithm = Uint8Array.of(0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00)
  return der(0x30, concatBytes(version, rsaAlgorithm, der(0x04, pkcs1)))
}

export async function importKey(pem) {
  const value = String(pem || '')
  const raw = Uint8Array.from(atob(value.replace(/-----[^-]+-----|\s/g, '')), (c) => c.charCodeAt(0))
  const pkcs8 = value.includes('BEGIN RSA PRIVATE KEY') ? pkcs1ToPkcs8(raw) : raw
  return crypto.subtle.importKey('pkcs8', pkcs8, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['sign'])
}

async function appJwt(env) {
  const now = Math.floor(Date.now() / 1000)
  const header = b64url(new TextEncoder().encode(JSON.stringify({ alg: 'RS256', typ: 'JWT' })))
  const payload = b64url(new TextEncoder().encode(JSON.stringify({ iat: now - 30, exp: now + 540, iss: env.GITHUB_APP_ID })))
  const input = `${header}.${payload}`
  const key = await importKey(env.GITHUB_APP_PRIVATE_KEY)
  return `${input}.${b64url(new Uint8Array(await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, new TextEncoder().encode(input))))}`
}

async function installationToken(env) {
  const jwt = await appJwt(env)
  const response = await fetch(`https://api.github.com/app/installations/${env.GITHUB_INSTALLATION_ID}/access_tokens`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${jwt}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'ASR-Support' },
  })
  if (!response.ok) throw new Error('GitHub App token request failed')
  return (await response.json()).token
}

function hex(bytes) {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export async function rateLimitIdentifier(ip, salt) {
  if (!salt) throw new Error('Rate-limit salt is not configured')
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(String(salt)),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  )
  const digest = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(String(ip || 'unknown')))
  return hex(new Uint8Array(digest))
}

async function rateLimit(request, env) {
  const identifier = await rateLimitIdentifier(request.headers.get('CF-Connecting-IP') || 'unknown', env.RATE_LIMIT_SALT)
  const hour = Math.floor(Date.now() / 3600000)
  const key = `rate:${identifier}:${hour}`
  const count = Number(await env.RATE_LIMIT.get(key) || 0)
  if (count >= 5) return false
  await env.RATE_LIMIT.put(key, String(count + 1), { expirationTtl: 3700 })
  return true
}

function decodeBase64(data) {
  if (typeof data !== 'string' || !data || data.length > Math.ceil(SCREENSHOT_LIMIT / 3) * 4 + 4) {
    throw new Error('Invalid screenshot data')
  }
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(data) || data.length % 4 === 1) throw new Error('Invalid screenshot data')
  const decoded = atob(data)
  const bytes = Uint8Array.from(decoded, (char) => char.charCodeAt(0))
  if (!bytes.length || bytes.byteLength > SCREENSHOT_LIMIT) throw new Error('Invalid screenshot size')
  return bytes
}

function hasPrefix(bytes, signature) {
  return bytes.length >= signature.length && signature.every((byte, index) => bytes[index] === byte)
}

function detectedImageType(bytes) {
  if (hasPrefix(bytes, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return 'image/png'
  if (hasPrefix(bytes, [0xff, 0xd8, 0xff])) return 'image/jpeg'
  if (
    hasPrefix(bytes, [0x52, 0x49, 0x46, 0x46]) &&
    bytes.length >= 12 &&
    bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50
  ) return 'image/webp'
  return ''
}

export function inspectScreenshot(shot) {
  if (!shot || !['image/png', 'image/jpeg', 'image/webp'].includes(shot.type)) throw new Error('Invalid screenshot')
  const bytes = decodeBase64(shot.data)
  const detectedType = detectedImageType(bytes)
  if (!detectedType || detectedType !== shot.type) throw new Error('Invalid screenshot format')
  const extension = detectedType === 'image/png' ? 'png' : detectedType === 'image/webp' ? 'webp' : 'jpg'
  return { bytes, contentType: detectedType, extension }
}

export function validate(body) {
  if (!body || !LABELS[body.kind]) return 'Invalid support type.'
  if (body.website) return 'Rejected.'
  if (typeof body.content !== 'string' || body.content.trim().length < 3) return 'Please provide a description.'
  if (body.content.length > CONTENT_LIMIT) return 'Submission is too large.'
  if (body.screenshots !== undefined && !Array.isArray(body.screenshots)) return 'Invalid screenshots.'
  if ((body.screenshots || []).length > SCREENSHOT_COUNT_LIMIT) return 'Too many screenshots.'
  try {
    for (const shot of body.screenshots || []) inspectScreenshot(shot)
  } catch {
    return 'Screenshots must be valid PNG, JPEG, or WebP images no larger than 2 MB each.'
  }
  return ''
}

async function cleanupScreenshots(keys, env) {
  await Promise.allSettled(keys.map((key) => env.ATTACHMENTS.delete(key)))
}

async function storeScreenshots(body, env) {
  const keys = []
  const urls = []
  try {
    if (!env.ATTACHMENT_BASE_URL) throw new Error('Attachment base URL is not configured')
    for (const shot of body.screenshots || []) {
      const { bytes, contentType, extension } = inspectScreenshot(shot)
      const key = `support/${new Date().toISOString().slice(0, 10)}/${crypto.randomUUID()}.${extension}`
      await env.ATTACHMENTS.put(key, bytes, { metadata: { contentType } })
      keys.push(key)
      const path = key.split('/').map(encodeURIComponent).join('/')
      urls.push(`${env.ATTACHMENT_BASE_URL.replace(/\/$/, '')}/${path}`)
    }
    return { keys, urls }
  } catch (error) {
    await cleanupScreenshots(keys, env)
    throw error
  }
}

function attachmentContentType(key) {
  if (key.endsWith('.png')) return 'image/png'
  if (key.endsWith('.jpg')) return 'image/jpeg'
  if (key.endsWith('.webp')) return 'image/webp'
  return 'application/octet-stream'
}

async function serveAttachment(request, env) {
  const url = new URL(request.url)
  let key
  try {
    key = decodeURIComponent(url.pathname.slice('/attachments/'.length))
  } catch {
    return new Response('Not found.', { status: 404, headers: { ...cors(), 'X-Content-Type-Options': 'nosniff' } })
  }
  if (!ATTACHMENT_KEY.test(key)) {
    return new Response('Not found.', { status: 404, headers: { ...cors(), 'X-Content-Type-Options': 'nosniff' } })
  }
  const object = await env.ATTACHMENTS.getWithMetadata(key, { type: 'arrayBuffer' })
  if (!object?.value) return new Response('Not found.', { status: 404, headers: { ...cors(), 'X-Content-Type-Options': 'nosniff' } })
  const contentType = attachmentContentType(key)
  if (object.metadata?.contentType && object.metadata.contentType !== contentType) {
    return new Response('Not found.', { status: 404, headers: { ...cors(), 'X-Content-Type-Options': 'nosniff' } })
  }
  const headers = new Headers(cors())
  headers.set('Content-Type', contentType)
  headers.set('X-Content-Type-Options', 'nosniff')
  headers.set('Cache-Control', 'public, max-age=31536000, immutable')
  return new Response(object.value, { headers })
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors() })
    const url = new URL(request.url)
    if (request.method === 'GET' && url.pathname.startsWith('/attachments/')) return serveAttachment(request, env)
    if (request.method !== 'POST') return json({ ok: false, error: 'POST required.' }, 405)

    try {
      if (!(await rateLimit(request, env))) return json({ ok: false, error: 'Too many submissions. Please try again later.' }, 429)
    } catch {
      return json({ ok: false, error: 'Support service is not configured correctly.' }, 500)
    }

    let body
    try {
      body = await request.json()
    } catch {
      return json({ ok: false, error: 'Invalid JSON.' }, 400)
    }
    const error = validate(body)
    if (error) return json({ ok: false, error }, 400)

    let content = redact(body.content)
    const labels = LABELS[body.kind]
    let uploaded
    try {
      uploaded = await storeScreenshots(body, env)
      if (uploaded.urls.length) {
        content += `\n\n## Screenshots\n${uploaded.urls.map((attachmentUrl, index) => `![Screenshot ${index + 1}](${attachmentUrl})`).join('\n\n')}`
      }
    } catch {
      return json({ ok: false, error: 'Screenshot upload failed.' }, 502)
    }

    const first = content.split('\n').find((line) => line.trim() && !line.startsWith('Type:') && !line.startsWith('Labels:')) || 'ASR Support'
    const prefix = body.kind === 'bug' ? 'Bug' : body.kind === 'question' ? 'Question' : 'Feature'
    const title = `[ASR ${prefix}] ${first.replace(/^#+\s*/, '').slice(0, 90)}`
    try {
      const token = await installationToken(env)
      const response = await fetch(`https://api.github.com/repos/${REPO}/issues`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'ASR-Support' },
        body: JSON.stringify({ title, body: content, labels }),
      })
      if (!response.ok) throw new Error('GitHub issue creation failed')
      const issue = await response.json()
      return json({ ok: true, issueNumber: issue.number, issueUrl: issue.html_url })
    } catch {
      await cleanupScreenshots(uploaded.keys, env)
      return json({ ok: false, error: 'Support service could not create the issue.' }, 502)
    }
  },
}
