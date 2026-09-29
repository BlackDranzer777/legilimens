# videocall.rs Two-Participant Audio Decode — Verification Plan

Status: **STAGE 1 HARDENED (offline + standalone Chrome mechanics); NOT wired into the live-call verdict; no
live/Docker run performed.** The passive observer, the pure evaluator, the source-fixture
signature, and their offline tests exist and pass (see §1a and §10). Everything about the
*live* two-participant proof remains a plan; thresholds are **provisional (not yet
calibrated)**; real-call audio decode/playback has **NOT** been verified. It plans how to
prove that, in a two-participant call using the real pinned `videocall.rs` Dioxus UI,
**each participant receives and decodes the *other* participant's synthetic microphone
audio**, both **direct** to the owned backend and **through the Legilimens proxy** — to the
same evidentiary bar the video path already meets, without weakening any assertion.

2026-09-29 review: [Stage-1 fixes, verified tests, and integration contract](VIDEOCALL_AUDIO_STAGE1_REVIEW.md)
is authoritative over conflicting implementation details below. 90 JS tests (including
both standalone Chrome probes) and 11 Python tests passed. No real-call audio run.

Verified baseline this builds on: `build/videocall-media/lifecycle-stream-cleanup-2/report.json`
passed simultaneous two-way video and both video-off/resume checks **direct AND proxied**
on the **lifecycle-patched** fixture; overall A/V is still **BLOCKED** because receiver
audio is unverified. The original unpatched fixture still has lifecycle failures — the two
builds must not be conflated (see [`VIDEOCALL_LIFECYCLE_DIAGNOSIS.md`](VIDEOCALL_LIFECYCLE_DIAGNOSIS.md)).

Companion documents: [`CLAUDE_VIDEOCALL_MEDIA_HANDOFF.md`](CLAUDE_VIDEOCALL_MEDIA_HANDOFF.md)
(scope/safety constraints), [`VIDEOCALL_MEDIA_RESULTS.md`](VIDEOCALL_MEDIA_RESULTS.md)
(current video PASS / audio BLOCKED state), [`VIDEOCALL_LIFECYCLE_DIAGNOSIS.md`](VIDEOCALL_LIFECYCLE_DIAGNOSIS.md).

---

## 1a. Stage-1 refinements (authoritative over earlier sections)

These refine the plan and record what Stage 1 implemented. Where they differ from later
sections, these win.

1. **Consume only newly observed PCM ranges.** The observer
   (`interop/videocall/media-probes.mjs`) tags each NetEq worker with a stable integer id
   and a monotonic cumulative sample counter, and stores samples in a fixed circular ring
   (bounded: `maxWorkers=8`, `ringSamples=48000*3` per worker). `sampleAudioObservers(cursors)`
   returns, per worker, only samples with absolute index ≥ the caller's cursor, plus
   `totalSamples`, `availableStart`, and an explicit `gap` (samples dropped from the ring
   before consumption = overflow). The pure `consumeSnapshot`
   (`interop/videocall/media-audio.mjs`) advances per-worker cursors so **a repeated
   snapshot yields zero fresh samples** and cannot manufacture liveness, and it sets
   explicit flags: `overflow`, `gapSamples`, `stopped` (counter stopped advancing beyond
   `stopMs`), `replaced` (a second sample-bearing worker appeared), `ambiguous` (>1
   sample-bearing worker recently active). Any of overflow / gap / stopped / replacement / ambiguity
   fails (or blocks) a direction — proven in `interop/tests/media-audio.test.mjs`.
2. **PCM presence is not proof.** NetEq can emit silence or loss-concealment output, so the
   evaluator requires the *other* participant's **source signature** AND **temporal
   progression**: dominant Goertzel energy on the expected participant's tone alphabet
   (with a dominance ratio over rivals and control bins), a cyclic match of the observed
   symbol run-order to this run's derived sequence, and ≥ `minTransitions` advancing symbol
   runs. Missing PCM → **BLOCKED**; observed silence/zeros in active mode → **FAIL**; DC/noise/wrong-frequency (energy but
   no signature) → **FAIL**, frozen single tone → **FAIL**.
3. **Fresh-PCM coverage is separate from tone energy.** The WAV is intentionally part
   silence, so the plan does **not** require ~90% non-silent samples. `minDeliverySec`
   gates *fresh decoded-PCM delivery* into the window; the tone/liveness checks operate only
   on the tone slots. The fixture is a 6 s file that Chrome **loops**; the evaluator matches
   the sequence **cyclically** and treats the silent tail and the loop boundary as expected
   (`synthStream` loop-boundary test).
