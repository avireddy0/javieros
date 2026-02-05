const express = require('express')
const { Client, LocalAuth } = require('whatsapp-web.js')
const QRCode = require('qrcode')

const app = express()
app.use(express.json())

let qrData = null
let isReady = false

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: '/data/whatsapp-session' }),
  puppeteer: { headless: true, args: ['--no-sandbox', '--disable-setuid-sandbox'] },
})

function requireBridgeAuth(req, res) {
  const token = process.env.WHATSAPP_BRIDGE_TOKEN
  if (!token) return true
  if (req.get('X-WhatsApp-Bridge-Token') === token) return true
  res.status(401).json({ error: 'Unauthorized' })
  return false
}

client.on('qr', (qr) => {
  qrData = qr
  console.log('QR code received — scan at /qr')
})

client.on('ready', () => {
  isReady = true
  qrData = null
  console.log('WhatsApp client ready')
})

client.on('disconnected', () => {
  isReady = false
  console.log('WhatsApp client disconnected')
})

client.initialize()

app.get('/qr', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (isReady) return res.json({ status: 'connected', message: 'Already authenticated' })
  if (!qrData) return res.json({ status: 'waiting', message: 'No QR code yet, please wait' })
  const png = await QRCode.toBuffer(qrData)
  res.type('image/png').send(png)
})

app.get('/status', (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  res.json({
    connected: isReady,
    qr_available: !!qrData,
  })
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
