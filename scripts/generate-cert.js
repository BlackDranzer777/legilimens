import selfsigned from 'selfsigned'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'
import { createHash, X509Certificate } from 'crypto'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const certsDir = path.join(__dirname, '../server/certs')

console.log('[legilimens] Generating self-signed certificate...')

const attrs = [
  { name: 'commonName', value: 'localhost' },
  { name: 'organizationName', value: 'Legilimens Security Tool' },
]

// WebTransport serverCertificateHashes requirements:
// - Validity period MUST be <= 14 days
// - Certificate MUST be a leaf/end-entity cert (NOT a CA cert)
// - Key algorithm: RSA >= 2048 bits or ECDSA P-256/P-384
const pems = selfsigned.generate(attrs, {
  keySize: 2048,
  days: 13, // 13 days to stay safely under the 14-day limit
  algorithm: 'sha256',
  extensions: [
    {
      name: 'subjectAltName',
      altNames: [
        { type: 2, value: 'localhost' },
        { type: 7, ip: '127.0.0.1' },
      ],
    },
    // basicConstraints WITHOUT cA:true — this MUST be a leaf cert, not a CA
    { name: 'basicConstraints', cA: false, critical: true },
    // keyUsage for a TLS server cert — NO keyCertSign
    {
      name: 'keyUsage',
      critical: true,
      digitalSignature: true,
      keyEncipherment: true,
    },
    // Extended key usage: server authentication
    { name: 'extKeyUsage', serverAuth: true },
  ],
})

if (!fs.existsSync(certsDir)) {
  fs.mkdirSync(certsDir, { recursive: true })
}

const certPath = path.join(certsDir, 'cert.pem')
const keyPath = path.join(certsDir, 'key.pem')

fs.writeFileSync(certPath, pems.cert)
fs.writeFileSync(keyPath, pems.private)

// Compute SPKI SHA-256 for Chrome --ignore-certificate-errors-spki-list and serverCertificateHashes
const x509 = new X509Certificate(pems.cert)
const spkiDer = x509.publicKey.export({ type: 'spki', format: 'der' })
const spkiHash = createHash('sha256').update(spkiDer).digest()
const spkiHashBase64 = spkiHash.toString('base64')

fs.writeFileSync(path.join(certsDir, 'fingerprint.txt'), spkiHashBase64)
fs.writeFileSync(
  path.join(certsDir, 'fingerprint.json'),
  JSON.stringify({ hash: spkiHashBase64, hashHex: spkiHash.toString('hex') }, null, 2)
)

// Verify it's NOT a CA cert
console.log(`\n  CA cert: ${x509.ca}  ← must be false`)
console.log(`  Valid:   ${x509.validFrom}  →  ${x509.validTo}`)

const notBefore = new Date(x509.validFrom)
const notAfter  = new Date(x509.validTo)
const daysValid = Math.round((notAfter - notBefore) / 86400000)
console.log(`  Period:  ${daysValid} days  (limit: 14)`)

console.log('\n[legilimens] Certificates written:')
console.log(`  ${certPath}`)
console.log(`  ${keyPath}`)
console.log(`\n  SPKI Hash (base64): ${spkiHashBase64}`)
console.log('\n[legilimens] Launch Chrome with:')
console.log(
  `  chrome --origin-to-force-quic-on=localhost:4433,localhost:4434 --ignore-certificate-errors-spki-list=${spkiHashBase64}`
)
console.log('\n[legilimens] Windows PowerShell:')
console.log(
  `  & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --origin-to-force-quic-on=localhost:4433,localhost:4434 --ignore-certificate-errors-spki-list=${spkiHashBase64} http://localhost:5173`
)
console.log(`\n[legilimens] Certificate valid for ${daysValid} days. Re-run before expiry.`)