4. **Deterministic run/participant signature at the source boundary.**
   `interop/videocall/media_fixtures.py` now emits a per-`(run_id, participant)` tone
   **sequence** (`derive_sequence`, sha256-based, no consecutive repeats). Alphabets are
   **harmonic-safe** — all tones in [900,1500] Hz so their 2nd harmonics (≥1800 Hz) leave
   the band; A uses the lower sub-band (943/1013/1087/1163), B the upper
   (1237/1307/1381/1459), so 440/880-style octave ambiguity is gone and participant
   identity is separable by band before decoding. Wrong participant, wrong-run sequence, and
   stale/repeated segments are rejected (tests). Signature parameters and the WAV SHA-256 are
   recorded in `fixtures.json`; the JS `deriveSequence` is byte-for-byte identical to the
   Python one (locked cross-language vector test in both suites).
5. **Separate microphone-only mute/resume phase.** The existing `checkControls` disables
   audio *and* video together. Audio isolation needs a distinct phase: mute only the
   sender's **mic** (real `Mute` control), require **both video directions to stay live**,
   and assert the receiver's decoded PCM for that sender **stops** (evaluator `mode:'silent'`
   → PASS on real silence, FAIL if the tone continues), then resume and require the signature
   to return. This phase is **specified only** in Stage 1 (evaluator `silent` mode exists and
   is tested); the live wiring is deferred to the integration stage.
6. **Live comparisons use the lifecycle build.** Future live runs must pass
   `--frontend-dist build/videocall-frontend-lifecycle/dist` (built and provenance-recorded
   by `build_lifecycle_fixture.py` → `fixture-build.json`: base image, result image, source
   hashes, patch hash). `run_media.py` already records the chosen frontend and its build
   record and labels a non-default dist as `custom-unverified`; the original pinned dist stays
   the default and is never overwritten.
7. **Runner gating (documented; change deferred).** Today `run_media.py` runs the proxied
   route after a **video-only** direct baseline (`video_baseline_ready`), and `--direct-only`
   cannot pass. Complete A/V verification must instead require a **direct A/V** baseline
   (video **and** audio) before the proxied route counts toward success; any video-only or
   diagnostic path must be explicit and must NOT be reportable as overall A/V success. This
   gating change is **not made in Stage 1** (no verdict wiring yet); it is required for the
   integration stage and is captured in §9/§10.

Stage-1 boundaries: earned audio PASS is **not** wired into `mediaVerdict`/`driver_result`;
no Docker or full videocall test was started; no Legilimens or upstream JS/Rust/WASM was
modified; the app was not rebuilt.

---

## 1. What "receives and decodes" means here — and what it does NOT

We must separate three stages and be explicit about which we can and cannot verify:

| Stage | Definition | Verifiable in this harness? |
| --- | --- | --- |
| **Transport receipt** | Encoded Opus packets for the other peer arrive over native WebTransport | Yes (already gated for video: native WT, no media WS) |
| **Decode** | Those packets are Opus-decoded and jitter-buffered into **PCM samples** that carry the *other* participant's identifiable audio signature, produced continuously over time | **Yes — this is the target of this plan** |
| **Playback (audible output)** | The decoded PCM is rendered to a real speaker and would be heard by a human | **No — explicitly out of scope; see §2 and §9** |

