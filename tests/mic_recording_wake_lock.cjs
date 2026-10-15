const fs = require('fs');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
const scenario = process.argv[3];
function between(a, b) { return source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a))); }
// Extract complete production function regions, including the visibility listener.
const production = [
  between('  function _setRecording(on)', '  function _updateMicTooltip'),
  between('  function _stopTracks(', '  // Gate continuous dictation'),
  between('  function _acquireMicWakeLock()', '  window._stopMic='),
  between('  async function _startMicCapture(', '  btn.addEventListener(\'pointerdown\''),
].join('\n');
const listeners = {};
const window = { _micActive: false, _micPendingSend: false };
const document = { visibilityState: 'visible', addEventListener: (k, fn) => { listeners[k] = fn; } };
const btn = { classList: { toggle() {} } };
const status = { style: {} };
const statusText = {};
let _rawAudioMode = scenario !== 'transcribe';
let _activeCaptureMode = null, _micWakeLock = null, _micWakeLockOp = null;
let _isRecording = false, _micStartSeq = 0, _micHoldActive = false;
let _finalText = '', _prefix = '', audioChunks = [], mediaStream = null, mediaRecorder = null;
let _speechStopRequested = false, _micRestartCount = 0, _forceMediaRecorder = false;
const _canRecordAudio = true, _locale = {};
const ta = { value: 'draft' };
const locks = [], recordings = [], delivered = [];
let requests = 0, stoppedTracks = 0, resolveLock;
function lock() {
  const l = { released: false, listener: null, addEventListener(k, fn) { this.listener = fn; },
    async release() { this.released = true; this.listener?.(); } };
  locks.push(l); return l;
}
const navigator = { mediaDevices: { async getUserMedia() { return { getTracks: () => [{ stop() { stoppedTracks++; } }] }; } },
  wakeLock: scenario === 'unsupported' ? undefined : { request(type) {
    assert.equal(type, 'screen'); requests++;
    if (scenario === 'rejected') return Promise.reject(new Error('denied'));
    if (scenario === 'pending-stop' || (scenario === 'pending-hide' && requests === 1)) return new Promise(resolve => { resolveLock = () => resolve(lock()); });
    return Promise.resolve(lock());
  } } };
class MediaRecorder {
  static isTypeSupported() { return true; }
  constructor() { this.state = 'inactive'; this.mimeType = 'audio/webm'; recordings.push(this); }
  start() { if (scenario === 'start-error') throw Error('start failed'); this.state = 'recording'; }
  stop() {
    this.state = 'inactive';
    this.ondataavailable({ data: new Blob(['synthetic audio bytes']) });
    this.onstop();
  }
}
window.MediaRecorder = MediaRecorder;
let recognition = scenario === 'speech' ? { start() {}, stop() { _setRecording(false); } } : null;
function _setButtonTooltipAndKey() {}
function _micButtonAvailable() { return true; }
function _micOriginNeedsSecureContext() { return false; }
function _micDictationContinuous() { return false; }
function _micToastKeyForRecognitionError() { return null; }
function _applyDeferredServerSttFlip() {}
function showToast() {}
function t(k) { return k; }
async function _sendRawAudio(blob) { delivered.push(await blob.text()); }
async function _transcribeBlob(blob) { delivered.push(await blob.text()); }
eval(production);
async function settle() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
(async () => {
  await _startMicCapture();
  await settle();
  if (scenario === 'start-error') {
    assert.equal(requests, 0); assert.equal(stoppedTracks, 1); assert.equal(window._micActive, false); return;
  }
  assert.equal(window._micActive, true);
  if (scenario !== 'unsupported') assert.equal(requests, 1, 'every active capture requests screen wake lock');
  if (scenario === 'pending-hide') {
    document.visibilityState = 'hidden'; listeners.visibilitychange();
    resolveLock(); await settle();
    assert.equal(locks[0].released, true, 'late hidden acquisition must release immediately');
    assert.equal(window._micActive, true);
    document.visibilityState = 'visible'; listeners.visibilitychange(); await settle();
    assert.equal(requests, 2);
  }
  if (scenario === 'visibility') {
    document.visibilityState = 'hidden'; listeners.visibilitychange(); await settle();
    assert.equal(locks[0].released, true);
    assert.equal(window._micActive, true, 'hide must not discard audio or stop recording');
    document.visibilityState = 'visible'; listeners.visibilitychange(); await settle();
    assert.equal(requests, 2);
  }
  if (scenario === 'error') {
    recordings[0].onerror(); await settle();
    assert.equal(window._micActive, false);
    // MediaRecorder emits a final dataavailable/stop sequence after error.
    recordings[0].stop();
  } else {
    _stopMic();
  }
  if (scenario === 'pending-stop') { resolveLock(); }
  await settle();
  assert.equal(window._micActive, false);
  assert.ok(locks.every(l => l.released), 'terminal capture releases all acquired locks');
  if (scenario !== 'speech') {
    assert.deepEqual(delivered, ['synthetic audio bytes'], 'stop preserves recorded byte handoff');
    assert.ok(stoppedTracks >= 1);
  }
  if (scenario === 'cancel') {
    // Pending permission cancellation must never acquire a lock or retain tracks.
    let grant;
    navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { grant = resolve; });
    const start = _startMicCapture(); _stopMic();
    const prior = requests;
    grant({ getTracks: () => [{ stop() { stoppedTracks++; } }] });
    await start; await settle();
    assert.equal(requests, prior); assert.equal(window._micActive, false);
  }
})().catch(err => { console.error(err); process.exitCode = 1; });
