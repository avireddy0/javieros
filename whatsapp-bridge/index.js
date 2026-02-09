const express = require('express')
const fs = require('fs')
const path = require('path')
const { makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys')
const pino = require('pino')
const QRCode = require('qrcode')
const crypto = require('crypto')

const app = express()
app.set('trust proxy', 1)
app.use(express.json())

// Configuration
const SESSIONS_BASE_DIR = process.env.WHATSAPP_SESSIONS_DIR || '/data/whatsapp-sessions'
const RECONNECT_DELAY_MS = Number(process.env.WHATSAPP_RECONNECT_DELAY_MS || 3000)
const BRIDGE_TOKEN = process.env.WHATSAPP_BRIDGE_TOKEN || ''
const ADMIN_TOKEN = process.env.WHATSAPP_ADMIN_TOKEN || ''
const HISTORY_MAX_MESSAGES = Number(process.env.WHATSAPP_HISTORY_MAX_MESSAGES || 200)
const HISTORY_MAX_CHATS = Number(process.env.WHATSAPP_HISTORY_MAX_CHATS || 200)
const HISTORY_FLUSH_MS = Number(process.env.WHATSAPP_HISTORY_FLUSH_MS || 5000)
const RATE_LIMIT_WINDOW_MS = Number(process.env.WHATSAPP_RATE_LIMIT_WINDOW_MS || 10000)
const RATE_LIMIT_MAX = Number(process.env.WHATSAPP_RATE_LIMIT_MAX || 60)
const MAX_SESSIONS = Number(process.env.WHATSAPP_MAX_SESSIONS || 100)
const SESSION_IDLE_TIMEOUT_MS = Number(process.env.WHATSAPP_SESSION_IDLE_TIMEOUT_MS || 30 * 24 * 60 * 60 * 1000) // 30 days

// UserSession class
class UserSession {
  constructor(userId) {
    this.userId = userId
    this.sock = null
    this.qrData = null
    this.isReady = false
    this.isConnecting = false
    this.reconnectTimer = null
    this.lastDisconnectCode = null
    this.reconnectAttempts = 0
    this.messageHistory = {}
    this.lastActivity = Date.now()
    this.historyFlushTimer = null
  }

  getSessionDir() {
    return path.join(SESSIONS_BASE_DIR, this.userId)
  }

  getHistoryFile() {
    return path.join(this.getSessionDir(), 'history.json')
  }

  clearReconnectTimer() {
    if (!this.reconnectTimer) return
    clearTimeout(this.reconnectTimer)
    this.reconnectTimer = null
  }

  loadHistory() {
    try {
      const historyFile = this.getHistoryFile()
      if (!fs.existsSync(historyFile)) return
      const raw = fs.readFileSync(historyFile, 'utf8')
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === 'object') {
        this.messageHistory = parsed
      }
    } catch (err) {
      console.error(`[${this.userId}] Failed to load history:`, err)
    }
  }

  saveHistory() {
    try {
      const sessionDir = this.getSessionDir()
      fs.mkdirSync(sessionDir, { recursive: true })
      const historyFile = this.getHistoryFile()
      fs.writeFileSync(historyFile, JSON.stringify(this.messageHistory, null, 2))
    } catch (err) {
      console.error(`[${this.userId}] Failed to persist history:`, err)
    }
  }

  scheduleHistoryFlush() {
    if (this.historyFlushTimer) {
      clearInterval(this.historyFlushTimer)
    }
    this.historyFlushTimer = setInterval(() => this.saveHistory(), HISTORY_FLUSH_MS)
    this.historyFlushTimer.unref()
  }

  trimHistory() {
    const chatIds = Object.keys(this.messageHistory)
    if (chatIds.length <= HISTORY_MAX_CHATS) return
    const sorted = chatIds.sort((a, b) => {
      const aLast = this.messageHistory[a]?.slice(-1)[0]?.timestamp ?? 0
      const bLast = this.messageHistory[b]?.slice(-1)[0]?.timestamp ?? 0
      return aLast - bLast
    })
    const removeCount = sorted.length - HISTORY_MAX_CHATS
    sorted.slice(0, removeCount).forEach((chatId) => {
      delete this.messageHistory[chatId]
    })
  }

  addHistoryEntry(chatId, entry) {
    if (!chatId || !entry) return
    const list = this.messageHistory[chatId] || []
    const last = list[list.length - 1]
    if (last?.id && entry.id && last.id === entry.id) return
    list.push(entry)
    if (list.length > HISTORY_MAX_MESSAGES) {
      list.splice(0, list.length - HISTORY_MAX_MESSAGES)
    }
    this.messageHistory[chatId] = list
    this.trimHistory()
  }

  updateActivity() {
    this.lastActivity = Date.now()
  }

  async connect(force = false) {
    if (this.isConnecting && !force) return
    this.isConnecting = true
    this.clearReconnectTimer()

    try {
      const sessionDir = this.getSessionDir()
      fs.mkdirSync(sessionDir, { recursive: true })
      const { state, saveCreds } = await useMultiFileAuthState(sessionDir)

      this.sock = makeWASocket({
        auth: state,
        printQRInTerminal: false,
        logger: pino({ level: 'silent' }),
      })

      this.sock.ev.on('creds.update', saveCreds)

      this.sock.ev.on('messages.upsert', (payload) => {
        const messages = payload?.messages || []
        messages.forEach((msg) => {
          if (!msg?.message) return
          const chatId = msg.key?.remoteJid
          if (!chatId) return
          const text = extractMessageText(msg.message)
          const entry = {
            id: msg.key?.id || null,
            chat_id: chatId,
            timestamp: normalizeTimestamp(msg.messageTimestamp),
            from_me: Boolean(msg.key?.fromMe),
            sender: msg.key?.participant || chatId,
            text,
          }
          this.addHistoryEntry(chatId, entry)
        })
      })

      this.sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update

        if (qr) {
          this.qrData = qr
          console.log(`[${this.userId}] QR code received`)
        }

        if (connection === 'close') {
          this.isReady = false
          this.isConnecting = false
          const statusCode = lastDisconnect?.error?.output?.statusCode
          this.lastDisconnectCode = statusCode ?? null
          const shouldReconnect = statusCode !== DisconnectReason.loggedOut
          console.log(`[${this.userId}] Connection closed, status:`, statusCode, 'reconnecting:', shouldReconnect)
          if (shouldReconnect) {
            this.reconnectAttempts += 1
            const baseDelay = RECONNECT_DELAY_MS * Math.pow(2, Math.min(this.reconnectAttempts, 6) - 1)
            const cappedDelay = Math.min(baseDelay, 60000)
            const jitter = Math.floor(cappedDelay * (0.2 + Math.random() * 0.2))
            const delay = cappedDelay + jitter
            this.clearReconnectTimer()
            this.reconnectTimer = setTimeout(() => {
              this.connect().catch((err) => {
                console.error(`[${this.userId}] Reconnect failed:`, err)
              })
            }, delay)
          }
        } else if (connection === 'open') {
          this.isReady = true
          this.isConnecting = false
          this.lastDisconnectCode = null
          this.qrData = null
          this.reconnectAttempts = 0
          this.updateActivity()
          console.log(`[${this.userId}] WhatsApp connected`)
        } else if (connection === 'connecting') {
          this.isConnecting = true
        }
      })
    } catch (err) {
      this.isConnecting = false
      console.error(`[${this.userId}] Failed to initialize WhatsApp socket:`, err)
      throw err
    }
  }

  async disconnect() {
    this.clearReconnectTimer()
    if (this.historyFlushTimer) {
      clearInterval(this.historyFlushTimer)
      this.historyFlushTimer = null
    }
    this.saveHistory()
    if (this.sock) {
      try {
        await this.sock.logout()
      } catch (err) {
        console.error(`[${this.userId}] Error during logout:`, err)
      }
      this.sock = null
    }
    this.isReady = false
    this.isConnecting = false
  }

  async cleanup() {
    await this.disconnect()
    try {
      const sessionDir = this.getSessionDir()
      if (fs.existsSync(sessionDir)) {
        fs.rmSync(sessionDir, { recursive: true, force: true })
        console.log(`[${this.userId}] Session directory cleaned up`)
      }
    } catch (err) {
      console.error(`[${this.userId}] Failed to cleanup session directory:`, err)
    }
  }
}

