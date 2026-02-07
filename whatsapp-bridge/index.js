const express = require('express')
const fs = require('fs')
const path = require('path')
const { makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys')
const pino = require('pino')
const QRCode = require('qrcode')

const app = express()
app.set('trust proxy', 1)
app.use(express.json())

const SESSION_DIR = process.env.WHATSAPP_SESSION_DIR || '/data/whatsapp-session'
const RECONNECT_DELAY_MS = Number(process.env.WHATSAPP_RECONNECT_DELAY_MS || 3000)
const BRIDGE_TOKEN = process.env.WHATSAPP_BRIDGE_TOKEN || ''
const ALLOW_INSECURE = process.env.WHATSAPP_ALLOW_INSECURE === 'true'
const HISTORY_FILE = process.env.WHATSAPP_HISTORY_FILE || path.join(SESSION_DIR, 'history.json')
const HISTORY_MAX_MESSAGES = Number(process.env.WHATSAPP_HISTORY_MAX_MESSAGES || 200)
const HISTORY_MAX_CHATS = Number(process.env.WHATSAPP_HISTORY_MAX_CHATS || 200)
const HISTORY_FLUSH_MS = Number(process.env.WHATSAPP_HISTORY_FLUSH_MS || 5000)
const RATE_LIMIT_WINDOW_MS = Number(process.env.WHATSAPP_RATE_LIMIT_WINDOW_MS || 10000)
const RATE_LIMIT_MAX = Number(process.env.WHATSAPP_RATE_LIMIT_MAX || 60)

let sock = null
let qrData = null
let isReady = false
let isConnecting = false
let reconnectTimer = null
let lastDisconnectCode = null
let reconnectAttempts = 0
let messageHistory = {}
const rateState = new Map()

function clearReconnectTimer() {
  if (!reconnectTimer) return
  clearTimeout(reconnectTimer)
  reconnectTimer = null
}

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

function loadHistory() {
  try {
    if (!fs.existsSync(HISTORY_FILE)) return
    const raw = fs.readFileSync(HISTORY_FILE, 'utf8')
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') {
      messageHistory = parsed
    }
  } catch (err) {
    console.error('Failed to load WhatsApp history:', err)
  }
}

function saveHistory() {
  try {
    const dir = path.dirname(HISTORY_FILE)
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(HISTORY_FILE, JSON.stringify(messageHistory, null, 2))
  } catch (err) {
    console.error('Failed to persist WhatsApp history:', err)
  }
}

function scheduleHistoryFlush() {
  const timer = setInterval(saveHistory, HISTORY_FLUSH_MS)
  timer.unref()
}

function trimHistory() {
  const chatIds = Object.keys(messageHistory)
  if (chatIds.length <= HISTORY_MAX_CHATS) return
  const sorted = chatIds.sort((a, b) => {
    const aLast = messageHistory[a]?.slice(-1)[0]?.timestamp ?? 0
    const bLast = messageHistory[b]?.slice(-1)[0]?.timestamp ?? 0
    return aLast - bLast
  })
  const removeCount = sorted.length - HISTORY_MAX_CHATS
  sorted.slice(0, removeCount).forEach((chatId) => {
    delete messageHistory[chatId]
  })
}

function addHistoryEntry(chatId, entry) {
  if (!chatId || !entry) return
  const list = messageHistory[chatId] || []
  const last = list[list.length - 1]
  if (last?.id && entry.id && last.id === entry.id) return
  list.push(entry)
  if (list.length > HISTORY_MAX_MESSAGES) {
    list.splice(0, list.length - HISTORY_MAX_MESSAGES)
  }
  messageHistory[chatId] = list
  trimHistory()
}

function getClientKey(req) {
  const forwarded = req.get('X-Forwarded-For')
  if (forwarded) return forwarded.split(',')[0].trim()
  return req.ip || 'unknown'
}

