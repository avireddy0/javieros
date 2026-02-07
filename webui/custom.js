(() => {
  if (window.__openWebuiWhatsappModal) return
  window.__openWebuiWhatsappModal = true

  const TOOL_NAME = 'WhatsApp Tools'
  const MODAL_ID = 'whatsapp-qr-modal'
  const stateMap = new WeakMap()

  function isWhatsAppRow(element) {
    let node = element
    for (let i = 0; i < 6 && node; i += 1) {
      if (node.textContent && node.textContent.includes(TOOL_NAME)) return node
      node = node.parentElement
    }
    return null
  }

  function resolveToggle(element) {
    if (!element) return null
    if (element.matches('input[type="checkbox"]')) return element
    const switchEl = element.closest('[role="switch"]')
    if (switchEl) return switchEl
    const label = element.closest('label')
    if (label) {
      const input = label.querySelector('input[type="checkbox"]')
      if (input) return input
    }
    return null
  }

  function readToggleState(toggle) {
    if (!toggle) return null
    if (toggle.matches('input[type="checkbox"]')) return Boolean(toggle.checked)
    const aria = toggle.getAttribute('aria-checked')
    if (aria === 'true') return true
    if (aria === 'false') return false
    return null
  }

  function ensureModal() {
    let modal = document.getElementById(MODAL_ID)
    if (modal) return modal

    modal = document.createElement('div')
    modal.id = MODAL_ID
    modal.style.cssText = [
      'position: fixed',
      'inset: 0',
      'background: rgba(9, 12, 16, 0.72)',
      'display: none',
      'align-items: center',
      'justify-content: center',
      'z-index: 9999',
    ].join(';')

    const panel = document.createElement('div')
    panel.style.cssText = [
      'width: min(520px, 90vw)',
      'background: #0f1419',
      'border-radius: 16px',
      'box-shadow: 0 20px 60px rgba(0,0,0,0.45)',
      'padding: 16px',
      'display: flex',
      'flex-direction: column',
      'gap: 12px',
    ].join(';')

    const header = document.createElement('div')
    header.style.cssText = [
      'display: flex',
      'align-items: center',
      'justify-content: space-between',
      'color: #f1f5f9',
      'font-size: 16px',
      'font-weight: 600',
    ].join(';')
    header.textContent = 'Connect WhatsApp'

    const closeBtn = document.createElement('button')
    closeBtn.type = 'button'
    closeBtn.textContent = 'Close'
    closeBtn.style.cssText = [
      'background: #1f2937',
      'border: 1px solid #334155',
      'color: #f8fafc',
      'border-radius: 8px',
      'padding: 6px 12px',
      'cursor: pointer',
    ].join(';')

    const iframe = document.createElement('iframe')
    iframe.style.cssText = [
      'width: 100%',
      'height: 520px',
      'border: none',
      'border-radius: 12px',
      'background: #0b0f14',
    ].join(';')

    const error = document.createElement('div')
    error.style.cssText = 'color: #fca5a5; font-size: 14px; display: none;'
    error.textContent = 'Unable to open WhatsApp QR. Please try again.'

    closeBtn.addEventListener('click', () => {
      modal.style.display = 'none'
      iframe.removeAttribute('src')
    })

    header.appendChild(closeBtn)
    panel.appendChild(header)
    panel.appendChild(error)
    panel.appendChild(iframe)
    modal.appendChild(panel)
    document.body.appendChild(modal)

    modal.addEventListener('click', (event) => {
      if (event.target === modal) closeBtn.click()
    })

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && modal.style.display === 'flex') closeBtn.click()
    })

    modal.__iframe = iframe
    modal.__error = error
    return modal
  }

  async function openModal() {
    const modal = ensureModal()
    modal.style.display = 'flex'
    modal.__error.style.display = 'none'
    modal.__iframe.removeAttribute('src')

    try {
      const response = await fetch('/api/v1/whatsapp/qr_session', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      })
      if (!response.ok) throw new Error('Unable to create QR session')
      const data = await response.json()
      if (!data?.modal_url) throw new Error('Invalid QR session response')
      modal.__iframe.src = data.modal_url
    } catch (err) {
      console.error(err)
      modal.__error.style.display = 'block'
    }
  }

  function handleToggleEvent(event) {
    const row = isWhatsAppRow(event.target)
    if (!row) return
    const toggle = resolveToggle(event.target) || row.querySelector('[role="switch"], input[type="checkbox"]')
    if (!toggle) return
    const current = readToggleState(toggle)
    if (current === null) return
    const previous = stateMap.get(toggle)
    stateMap.set(toggle, current)
    if (previous === false && current === true) {
      openModal()
    }
  }

  function primeToggles() {
    const toggles = document.querySelectorAll('[role="switch"], input[type="checkbox"]')
    toggles.forEach((toggle) => {
      if (stateMap.has(toggle)) return
      const row = isWhatsAppRow(toggle)
      if (!row) return
      const state = readToggleState(toggle)
      if (state !== null) stateMap.set(toggle, state)
    })
  }

  document.addEventListener('click', handleToggleEvent, true)
  document.addEventListener('change', handleToggleEvent, true)

  const observer = new MutationObserver(() => primeToggles())
  observer.observe(document.documentElement, { childList: true, subtree: true })
  window.addEventListener('load', primeToggles)
})()