"Decode" is proven only by observing **actual decoded PCM sample values** at a real
pipeline boundary and confirming they (a) are non-trivial (not silence/zeros),
(b) carry the *other* participant's distinct tone signature, (c) advance/stream
continuously across a sustained window (matching this run's derived sequence), and
(d) are attributed to that participant by **topology + signature** — the two-party
call has a single remote source whose alphabet/sequence identifies the sender. This is
**not** an authenticated worker→peer mapping (see §5 for the limits). Nothing weaker is
accepted (see §4.3).

**Explicitly rejected as proof of received/decoded audio** (per the task and the
guide):

- Microphone/mute **button UI state** (`Mute`/`Unmute` tooltip visibility). The
  existing harness already flags this: `media-browser.mjs:164` sets
  `micUi.status = 'PASS'` with the comment *"UI state only, never a remote-audio
  verdict."* It is retained as a UI liveness note, never as audio proof.
- **Packet counts / jitter-buffer stats** — the NetEq `stats` diagnostics
  (`jitter_buffer_delay_ms`, `packets_awaiting_decode`, `current_buffer_size_ms`,
  emitted at `neteq_audio_decoder.rs:329-408`). These show packets *arrived and were
  buffered*, not that PCM was produced.
- **AudioWorklet startup / node creation** — worklet registration
  (`shared_audio_context.rs:119-175`) or `pcm-player` node creation
  (`shared_audio_context.rs:177-206`). `probe-worklet.mjs` only proves the *local*
  browser can register a worklet under the serving policy; it says nothing about
  remote audio.
- **VAD / RMS "speaking" indicators** — `peer_speaking` events
  (`neteq_audio_decoder.rs:214-224`) and `is_speaking`/`audio_level` heartbeat
  flags. RMS is *derived from* decoded PCM, so it is at most weak corroboration;
  the sender's `is_speaking` heartbeat flag is the sender's self-report and is not
  receiver-side decode at all. Primary proof is the PCM itself.

---

## 2. Ground truth: how audio actually flows in this checkout

Traced in the pinned checkout at `.tooling/videocall-rs` (revision
`31a8b2076e7228f1b846fe4ed75dc70ffc27eb44`). All line references are to that source.

**Sender (encoder) path** — `videocall-client/src/encode/microphone_encoder.rs`:
- Real `getUserMedia` mic (in tests: Chrome's `--use-file-for-fake-audio-capture`
  WAV) → `encoderWorker.min.js` AudioWorklet Opus encoder (`create_node(...,
  "/encoderWorker.min.js", "encoder-worklet", ...)`, line 464-471).
- Each encoded chunk → `transform_audio_chunk` (line 86-131, `MediaType::AUDIO`,
  optional RED redundancy) → `client.send_media_packet(packet)` (line 316) over the
  same connection as video.
- Local VAD (AnalyserNode RMS, line 556-599) only sets the sender's own
  `is_speaking`/`audio_level` for the heartbeat — **not** part of the receiver proof.
- Muting the mic: `set_enabled(false)` (line 196-216) stops the codec and, via the
  connection, sets `audio_enabled=false` in the 1 Hz heartbeat (see below).

**Receiver (decoder) path** — `videocall-client/src/decode/`:
1. `PeerDecodeManager::decode` → `Peer::decode` (`peer_decode_manager.rs:287-513`)
   routes `MediaType::AUDIO` to the peer's `NetEqAudioPeerDecoder`.
2. `NetEqAudioPeerDecoder::decode` (`neteq_audio_decoder.rs:667-756`) posts
   `WorkerMsg::Insert{seq,timestamp,payload}` to a **per-peer NetEq Web Worker**
   (one `Worker` per peer, created at `neteq_audio_decoder.rs:505` from the
   `#neteq-worker` link tag → `/neteq_worker_loader.js`, `index.html:43`).
3. **Inside the worker** (`neteq/src/bin/neteq_worker.rs`): `insert_packet` feeds
   NetEq; a 5 ms production timer (line 223-314) calls `eq.get_audio()` and posts the
   decoded **`Float32Array` PCM** back to the main thread via
   `post_message_with_transfer(&pcm, &sab)` (lines 247-251 and 289-295), ~100 frames/s
   of 48 kHz mono PCM (10 ms / 480 samples per frame).
4. **Main thread** receives it in `create_message_handler`
   (`neteq_audio_decoder.rs:424-441`): `data.is_instance_of::<Float32Array>()` →
   `handle_pcm_data` (line 185-241), which computes VAD RMS and then **forwards the
   PCM to the per-peer `pcm-player` AudioWorklet** via
   `send_pcm_to_safari_worklet` → `pcm_player.port().post_message({command:'play',
   pcm})` (line 127-139, 233-240). This forward is a structured clone (no transfer).
5. **Playback worklet** `videocall-client/src/scripts/pcmPlayerWorker.js`:
   `handleMessage` `'play'` enqueues into a ring buffer (line 176-203); `process()`
   writes to output channels (line 215-227). Graph:
   `pcm-player → peer_gain → master_gain → AudioContext.destination`
   (`shared_audio_context.rs:184-191` and `40-42`). `destination` is the speaker.

### 2.1 The linchpin: mute *gates PCM production*

This is the single most important fact for the design:

- A peer's audio decoder is **created muted** (`peer_decode_manager.rs:152`,
  `audio.set_muted(true)`), and the worker starts muted
  (`neteq_worker.rs:78`, `IS_MUTED = true`).
- The worker's production timer **only calls `get_audio()` and posts PCM when
  `!is_muted`** (`neteq_worker.rs:242-255` for the first frame and `264-306` for
  steady state — when muted it advances the frame counter and produces nothing).
- The decoder is **auto-unmuted** when the sender is actually sending audio:
  - first AUDIO frame before any heartbeat → `audio_enabled=true; audio.set_muted(false)`
    (`peer_decode_manager.rs:350-361`), and
  - heartbeat with `audio_enabled=true` → `set_muted(false)`
    (`peer_decode_manager.rs:441-447`).