// Global session management
const sessions = new Map()
const rateState = new Map()

// Utility functions
function normalizeTimestamp(timestamp) {
  if (!timestamp) return Date.now()
  const numeric = Number(timestamp)
  if (Number.isNaN(numeric)) return Date.now()
  return numeric > 1e12 ? numeric : numeric * 1000
}

function unwrapMessage(message) {
  if (!message) return message
  if (message.ephemeralMessage?.message) return unwrapMessage(message.ephemeralMessage.message)
  if (message.viewOnceMessage?.message) return unwrapMessage(message.viewOnceMessage.message)
  if (message.viewOnceMessageV2?.message) return unwrapMessage(message.viewOnceMessageV2.message)
  return message
}

function extractMessageText(message) {
  const unwrapped = unwrapMessage(message)
  if (!unwrapped) return ''
  if (unwrapped.conversation) return unwrapped.conversation
  if (unwrapped.extendedTextMessage?.text) return unwrapped.extendedTextMessage.text
  if (unwrapped.imageMessage?.caption) return unwrapped.imageMessage.caption
  if (unwrapped.videoMessage?.caption) return unwrapped.videoMessage.caption
  if (unwrapped.documentMessage?.caption) return unwrapped.documentMessage.caption
  if (unwrapped.buttonsResponseMessage?.selectedDisplayText) {
    return unwrapped.buttonsResponseMessage.selectedDisplayText
  }
  if (unwrapped.listResponseMessage?.title) return unwrapped.listResponseMessage.title
  if (unwrapped.templateButtonReplyMessage?.selectedDisplayText) {
    return unwrapped.templateButtonReplyMessage.selectedDisplayText
  }
  const messageType = Object.keys(unwrapped)[0]
  return messageType ? `[${messageType}]` : ''
}

