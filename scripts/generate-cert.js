import { execFileSync } from 'child_process'
import fs from 'fs'
import os from 'os'
import path from 'path'
import { fileURLToPath } from 'url'
import { createHash, X509Certificate } from 'crypto'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const certsDir = path.join(__dirname, '../server/certs')

console.log('[legilimens] Generating self-signed certificate...')

// WebTransport serverCertificateHashes requirements (Chromium):
// - Key algorithm MUST be ECDSA on the P-256 (secp256r1) curve.
//   RSA certs are REJECTED and the QUIC handshake fails with ERR_CONNECTION_REFUSED.
// - Validity period MUST be <= 14 days.
// - Certificate MUST be a leaf/end-entity cert (NOT a CA cert).
//
// The `selfsigned` npm package only emits RSA keys, so we shell out to openssl
// to produce a P-256 certificate instead.
if (!fs.existsSync(certsDir)) {
  fs.mkdirSync(certsDir, { recursive: true })
}

const certPath = path.join(certsDir, 'cert.pem')
const keyPath = path.join(certsDir, 'key.pem')

// Generate the EC P-256 self-signed cert into a temp dir, then move into place.
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'legilimens-cert-'))
const tmpKey = path.join(tmpDir, 'key.pem')
const tmpCert = path.join(tmpDir, 'cert.pem')

try {
  execFileSync(
    'openssl',
    [
      'req', '-x509',
      '-newkey', 'ec',
      '-pkeyopt', 'ec_paramgen_curve:prime256v1',
      '-nodes',
      '-keyout', tmpKey,
      '-out', tmpCert,
      '-days', '13', // 13 days to stay safely under the 14-day limit
      '-subj', '/CN=localhost/O=Legilimens Security Tool',
      '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
      '-addext', 'basicConstraints=critical,CA:FALSE',
      '-addext', 'keyUsage=critical,digitalSignature',
      '-addext', 'extendedKeyUsage=serverAuth',
    ],
    { stdio: ['ignore', 'ignore', 'inherit'] }
  )

  fs.copyFileSync(tmpCert, certPath)
  fs.copyFileSync(tmpKey, keyPath)
} catch (err) {
  console.error('[legilimens] openssl failed to generate the certificate.')
  console.error('  Make sure the `openssl` CLI is installed and on your PATH.')
  console.error(`  ${err.message}`)
  process.exit(1)
} finally {
  fs.rmSync(tmpDir, { recursive: true, force: true })
}

const certPem = fs.readFileSync(certPath, 'utf8')

const x509 = new X509Certificate(certPem)

// Two DIFFERENT hashes are needed — do not confuse them:
//  • SPKI hash  = SHA-256 of SubjectPublicKeyInfo. Used ONLY for the Chrome/Chromium
//    --ignore-certificate-errors-spki-list launch flag.
//  • Cert hash  = SHA-256 of the ENTIRE DER certificate. This is the value the
//    WebTransport serverCertificateHashes API expects (served at /cert-hash by the proxy).
const spkiDer = x509.publicKey.export({ type: 'spki', format: 'der' })
const spkiHash = createHash('sha256').update(spkiDer).digest()
const spkiHashBase64 = spkiHash.toString('base64')

const certHash = createHash('sha256').update(x509.raw).digest()
const certHashBase64 = certHash.toString('base64')

fs.writeFileSync(path.join(certsDir, 'fingerprint.txt'), spkiHashBase64)
fs.writeFileSync(
  path.join(certsDir, 'fingerprint.json'),
  JSON.stringify(
    {
      spkiHash: spkiHashBase64,
      spkiHashHex: spkiHash.toString('hex'),
      certHash: certHashBase64,
      certHashHex: certHash.toString('hex'),
    },
    null,
    2
  )
)

// Sanity-check the constraints that WebTransport serverCertificateHashes cares about
const notBefore = new Date(x509.validFrom)
const notAfter  = new Date(x509.validTo)
const daysValid = Math.round((notAfter - notBefore) / 86400000)

console.log(`\n  Key type: ${x509.publicKey.asymmetricKeyType} ${x509.publicKey.asymmetricKeyDetails?.namedCurve ?? ''}  ← must be ec / prime256v1`)
console.log(`  CA cert:  ${x509.ca}  ← must be false`)
console.log(`  Valid:    ${x509.validFrom}  →  ${x509.validTo}`)
console.log(`  Period:   ${daysValid} days  (limit: 14)`)

console.log('\n[legilimens] Certificates written:')
console.log(`  ${certPath}`)
console.log(`  ${keyPath}`)
console.log(`\n  Cert hash (serverCertificateHashes, served at /cert-hash): ${certHashBase64}`)
console.log(`  SPKI hash (--ignore-certificate-errors-spki-list flag):     ${spkiHashBase64}`)
console.log('\n[legilimens] The app uses serverCertificateHashes, so the launch flag is OPTIONAL.')
console.log('[legilimens] If you still want to launch Chromium with the flag:')
console.log(
  `  chromium --origin-to-force-quic-on=localhost:4433,localhost:4434 --ignore-certificate-errors-spki-list=${spkiHashBase64} http://localhost:5173`
)
console.log('\n[legilimens] Windows PowerShell:')
console.log(
  `  & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --origin-to-force-quic-on=localhost:4433,localhost:4434 --ignore-certificate-errors-spki-list=${spkiHashBase64} http://localhost:5173`
)
console.log(`\n[legilimens] Certificate valid for ${daysValid} days. Re-run before expiry.`)