function enforceRateLimit(req, res) {
  const now = Date.now()
  const key = getClientKey(req)
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

async function connectWhatsApp(force = false) {
  if (isConnecting && !force) return
  isConnecting = true
  clearReconnectTimer()

  try {
    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR)

    sock = makeWASocket({
      auth: state,
      printQRInTerminal: false,
      logger: pino({ level: 'silent' }),
    })

    sock.ev.on('creds.update', saveCreds)

    sock.ev.on('messages.upsert', (payload) => {
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
        addHistoryEntry(chatId, entry)
      })
    })

    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect, qr } = update

      if (qr) {
        qrData = qr
        console.log('QR code received')
      }

      if (connection === 'close') {
        isReady = false
        isConnecting = false
        const statusCode = lastDisconnect?.error?.output?.statusCode
        lastDisconnectCode = statusCode ?? null
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut
        console.log('Connection closed, status:', statusCode, 'reconnecting:', shouldReconnect)
        if (shouldReconnect) {
          reconnectAttempts += 1
          const baseDelay = RECONNECT_DELAY_MS * Math.pow(2, Math.min(reconnectAttempts, 6) - 1)
          const cappedDelay = Math.min(baseDelay, 60000)
          const jitter = Math.floor(cappedDelay * (0.2 + Math.random() * 0.2))
          const delay = cappedDelay + jitter
          clearReconnectTimer()
          reconnectTimer = setTimeout(() => {
            connectWhatsApp().catch((err) => {
              console.error('Reconnect failed:', err)
            })
          }, delay)
        }
      } else if (connection === 'open') {
        isReady = true
        isConnecting = false
        lastDisconnectCode = null
        qrData = null
        reconnectAttempts = 0
        console.log('WhatsApp connected')
      } else if (connection === 'connecting') {
        isConnecting = true
      }
    })
  } catch (err) {
    isConnecting = false
    console.error('Failed to initialize WhatsApp socket:', err)
    throw err
  }
}

function requireBridgeAuth(req, res) {
  if (ALLOW_INSECURE) return true
  if (req.get('X-WhatsApp-Bridge-Token') === BRIDGE_TOKEN) return true
  res.status(401).json({ error: 'Unauthorized' })
  return false
}

app.get('/health', (req, res) => {
  res.json({ status: 'healthy', version: '2.1.0' })
})

app.get('/status', (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (!enforceRateLimit(req, res)) return
  res.json({
    connected: isReady,
    connecting: isConnecting,
    qr_available: !!qrData,
    last_disconnect_code: lastDisconnectCode,
  })
})

app.get('/qr', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (!enforceRateLimit(req, res)) return
  if (isReady) return res.json({ status: 'connected', message: 'Already authenticated' })
  if (!qrData) return res.json({ status: 'waiting', message: 'No QR code yet, please wait' })
  const png = await QRCode.toBuffer(qrData)
  res.set('Cache-Control', 'no-store, max-age=0')
  res.set('Pragma', 'no-cache')
  res.set('Expires', '0')
  res.type('image/png').send(png)
})

app.post('/start', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (!enforceRateLimit(req, res)) return
  if (isReady) return res.json({ started: true, connected: true, message: 'Already connected' })
  if (isConnecting) return res.json({ started: true, connected: false, message: 'Connection already in progress' })
  try {
    await connectWhatsApp(true)
    res.json({ started: true, connected: isReady })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.post('/send', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (!enforceRateLimit(req, res)) return
  if (!isReady) return res.status(503).json({ error: 'WhatsApp not connected' })
  const { to, message } = req.body || {}
  if (!to || !message) {
    return res.status(400).json({ error: "Both 'to' and 'message' are required" })
  }
  try {
    const jid = normalizeChatId(to)
    if (!jid) return res.status(400).json({ error: 'Invalid recipient format' })
    await sock.sendMessage(jid, { text: message })
    res.json({ success: true, to, message })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.post('/messages', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (!enforceRateLimit(req, res)) return
  if (!isReady) return res.status(503).json({ error: 'WhatsApp not connected' })
  const { chat_id, limit } = req.body || {}
  const chatId = normalizeChatId(chat_id)
  if (!chatId) return res.status(400).json({ error: "'chat_id' is required" })
  const stored = messageHistory[chatId] || []
  const requested = Number(limit)
  const safeLimit = Number.isFinite(requested) && requested > 0 ? requested : 20
  const size = Math.min(safeLimit, stored.length)
  const messages = stored.slice(-size)
  res.json({ chat_id: chatId, count: messages.length, messages })
})

const PORT = process.env.PORT || 3000

if (!ALLOW_INSECURE && !BRIDGE_TOKEN) {
  console.error('WHATSAPP_BRIDGE_TOKEN is required. Set WHATSAPP_ALLOW_INSECURE=true to override.')
  process.exit(1)
}

fs.mkdirSync(SESSION_DIR, { recursive: true })
loadHistory()
scheduleHistoryFlush()

process.on('SIGTERM', () => {
  saveHistory()
  process.exit(0)
})

app.listen(PORT, () => {
  console.log(`WhatsApp bridge listening on port ${PORT}`)
  connectWhatsApp().catch((err) => {
    console.error('Initial connect failed:', err)
  })
})