function normalizeChatId(input) {
  if (!input) return null
  const trimmed = String(input).trim()
  if (!trimmed) return null
  if (trimmed.includes('@')) return trimmed
  const digits = trimmed.replace(/\D/g, '')
  if (!digits) return null
  return `${digits}@s.whatsapp.net`
}

function sanitizeUserId(userId) {
  if (!userId || typeof userId !== 'string') return null
  const sanitized = userId.trim().replace(/[^a-zA-Z0-9_-]/g, '')
  if (sanitized.length === 0 || sanitized.length > 64) return null
  return sanitized
}

function getClientKey(req, userId) {
  return `${userId}:${req.ip || 'unknown'}`
}

function enforceRateLimit(req, res, userId) {
  const now = Date.now()
  const key = getClientKey(req, userId)
  const entry = rateState.get(key) || { count: 0, resetAt: now + RATE_LIMIT_WINDOW_MS }
  if (now > entry.resetAt) {
    entry.count = 0
    entry.resetAt = now + RATE_LIMIT_WINDOW_MS
  }
  entry.count += 1
  rateState.set(key, entry)
  if (entry.count > RATE_LIMIT_MAX) {
    res.status(429).json({ error: 'Rate limit exceeded. Please retry shortly.' })
    return false
  }
  return true
}

function requireBridgeAuth(req, res) {
  const provided = req.get('X-WhatsApp-Bridge-Token') || ''
  if (provided.length === BRIDGE_TOKEN.length && crypto.timingSafeEqual(Buffer.from(provided), Buffer.from(BRIDGE_TOKEN))) {
    return true
  }
  res.status(401).json({ error: 'Unauthorized' })
  return false
}

function requireAdminAuth(req, res) {
  if (!ADMIN_TOKEN) {
    res.status(403).json({ error: 'Admin operations disabled (WHATSAPP_ADMIN_TOKEN not set)' })
    return false
  }
  const provided = req.get('X-WhatsApp-Admin-Token') || ''
  if (provided.length === ADMIN_TOKEN.length && crypto.timingSafeEqual(Buffer.from(provided), Buffer.from(ADMIN_TOKEN))) {
    return true
  }
  res.status(401).json({ error: 'Unauthorized' })
  return false
}

