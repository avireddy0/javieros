const express = require('express')
const { makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys')
const pino = require('pino')
const QRCode = require('qrcode')

const app = express()
app.use(express.json())

let sock = null
let qrData = null
let isReady = false

async function connectWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState('/data/whatsapp-session')

  sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
    logger: pino({ level: 'silent' }),
  })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update

    if (qr) {
      qrData = qr
      console.log('QR code received')
    }

    if (connection === 'close') {
      isReady = false
      const statusCode = lastDisconnect?.error?.output?.statusCode
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut
      console.log('Connection closed, status:', statusCode, 'reconnecting:', shouldReconnect)
      if (shouldReconnect) {
        setTimeout(connectWhatsApp, 3000)
      }
    } else if (connection === 'open') {
      isReady = true
      qrData = null
      console.log('WhatsApp connected')
    }
  })
}

function requireBridgeAuth(req, res) {
  const token = process.env.WHATSAPP_BRIDGE_TOKEN
  if (!token) return true
  if (req.get('X-WhatsApp-Bridge-Token') === token) return true
  res.status(401).json({ error: 'Unauthorized' })
  return false
}

app.get('/health', (req, res) => {
  res.json({ status: 'healthy', version: '2.0.0' })
})

app.get('/status', (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  res.json({
    connected: isReady,
    qr_available: !!qrData,
  })
})

app.get('/qr', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (isReady) return res.json({ status: 'connected', message: 'Already authenticated' })
  if (!qrData) return res.json({ status: 'waiting', message: 'No QR code yet, please wait' })
  const png = await QRCode.toBuffer(qrData)
  res.type('image/png').send(png)
})

app.post('/send', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (!isReady) return res.status(503).json({ error: 'WhatsApp not connected' })
  const { to, message } = req.body
  try {
    const jid = to.replace(/[^0-9]/g, '') + '@s.whatsapp.net'
    await sock.sendMessage(jid, { text: message })
    res.json({ success: true, to, message })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.post('/messages', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (!isReady) return res.status(503).json({ error: 'WhatsApp not connected' })
  res.json({ error: 'Message history not supported with Baileys - use message webhooks instead' })
})

const PORT = process.env.PORT || 3000
app.listen(PORT, () => {
  console.log(`WhatsApp bridge listening on port ${PORT}`)
  connectWhatsApp()
})