- When the sender mutes, its heartbeat carries `audio_enabled=false` → receiver calls
  `set_muted(true)` and `flush()` (`peer_decode_manager.rs:441-474`) → the worker
  **stops producing PCM**.

**Consequences the plan relies on:**
1. Decoded PCM crosses the worker→main boundary **only while the remote sender's mic
   is on**. So observing real PCM there is itself strong evidence of an active,
   decoded remote stream — and the *absence* of PCM after a remote mute is a genuine,
   mechanism-level signal (not UI state).
2. No local user action is required to "listen": once the other participant unmutes
   (which the harness already does via the real `Unmute` control), the receiver's
   decoder auto-unmutes and PCM begins. The plan does **not** need a new UI gesture.

---

## 3. Fixtures (participant audio signatures) — IMPLEMENTED (Stage 1)

`interop/videocall/media_fixtures.py` now emits a **deterministic, run-specific,
participant-specific tone sequence** (the `SIGNATURE` block + `derive_sequence` +
`make_audio`), consumed by Chrome via `--use-file-for-fake-audio-capture`
(`media-browser.mjs:32`). This replaced the earlier legacy 440/554/880/988 two-tone
pattern (those were octave-related and harmonically ambiguous).

- **Harmonic-safe alphabets** (all in [900,1500] Hz, so 2nd harmonics ≥1800 Hz leave the
  analysis band; disjoint sub-bands separate participants before decoding):
  **A** = 943/1013/1087/1163 Hz, **B** = 1237/1307/1381/1459 Hz. Off-target control bins
  800/1200/1600 Hz reject noise. Must match `ALPHABETS`/`CONTROL_HZ` in `media-audio.mjs`.
- **Sequence:** `derive_sequence(run_id, participant, alphabet, 12)` = per-index sha256,
  first byte mod alphabet size, rotated so no two consecutive symbols repeat (a detectable
  transition every slot). A different run_id or participant yields a different sequence, so
  **previous-run signatures and wrong participants are rejected**. The JS `deriveSequence`
  is byte-for-byte identical (locked cross-language vector tested in both suites).
- **Layout:** 48 kHz / 16-bit mono; each symbol = 0.30 s tone + 0.15 s gap; 12 symbols
  (5.4 s) then a silent tail to the 6 s file. Chrome **loops** the file, so the receiver
  sees the sequence + silent-tail repeat; the evaluator matches it **cyclically** and treats
  the loop boundary / silent tail as expected. **Fresh-PCM coverage is not tone coverage**:
  the file is intentionally part-silence, so we never require ~90% non-silent samples.
- **Recorded in `fixtures.json`:** per participant `signature` (version, `alphabetHz`,
  `controlHz`, `sequenceHz`, `toneSec`, `gapSec`, `sequenceLength`, `sampleRate`) and the
  WAV SHA-256; a legacy `tones` field (= alphabet) is kept for `verify-fixtures.mjs`.

The tone/silence envelope + advancing symbol order is the audio analogue of the video
"advancing bar": correctly-timed, correctly-*ordered* bursts across a multi-second window
prove *live streaming decode*, not a static or replayed buffer.

**Optional future hardening (not implemented):** a slow per-participant frequency micro-step
across the loop to further defeat replay. Fixture-only, source-boundary; touches no receive
path. Leave off unless review wants it.

---

## 4. Decoded-PCM observation points

### 4.1 Primary observation point — worker→main `Float32Array` (decode boundary)

The **cleanest true decode boundary** is the `Float32Array` posted by the NetEq worker
to the main thread (`neteq_worker.rs:247-251/289-295` → received at
`neteq_audio_decoder.rs:427`). This is *after* Opus decode + jitter buffering and
*before* playback. Sampling here proves decode independently of whether audio is ever
rendered to a speaker.

**PROPOSED observer (not yet implemented), passive and disclosed.** Extend the
existing `installMediaProbes` in `interop/videocall/media-probes.mjs`, which **already
proxies `globalThis.Worker` construction** (line 36-43). At construction of a worker
whose script URL matches the NetEq loader (`neteq_worker_loader`), attach an
**additional passive** listener:

```js
worker.addEventListener('message', (e) => {
  if (e.data instanceof Float32Array && e.data.length) {
    // COPY out immediately; never transfer, mutate, or re-post.
    ring.push(e.data.slice());          // bounded ring buffer (drop-oldest)
    totalSamples += e.data.length;      // monotonic counter
  }
});
```

Why this is safe and non-invasive (must hold, and be re-audited during
implementation):
- It uses `addEventListener`, which **adds** a listener. The app installs its handler
  via the `onmessage` *property* (`worker.set_onmessage`, `neteq_audio_decoder.rs:549`).
  The two do not replace each other; both fire. **No app handler is wrapped,
  replaced, or reordered in a way that changes behavior.**
