const express = require('express')
const { Client, LocalAuth } = require('whatsapp-web.js')
const QRCode = require('qrcode')

const app = express()
app.use(express.json())

let qrData = null
let isReady = false
let client = null
let initPromise = null

const PUPPETEER_ARGS = [
  '--no-sandbox',
  '--disable-setuid-sandbox',
  '--disable-dev-shm-usage',
  '--disable-gpu',
  '--disable-software-rasterizer',
  '--no-first-run',
  '--no-zygote',
  '--single-process',
  '--disable-accelerated-2d-canvas',
  '--disable-web-security',
  '--disable-features=IsolateOrigins',
  '--disable-site-isolation-trials',
  '--disable-extensions',
  '--disable-background-networking',
  '--disable-sync',
  '--disable-translate',
  '--metrics-recording-only',
  '--mute-audio',
  '--no-default-browser-check',
]

function createClient() {
  const c = new Client({
    authStrategy: new LocalAuth({ dataPath: '/data/whatsapp-session' }),
    puppeteer: {
      headless: true,
      args: PUPPETEER_ARGS,
      timeout: 60000,
    },
  })

  c.on('qr', (qr) => {
    qrData = qr
    console.log('QR code received — scan at /qr')
  })

  c.on('ready', () => {
    isReady = true
    qrData = null
    console.log('WhatsApp client ready')
  })

  c.on('disconnected', () => {
    isReady = false
    console.log('WhatsApp client disconnected')
  })

  c.on('auth_failure', (msg) => {
    console.error('Auth failure:', msg)
  })

  return c
}

async function initializeClient() {
  if (client && isReady) return client
  if (initPromise) return initPromise

  initPromise = (async () => {
    console.log('Initializing WhatsApp client...')
    client = createClient()
    try {
      await client.initialize()
      return client
    } catch (err) {
      console.error('Client init failed:', err.message)
      client = null
      initPromise = null
      throw err
    }
  })()

  return initPromise
}

function requireBridgeAuth(req, res) {
  const token = process.env.WHATSAPP_BRIDGE_TOKEN
  if (!token) return true
  if (req.get('X-WhatsApp-Bridge-Token') === token) return true
  res.status(401).json({ error: 'Unauthorized' })
  return false
}

// Health check - doesn't require client
app.get('/health', (req, res) => {
  res.json({ status: 'healthy', version: '2.0.0' })
})

// Status - doesn't initialize client
app.get('/status', (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  res.json({
    connected: isReady,
    qr_available: !!qrData,
    client_initialized: !!client,
  })
})

// Initialize and get QR
app.get('/qr', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return

  if (isReady) {
    return res.json({ status: 'connected', message: 'Already authenticated' })
  }

  try {
    // Start initialization if not started
    if (!client && !initPromise) {
      initializeClient().catch(err => console.error('Background init failed:', err.message))
    }

    // Wait a bit for QR
    if (!qrData) {
      await new Promise(r => setTimeout(r, 5000))
    }

    if (!qrData) {
      return res.json({ status: 'waiting', message: 'Initializing, please wait and refresh...' })
    }

    const png = await QRCode.toBuffer(qrData)
    res.type('image/png').send(png)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Start client manually
app.post('/start', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return

  try {
    await initializeClient()
    res.json({ status: 'initializing', message: 'Client starting...' })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.post('/send', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (!isReady) return res.status(503).json({ error: 'WhatsApp not connected' })
  const { to, message } = req.body
  try {
    const chatId = to.replace(/[^0-9]/g, '') + '@c.us'
    await client.sendMessage(chatId, message)
    res.json({ success: true, to, message })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.post('/messages', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (!isReady) return res.status(503).json({ error: 'WhatsApp not connected' })
  const { chat_id, limit = 20 } = req.body
  try {
    const chatId = chat_id.replace(/[^0-9]/g, '') + '@c.us'
    const chat = await client.getChatById(chatId)
    const messages = await chat.fetchMessages({ limit })
    const result = messages.map((m) => ({
      from: m.from,
      body: m.body,
      timestamp: m.timestamp,
      fromMe: m.fromMe,
    }))
    res.json(result)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

const PORT = process.env.PORT || 3000
app.listen(PORT, () => console.log(`WhatsApp bridge listening on port ${PORT}`))
