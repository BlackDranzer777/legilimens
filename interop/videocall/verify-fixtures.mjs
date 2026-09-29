// Confirm Chrome consumes the chosen synthetic fixture files (not a default fake device):
// getUserMedia -> draw video -> read identity colour; analyse audio -> dominant tone.
import { writeFile } from 'node:fs/promises'
const cfg = JSON.parse(process.env.VFIX)
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')

const browser = await chromium.launch({ channel: process.env.VERIFY_CHANNEL || 'chrome', headless: true, args: [
  '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
  `--use-file-for-fake-video-capture=${cfg.video}`,
  `--use-file-for-fake-audio-capture=${cfg.audio}`,
] })
const out = { participant: cfg.name }
try {
  const page = await browser.newPage()
  await page.goto(cfg.url)   // loopback origin: a secure context for getUserMedia
  const res = await page.evaluate(async () => {
    const s = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
    const v = document.createElement('video'); v.srcObject = s; v.muted = true
    await v.play(); await new Promise((r) => setTimeout(r, 1200))
    const cv = document.createElement('canvas'); cv.width = 64; cv.height = 64
    const g = cv.getContext('2d'); g.drawImage(v, 0, 0, 64, 64)
    const px = g.getImageData(16, 8, 32, 16).data   // upper-centre (identity region)
    let r = 0, gg = 0, b = 0
    for (let i = 0; i < px.length; i += 4) { r += px[i]; gg += px[i + 1]; b += px[i + 2] }
    const nn = px.length / 4; const color = [Math.round(r / nn), Math.round(gg / nn), Math.round(b / nn)]
    // audio dominant frequency via AnalyserNode
    const ac = new (window.AudioContext || window.webkitAudioContext)()
    const src = ac.createMediaStreamSource(s); const an = ac.createAnalyser(); an.fftSize = 8192
    src.connect(an); await new Promise((r) => setTimeout(r, 900))
    const buf = new Float32Array(an.frequencyBinCount); an.getFloatFrequencyData(buf)
    let maxI = 0; for (let i = 1; i < buf.length; i++) if (buf[i] > buf[maxI]) maxI = i
    const domFreq = Math.round(maxI * ac.sampleRate / an.fftSize)
    s.getTracks().forEach((t) => t.stop())
    return { color, domFreq }
  })
  out.color = res.color; out.domFreq = res.domFreq
  const [er, eg, eb] = cfg.expectColor
  out.colorMatch = Math.abs(res.color[0] - er) < 60 && Math.abs(res.color[1] - eg) < 60 && Math.abs(res.color[2] - eb) < 60
  out.toneMatch = cfg.expectTones.some((f) => Math.abs(res.domFreq - f) < 40)
  out.passed = out.colorMatch && out.toneMatch
} catch (e) {
  out.error = String(e.message); out.passed = false
} finally {
  await browser.close()
  await writeFile(cfg.report, JSON.stringify(out, null, 2))
}
process.exitCode = out.passed ? 0 : 1