function getUserId(req, res) {
  const userId = req.get('X-User-ID')
  const sanitized = sanitizeUserId(userId)
  if (!sanitized) {
    res.status(400).json({ error: 'Missing or invalid X-User-ID header' })
    return null
  }
  return sanitized
}

function getOrCreateSession(userId) {
  if (!sessions.has(userId)) {
    if (sessions.size >= MAX_SESSIONS) {
      throw new Error(`Maximum session limit (${MAX_SESSIONS}) reached`)
    }
    const session = new UserSession(userId)
    session.loadHistory()
    session.scheduleHistoryFlush()
    sessions.set(userId, session)
    console.log(`[${userId}] New session created (total: ${sessions.size}/${MAX_SESSIONS})`)
  }
  const session = sessions.get(userId)
  session.updateActivity()
  return session
}

// Periodic cleanup of idle sessions
function cleanupIdleSessions() {
  const now = Date.now()
  const toRemove = []

  for (const [userId, session] of sessions.entries()) {
    const idleTime = now - session.lastActivity
    if (idleTime > SESSION_IDLE_TIMEOUT_MS) {
      toRemove.push(userId)
    }
  }

  for (const userId of toRemove) {
    const session = sessions.get(userId)
    console.log(`[${userId}] Cleaning up idle session (idle for ${Math.floor((now - session.lastActivity) / (24 * 60 * 60 * 1000))} days)`)
    session.disconnect().catch((err) => {
      console.error(`[${userId}] Error during idle cleanup:`, err)
    })
    sessions.delete(userId)
  }

  if (toRemove.length > 0) {
    console.log(`Cleaned up ${toRemove.length} idle session(s). Active sessions: ${sessions.size}`)
  }
}

// Schedule idle session cleanup every 6 hours
const cleanupTimer = setInterval(cleanupIdleSessions, 6 * 60 * 60 * 1000)
cleanupTimer.unref()

// Routes
app.get('/health', (req, res) => {
  res.json({
    status: 'healthy',
    version: '3.0.0',
    active_sessions: sessions.size,
    max_sessions: MAX_SESSIONS
  })
})

app.get('/status', (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  const userId = getUserId(req, res)
  if (!userId) return
  if (!enforceRateLimit(req, res, userId)) return

  const session = sessions.get(userId)
  if (!session) {
    return res.json({
      connected: false,
      connecting: false,
      qr_available: false,
      last_disconnect_code: null,
      session_exists: false
    })
  }

  res.json({
    connected: session.isReady,
    connecting: session.isConnecting,
    qr_available: !!session.qrData,
    last_disconnect_code: session.lastDisconnectCode,
    session_exists: true
  })
})

app.get('/qr', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  const userId = getUserId(req, res)
  if (!userId) return
  if (!enforceRateLimit(req, res, userId)) return

  const session = getOrCreateSession(userId)
  if (session.isReady) {
    return res.json({ status: 'connected', message: 'Already authenticated' })
  }
  if (!session.qrData) {
    return res.json({ status: 'waiting', message: 'No QR code yet, please wait' })
  }

  const png = await QRCode.toBuffer(session.qrData)
  res.set('Cache-Control', 'no-store, max-age=0')
  res.set('Pragma', 'no-cache')
  res.set('Expires', '0')
  res.type('image/png').send(png)
})

