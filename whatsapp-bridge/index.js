const express = require('express')
const { makeWASocket, useMultiFileAuthState, DisconnectReason } = require('@whiskeysockets/baileys')
const pino = require('pino')
const QRCode = require('qrcode')

const app = express()
app.use(express.json())

let sock = null
let qrData = null
let isReady = false
let pairingCode = null
let currentPhoneNumber = null

async function connectWhatsApp(phoneNumber = null) {
  currentPhoneNumber = phoneNumber
  const { state, saveCreds } = await useMultiFileAuthState('/data/whatsapp-session')

  sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
    logger: pino({ level: 'silent' }),
  })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update

    if (qr) {
      qrData = qr
      console.log('QR code received')
      
      // If phone number provided, request pairing code instead of QR
      if (currentPhoneNumber && !pairingCode) {
        try {
          const code = await sock.requestPairingCode(currentPhoneNumber)
          pairingCode = code
          console.log('Pairing code generated:', code)
        } catch (err) {
          console.error('Failed to get pairing code:', err.message)
        }
      }
    }

    if (connection === 'close') {
      isReady = false
      pairingCode = null
      const statusCode = lastDisconnect?.error?.output?.statusCode
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut
      console.log('Connection closed, status:', statusCode, 'reconnecting:', shouldReconnect)
      if (shouldReconnect) {
        setTimeout(() => connectWhatsApp(currentPhoneNumber), 3000)
      }
    } else if (connection === 'open') {
      isReady = true
      qrData = null
      pairingCode = null
      currentPhoneNumber = null
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
  res.json({ status: 'healthy', version: '2.1.0' })
})

app.get('/status', (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  res.json({
    connected: isReady,
    qr_available: !!qrData,
    pairing_code: pairingCode || null,
  })
})

app.get('/qr', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (isReady) return res.json({ status: 'connected', message: 'Already authenticated' })
  if (!qrData) return res.json({ status: 'waiting', message: 'No QR code yet, please wait' })
  const png = await QRCode.toBuffer(qrData)
  res.type('image/png').send(png)
})

// Request pairing code instead of QR scan
app.post('/pair', async (req, res) => {
  if (!requireBridgeAuth(req, res)) return
  if (isReady) return res.json({ status: 'connected', message: 'Already authenticated' })
  
  const { phone } = req.body
  if (!phone) {
    return res.status(400).json({ 
      error: 'Phone number required', 
      example: '15551234567 (country code + number, no + or spaces)' 
    })
  }
  
  const cleanPhone = phone.replace(/[^0-9]/g, '')
  if (cleanPhone.length < 10) {
    return res.status(400).json({ error: 'Invalid phone number format' })
  }
  
  try {
    // Reset and reconnect with phone number to trigger pairing code
    pairingCode = null
    await connectWhatsApp(cleanPhone)
    
    // Wait for pairing code to be generated (up to 15 seconds)
    let attempts = 0
    while (!pairingCode && attempts < 15) {
      await new Promise(r => setTimeout(r, 1000))
      attempts++
    }
    
    if (pairingCode) {
      res.json({ 
        status: 'pairing', 
        code: pairingCode,
        instructions: [
          '1. Open WhatsApp on your phone',
          '2. Go to Settings > Linked Devices',
          '3. Tap "Link a Device"',
          '4. Tap "Link with phone number instead"',
          '5. Enter this code: ' + pairingCode
        ]
      })
    } else {
      res.status(500).json({ 
        error: 'Failed to generate pairing code',
        hint: 'Try again in a few seconds'
      })
    }
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
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
