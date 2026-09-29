// Minimal native-API reproduction, independent of the suite's receive pump/retries.
export async function probeEmptyDatagram(page, address, hash, readableType) {
  return page.evaluate(async ({ address, hash, readableType }) => {
    async function observe(promise, timeout = 3000) {
      let timer
      try {
        return await Promise.race([
          promise.then(({ value, done }) => ({ status: 'received', done, bytes: value ? Array.from(value) : null }),
            error => ({ status: 'error', message: error.message })),
          new Promise(resolve => { timer = setTimeout(() => resolve({ status: 'timeout' }), timeout) }),
        ])
      } finally { clearTimeout(timer) }
    }
    const wt = new WebTransport(`https://${address}/echo?case=minimal-empty`, {
      serverCertificateHashes: [{ algorithm: 'sha-256', value: Uint8Array.from(atob(hash), c => c.charCodeAt(0)) }],
      ...(readableType ? { datagramsReadableType: readableType } : {}),
    })
    wt.closed.catch(() => {})
    let readyTimer
    try {
      await Promise.race([wt.ready, new Promise((_, reject) => {
        readyTimer = setTimeout(() => reject(new Error('Minimal probe handshake timeout')), 8000)
      })])
      clearTimeout(readyTimer)
      let byteReadable = false
      try {
        const byob = wt.datagrams.readable.getReader({ mode: 'byob' })
        byteReadable = true
        byob.releaseLock()
      } catch { /* A default (non-byte) stream rejects BYOB readers. */ }
      const writer = wt.datagrams.writable.getWriter()
      const reader = wt.datagrams.readable.getReader()
      let read = reader.read()
      await writer.write(new Uint8Array([1, 2, 3]))
      const before = await observe(read)
      if (before.status !== 'received') return { before }
      read = reader.read()
      await writer.write(new Uint8Array(0))
      const empty = await observe(read)
      // Reuse the still-pending read after timeout. Never leave competing reads.
      if (empty.status === 'received') read = reader.read()
      await writer.write(new Uint8Array([4, 5, 6]))
      const after = await observe(read)
      return { byteReadable, before, empty, after }
    } finally {
      clearTimeout(readyTimer)
      wt.close()
    }
  }, { address, hash, readableType })
}