app.post('/start', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  const userId = getUserId(req, res)
  if (!userId) return
  if (!enforceRateLimit(req, res, userId)) return

  try {
    const session = getOrCreateSession(userId)
    if (session.isReady) {
      return res.json({ started: true, connected: true, message: 'Already connected' })
    }
    if (session.isConnecting) {
      return res.json({ started: true, connected: false, message: 'Connection already in progress' })
    }
    await session.connect(true)
    res.json({ started: true, connected: session.isReady })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.post('/send', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  const userId = getUserId(req, res)
  if (!userId) return
  if (!enforceRateLimit(req, res, userId)) return

  const session = sessions.get(userId)
  if (!session || !session.isReady) {
    return res.status(503).json({ error: 'WhatsApp not connected' })
  }

  const { to, message } = req.body || {}
  if (!to || !message) {
    return res.status(400).json({ error: "Both 'to' and 'message' are required" })
  }

  try {
    const jid = normalizeChatId(to)
    if (!jid) return res.status(400).json({ error: 'Invalid recipient format' })
    await session.sock.sendMessage(jid, { text: message })
    session.updateActivity()
    res.json({ success: true, to, message })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.post('/messages', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  const userId = getUserId(req, res)
  if (!userId) return
  if (!enforceRateLimit(req, res, userId)) return

  const session = sessions.get(userId)
  if (!session || !session.isReady) {
    return res.status(503).json({ error: 'WhatsApp not connected' })
  }

  const { chat_id, limit } = req.body || {}
  const chatId = normalizeChatId(chat_id)
  if (!chatId) return res.status(400).json({ error: "'chat_id' is required" })

  const stored = session.messageHistory[chatId] || []
  const requested = Number(limit)
  const safeLimit = Number.isFinite(requested) && requested > 0 ? requested : 20
  const size = Math.min(safeLimit, stored.length)
  const messages = stored.slice(-size)
  session.updateActivity()
  res.json({ chat_id: chatId, count: messages.length, messages })
})

// User endpoint: unlink own WhatsApp session
app.delete('/session', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  const userId = getUserId(req, res)
  if (!userId) return
  if (!enforceRateLimit(req, res, userId)) return

  const session = sessions.get(userId)
  if (!session) {
    return res.json({ success: true, message: 'No active session to unlink' })
  }

  try {
    await session.cleanup()
    sessions.delete(userId)
    res.json({ success: true, message: 'Session unlinked and data removed' })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Admin endpoint: list all sessions
app.get('/sessions', (req, res) => {
  if (!requireAdminAuth(req, res)) return

  const sessionList = Array.from(sessions.entries()).map(([userId, session]) => ({
    user_id: userId,
    connected: session.isReady,
    connecting: session.isConnecting,
    last_activity: session.lastActivity,
    idle_time_ms: Date.now() - session.lastActivity,
    reconnect_attempts: session.reconnectAttempts
  }))

  res.json({
    total_sessions: sessions.size,
    max_sessions: MAX_SESSIONS,
    sessions: sessionList
  })
})

// Admin endpoint: delete specific user session
app.delete('/sessions/:userId', async (req, res) => {
  if (!requireAdminAuth(req, res)) return

  const userId = sanitizeUserId(req.params.userId)
  if (!userId) {
    return res.status(400).json({ error: 'Invalid user ID' })
  }

  const session = sessions.get(userId)
  if (!session) {
    return res.status(404).json({ error: 'Session not found' })
  }

  try {
    await session.cleanup()
    sessions.delete(userId)
    res.json({ success: true, message: `Session for user ${userId} removed` })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Startup
const PORT = process.env.PORT || 3000

if (!BRIDGE_TOKEN) {
  console.error('FATAL: WHATSAPP_BRIDGE_TOKEN must be set and non-empty')
  process.exit(1)
}

fs.mkdirSync(SESSIONS_BASE_DIR, { recursive: true })

process.on('SIGTERM', () => {
  console.log('SIGTERM received, saving all sessions...')
  for (const [userId, session] of sessions.entries()) {
    session.saveHistory()
  }
  process.exit(0)
})

app.listen(PORT, () => {
  console.log(`WhatsApp multi-user bridge v3.0.0 listening on port ${PORT}`)
  console.log(`Max concurrent sessions: ${MAX_SESSIONS}`)
  console.log(`Session idle timeout: ${SESSION_IDLE_TIMEOUT_MS / (24 * 60 * 60 * 1000)} days`)
  console.log(`Sessions directory: ${SESSIONS_BASE_DIR}`)
})