- It only **reads and copies** (`.slice()`). It never transfers, mutates, injects,
  or re-posts. It creates no decoded results and alters no codec/worklet/transport
  output — satisfying the guide's "observers must not create decoded results or
  replace outputs."
- The app forwards PCM to the playback worklet by structured clone (no transfer,
  `send_pcm_to_safari_worklet`), and the worker→main hop's transfer completes at the
  postMessage boundary; a passive read on the receiving side does not neuter the
  buffer for the app's own handler. (To be re-verified empirically on first run.)
- The ring buffer is bounded (e.g., last ~2 s = 96k samples) to cap memory. Audio
  carries no JWTs/cookies; we still keep the report's existing redaction discipline.

The Node driver periodically snapshots the ring and runs frequency analysis (§4.3).

### 4.2 Secondary / corroborating point — `pcm-player` worklet input (delivery to playback stage)

The message the decoder posts to the `pcm-player` node
(`pcm_player.port().post_message({command:'play', pcm})`,
`neteq_audio_decoder.rs:130-136`; consumed at `pcmPlayerWorker.js:176-203`) shows the
decoded PCM was **handed to the playback stage**. This is *closer to* playback but is
still **not** audible output. Optionally observe it (passively wrap the `pcm-player`
node's `port.postMessage`, or read inside a disclosed copy of the worklet — the
latter would touch upstream JS and is therefore **not** proposed). Treated only as
corroboration that decode output reached the renderer; never the primary verdict.

### 4.3 What we measure on the captured PCM

For a snapshot window of decoded PCM at 48 kHz mono:

1. **Energy floor** — mean square (RMS²) must exceed a silence floor to reject
   zeros/near-silence.
2. **Identity by frequency** — Goertzel (or FFT) energy at the **other** participant's
   two fixture tones must dominate: energy at the other's tones ≫ energy at *this*
   participant's own tones **and** ≫ energy at off-target control bins (e.g., 300 /
   700 / 1500 Hz). This ties the decoded audio to that specific synthetic mic and
   guards against self-audio leakage and broadband noise.
3. **Liveness** — across a sustained window, count in-band **bursts** of the other's
   tone(s) separated by sub-floor gaps; require a count consistent with the ~1.4 s
   fixture period (i.e., continuous streaming decode, not a single static buffer).
4. **Attribution** — see §5.

All numeric thresholds (floor, dominance ratio, minimum bursts, window length,
snapshot cadence) are **provisional (not yet calibrated)** and will be fixed from the
first real direct run *before* any proxied comparison, then held identical across
routes (mirroring `VIDEO_LIMITS`, `media-policy.mjs:2-4`).

---

## 5. Participant attribution (three independent layers)

The test is strictly **two participants**, so on each browser there is exactly **one
remote peer**. Attribution uses three layers, in decreasing self-sufficiency:

1. **Topology** — On B's page the only remote NetEq worker is A's; on A's page it is
   B's. Any decoded PCM observed on B is therefore A's audio by construction (and
   vice-versa). Guard: assert exactly one remote peer / one NetEq PCM source per page
   during the window (analogous to the video `tilePresent === 1` guard,
   `media-probes.mjs:71`). More than one remote PCM source ⇒ FAIL (ambiguous).
2. **Frequency signature** — The observed dominant tones must match the *other*
   participant's disjoint fixture set (§4.3). This is content-level attribution that
   needs no trust in internal identifiers, and it is what makes "self-audio leak"
   detectable (B hearing B's own 880/988 on a remote source ⇒ FAIL).
3. **Server-session corroboration** — Map the remote peer's `session_id` → authenticated
   `user_id` using the **same** transport-server room/user log parsing already used for
   video (`parseServerSessions`, `media-policy.mjs:89-96`; consumed via
   `report.serverAttribution`, `media-browser.mjs:196-200`). Tie the audio-bearing peer
   to `media-a`/`media-b`. (Linking a *specific NetEq worker instance* to a `session_id`
   from JS is **not yet designed**; the `peer_speaking`/`neteq` diagnostics carry
   `to_peer=peer_id`, but whether those events are exposed to a JS-visible sink in this
   build is **NOT YET VERIFIED** — see §11. Until verified, attribution rests on layers
   1 + 2, which are sufficient for a 2-party call.)

---

## 6. Test procedure (mirrors the proven video gate)

Reuse the entire existing owned-service orchestration and real-UI join/admit flow;
add audio observation alongside video. No new services, no new auth path.

1. **Setup (unchanged):** `run_media.py` brings up owned NATS / PostgreSQL / meeting-api
   / webtransport-server (+ Legilimens proxy for the proxied route), serves the UI
   locally (crossOriginIsolated, exact-origin CORS, CSP), generates fixtures. Two real
   Chrome instances with **distinct** fake video+audio files (`media-browser.mjs:28-33`).
2. **Join + admit (unchanged):** real home submit → `POST /join` → host `Start
   Meeting` → host admits guest via `.btn-admit` → guest `Join Meeting`
   (`media-browser.mjs:69-104,172-184`).
3. **Enable media (unchanged):** both enable camera and mic via the real controls
   (`Start Video`, `Unmute`; `setMedia`, `media-browser.mjs:84-89`). Unmuting the mic
   is what triggers the *other* side's decoder to auto-unmute and start producing PCM
   (§2.1).
4. **Warm-up (bounded):** wait, up to a bounded deadline, until **both** directions
   show first decoded PCM meeting the identity+floor test (audio analogue of
   `waitForVideo`, `media-browser.mjs:127-147`). Separate from and not weakening the
   sustained window.
5. **Sustained baseline window:** over a fixed window (provisional: reuse
   `windowMs = 15000`), sample decoded PCM on both pages concurrently and evaluate the
   §4.3 criteria per direction **in the same window as the video** observation.
6. **Bounded mute/resume checks:** §7.
7. **Route order (unchanged safety rule):** run **direct first**; only run the
   **proxied** route if the direct A/V baseline is clean. Proxied uses the exact same
   thresholds. A media **WebSocket** success must FAIL the WebTransport test
   (`liveTransport`, `media-policy.mjs:29-34`) — reused verbatim for audio.
8. **Aggregate + cleanup (unchanged):** `media_results.py` / `run_media.py` owned-resource
   teardown and port-release verification.

---

## 7. Bounded mute/resume checks (false-positive controls)

These exploit the real mechanism in §2.1 and observe the *effect at the decode
boundary*, never the button.

For each sender S ∈ {A, B}, bounded and time-boxed like the video controls
(`checkControls`, `media-browser.mjs:149-170`; limits in `media-policy.mjs:2-4`):

1. **Mute:** S clicks the real `Mute` control. After a drain (provisional 3 s, for
   heartbeat propagation + NetEq flush), over a bounded window (provisional 6 s) the
   **other** page's decoded-PCM stream for S must **stop**: in-band energy at S's
   tones must fall below the silence floor for the whole window (worker ceased
   producing PCM). This is the mute proof — absence of decoded PCM, not mic UI.
2. **Isolation during mute:** in the same window, the *other* direction's audio, both
   video directions, peer membership, and native transport must remain live
   (reuse the video isolation checks).
3. **Resume:** S clicks `Unmute`. Within a bounded warm-up (provisional ≤15 s), S's
   tones must reappear on the other page and satisfy the full §4.3 criteria again.
4. Symmetric for the other sender.

`micUi` remains reported as UI-only telemetry (`media-browser.mjs:164`), never counted
toward the audio verdict.

---

## 8. Negative / anti-false-positive tests

1. **Self-audio leak:** on B, decoded PCM attributed to the remote source must carry
   **A's** tones (440/554), never B's own (880/988). Own-tone dominance on a remote
   source ⇒ FAIL (mirrors the video self-preview guard).
2. **Silence baseline:** before the remote sender unmutes (or while its mic is off),
   the receiver's NetEq worker produces **no** PCM (mute gates production). The window
   must be empty/sub-floor. A non-silent "remote" reading here ⇒ FAIL and indicates a
   broken observer or leakage. This baseline is what makes the positive result
   meaningful.
3. **Off-target frequency energy:** energy at control bins (300/700/1500 Hz) must stay
   well below the in-band tones; broadband energy without a dominant expected tone
   (e.g., noise or a wrong decode) ⇒ FAIL, not PASS.
4. **Transport-fallback:** any successful media **WebSocket** ⇒ FAIL (native WT
   required), reusing `liveTransport` unchanged.
5. **Muted-remote ⇒ no PCM:** covered by §7.1; a decoded stream that persists after a
   confirmed remote mute ⇒ FAIL.
6. **Zeros/DC/constant guard:** all-zero, constant, or DC-only buffers fail the energy
   *and* tone tests (guards against a stuck/garbage buffer scoring as "audio").

---

## 9. Precise PASS / FAIL / BLOCKED criteria

Per **direction** (A→B and B→A), over the sustained window, on a given **route**
(direct or proxied):

- **PASS** requires **all** of:
  1. native transport healthy, **no** media WS (`liveTransport`), for the whole window;
  2. exactly one remote PCM source on the receiver (topology guard);
  3. decoded PCM present with energy above the silence floor for ≥ the coverage
     threshold of samples (provisional: reuse `minCoverage = 0.9`);
  4. dominant in-band frequency = the **other** participant's fixture tones, above the
     dominance ratio vs. own-tones and control bins;
  5. **liveness**: ≥ the minimum number of correctly-timed in-band bursts across the
     window (streaming, not static);
  6. attribution satisfied (layers 1 + 2; layer 3 corroborated where available).
- **FAIL**: any negative test in §8 triggers; or PCM present but wrong identity /
  no liveness / WS fallback / ambiguous source / decoded audio persists after remote
  mute; or driver/worker/runtime errors (the media verdict already fails closed on
  `errors`, `media-policy.mjs:99-101`).
- **BLOCKED**: the passive observer cannot be attached, or the observation boundary
  cannot be read on this build (e.g., transfer/neutering prevents a safe passive copy),
  **and** no negative test has fired — i.e., we could not measure, as opposed to
  measured-and-failed. BLOCKED is recorded honestly with retained partial evidence;
  it is never upgraded to PASS.

**Overall audio verdict** = PASS only if **both** directions PASS on **both** routes
**and** both bounded mute/resume checks PASS. This slots into the existing fail-closed
aggregation without weakening it:
- `media-policy.mjs mediaVerdict` (line 118-122) already requires `audio.aToB`,
  `audio.bToA`, and each `controls[S].audioReceipt` to be `PASS` for an overall
  `passed`; today those are hard-wired to `BLOCKED`
  (`media-browser.mjs:19,150`). The plan's job is to make them *earned* PASS/FAIL from
  real PCM, **not** to relax the aggregation.
- `media_results.py driver_result` (line 13-18) already requires audio aToB/bToA and
  `audioReceipt` PASS for a complete run. Unchanged.

No assertion is loosened anywhere. A media WebSocket success still fails the WT test.

---

## 10. Files changed and status

Harness only; the pinned upstream checkout and its built JS/WASM are **not** touched.

### Stage 1 — IMPLEMENTED and offline-tested (this diff)

| File | Change |
| --- | --- |
| `interop/videocall/media-probes.mjs` | Extended `installMediaProbes`: bounded per-NetEq-worker passive PCM observer (circular ring, monotonic sample counter, `maxWorkers`/`ringSamples` bounds). Adds `addEventListener('message')` — never replaces the app's `onmessage`, never mutates/transfers the buffer, never posts/injects. New export `sampleAudioObservers(cursors)` returns only samples past each cursor plus `gap`/`totalSamples`. |
| `interop/videocall/media-audio.mjs` **(new)** | Pure evaluator: `deriveSequence`, `expectedSignature`, `goertzelPower`, `rms`, `classifyHops`, `evaluateAudioWindow` (active/silent modes), `consumeSnapshot`/`newAudioState` (cursor advance + overflow/gap/stopped/replaced/ambiguous flags), `evaluateDirection` (topology+signature selection), `AUDIO_LIMITS` (provisional). No browser APIs, no I/O. |
| `interop/videocall/media_fixtures.py` | Run/participant tone **signature** (`SIGNATURE`, `derive_sequence`); `make_audio` renders the sequence; `generate` records signature params + WAV SHA-256. Harmonic-safe alphabets. Video generation unchanged. |
| `interop/tests/media-audio.test.mjs` **(new)** | Evaluator + range-consumption tests (see §Tests below). |
| `interop/tests/media-audio-observer.test.mjs` **(new)** | Observer-mechanics tests (ring slicing/gaps; passive non-interference; bounds; skip-gated real-Chrome transfer-integrity test). Labelled NOT decode evidence. |
| `interop/tests/test_media_fixtures.py` | Added signature determinism + cross-language vector + WAV-matches-sequence tests. |

### Integration stage — NOT DONE (deferred for review)

| File | Planned change | Status |
| --- | --- | --- |
| `interop/videocall/media-browser.mjs` | Poll `sampleAudioObservers` with per-worker cursors alongside video sampling; feed `consumeSnapshot`/`evaluateDirection`; populate `report.audio[dir]` and add a **mic-only** mute/resume phase (§7) writing `controls[S].audioReceipt`, replacing the hard-coded `BLOCKED` (`media-browser.mjs:19,150`). | **NOT wired** |
| `interop/videocall/media-policy.mjs` | Optionally re-export `AUDIO_LIMITS`; `mediaVerdict` already consumes audio statuses (no semantic change). | **NOT changed** |
| `interop/videocall/run_media.py` | Gating change (§1a point 7 / §9): require a **direct A/V** baseline before the proxied route can count toward success; keep video-only/`--direct-only` explicitly non-passing. | **NOT changed** |

Upstream (`/.tooling/videocall-rs/...`) and Legilimens backend/frontend: **no changes.** The
app was not rebuilt. All decode/worker/worklet/transport/playback code stays exactly as
pinned; the observer only reads its output.

### Tests (Stage 1)

- `interop/tests/media-audio.test.mjs`: signature parity + determinism +
  harmonic-safety; valid receive both directions; loop-boundary/mid-sequence; rejects wrong
  participant, self-source leak, wrong-run sequence, DC, noise, off-signature tones, frozen
  tone; absent PCM→BLOCKED; active silence→FAIL; measured-silence checks; repeated-snapshot no-liveness; stopped
  delivery; gap+overflow; worker-replacement ambiguity; single-source PASS; no-source BLOCKED.
- `interop/tests/media-audio-observer.test.mjs`: ring slicing/gap reader; passive
  non-interference + non-replacement + delivery-count (vm); NetEq-only + bounds; **skip-gated**
  real-Chrome transferred-PCM integrity test (`MEDIA_BROWSER_TEST=1`, **passed 2026-09-29**).
- `interop/tests/test_media_fixtures.py`: signature determinism, locked cross-language vector,
  WAV dominant-tone matches the derived sequence.

Current run commands, hardened contracts, and results are in [the Stage-1 review](VIDEOCALL_AUDIO_STAGE1_REVIEW.md).

---

## 11. Open questions / risks / NOT YET VERIFIED

1. **Passive-read safety on transferred buffers** — the worker→main hop uses
   `post_message_with_transfer`. A passive `.slice()`/ring copy on the receiving side is
   believed not to neuter the buffer for the app's own handler. The **vm** observer-mechanics
   test proves non-interference under synthetic (non-transfer) messages; the **real-transfer**
   proof is the skip-gated Chrome test, which **passed on 2026-09-29** with three distinct
   transferred buffers. Real-call NetEq observation is still unverified. If real transfer ever proves unsafe, fall back to the §4.2
   `pcm-player` port observation (still passive) or record BLOCKED — never patch the upstream
   worker to emit a copy.
2. **Diagnostics visibility** — whether `peer_speaking`/`neteq`/`peer_status` events
   (which carry `to_peer=peer_id`) are exposed to a JS-visible sink in this build is
   **NOT YET VERIFIED**. Attribution layer 3 depends on it; layers 1+2 do not, and
   are sufficient for two parties.
3. **Thresholds uncalibrated** — floor, dominance ratio, burst counts, windows are all
   provisional until a first direct run. They will be fixed *before* the proxied
   comparison and then held identical across routes.
4. **Fake-audio looping** — Chrome loops the fake WAV, so the audio liveness proof is
   "continuous correctly-timed bursts," not a monotonic counter. The optional fixture
   frequency-step (§3) would strengthen anti-replay if desired.
5. **Proxied video passed on the labelled lifecycle-patched fixture**, in one paired
   direct/proxied run (see `VIDEOCALL_LIFECYCLE_DIAGNOSIS.md`). This does not establish
   audio or unchanged-upstream compatibility. Establish direct A/V before counting
   the proxied A/V comparison as successful; retain honest FAIL/BLOCKED results.

---

## 12. Safety & scope compliance (restated)

- Owned local services only; no public videocall.rs / OAuth / analytics; app ports on
  127.0.0.1; PostgreSQL/NATS unpublished.
- **No** global TLS ignore, `ignoreHTTPSErrors`, `INSECURE`, or disabled web security;
  browser trusts only the exact owned endpoint pin, Legilimens verifies the exact
  upstream pin — unchanged from the video path.
- Synthetic mic/camera inputs only; no physical devices; no user browser profile.
- Observers are **passive, read-only**: they create no decoded results, replace no
  outputs, inject no audio, draw nothing onto receiving canvases, and modify no
  upstream code or built assets. Test-only input/observation adapters are disclosed
  and scoped.
- **No assertion is weakened to obtain a pass.** A media WebSocket success fails the
  WT test. BLOCKED is reported honestly, never upgraded.
- Stage 1 changed **only** the harness files listed in §10; it started **no** Docker or
  live videocall run, launched **no** browser (the real-Chrome observer test is skip-gated
  and was not executed), rebuilt **nothing**, and touched **no** Legilimens or upstream
  JS/Rust/WASM. Earned audio PASS is **not** wired into the live-call verdict. **Real-call
  audio decoding/playback has NOT been verified.**
