'use strict';

// ── State ─────────────────────────────────────────────────────────────────
let webrtcPc   = null;
let mjpegActive = false;

// ── DOM shortcuts ─────────────────────────────────────────────────────────
const video     = () => document.getElementById('live-video');
const mjpeg     = () => document.getElementById('mjpeg-frame');
const indicator = () => document.getElementById('live-indicator');
const badge     = () => document.getElementById('mode-badge');
const errorEl   = () => document.getElementById('stream-error');

function setMode(mode) {
  const ind = indicator();
  const b   = badge();
  ind.className = mode;
  if (mode === 'webrtc') {
    ind.textContent = '● AO VIVO';
    b.textContent   = 'WebRTC · P2P ~100 ms';
  } else if (mode === 'mjpeg') {
    ind.textContent = '● AO VIVO';
    b.textContent   = 'MJPEG · fallback';
  } else if (mode === 'error') {
    ind.textContent = '○ ERRO';
    b.textContent   = '–';
  } else {
    ind.textContent = '● Conectando...';
    b.textContent   = '–';
  }
}

function showError() {
  video().style.display = 'none';
  mjpeg().style.display = 'none';
  errorEl().classList.add('visible');
  setMode('error');
}

// ── WebRTC (P2P) ──────────────────────────────────────────────────────────
function stopWebRTC() {
  if (webrtcPc) { try { webrtcPc.close(); } catch (_) {} webrtcPc = null; }
  const v = video();
  if (v.srcObject) {
    v.srcObject.getTracks().forEach(t => t.stop());
    v.srcObject = null;
  }
  v.style.display = 'none';
}

async function startWebRTC() {
  if (!window.RTCPeerConnection) return false;

  stopWebRTC();
  errorEl().classList.remove('visible');

  try {
    const pc = new RTCPeerConnection({
      iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    });
    webrtcPc = pc;

    // Prefer H.264 to match the server's encoding pipeline
    const tx = pc.addTransceiver('video', { direction: 'recvonly' });
    if (RTCRtpReceiver.getCapabilities) {
      const caps = RTCRtpReceiver.getCapabilities('video');
      if (caps) {
        const h264 = caps.codecs.filter(c => c.mimeType === 'video/H264');
        const rest = caps.codecs.filter(c => c.mimeType !== 'video/H264');
        if (h264.length) tx.setCodecPreferences([...h264, ...rest]);
      }
    }

    pc.ontrack = (event) => {
      if (!event.streams[0]) return;
      const v = video();
      v.srcObject = event.streams[0];
      v.style.display = 'block';
      mjpeg().style.display = 'none';
      errorEl().classList.remove('visible');
      v.play().catch(() => {});
    };

    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
        stopWebRTC();
        startMjpeg();
      }
    };

    // Create offer and wait for all local ICE candidates to be gathered
    // before sending — this way the server can answer in one HTTP round-trip.
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    await new Promise(resolve => {
      if (pc.iceGatheringState === 'complete') return resolve();
      const timeout = setTimeout(resolve, 3000);
      pc.onicegatheringstatechange = () => {
        if (pc.iceGatheringState === 'complete') { clearTimeout(timeout); resolve(); }
      };
    });

    // POST the complete offer — no WebSocket needed for signaling
    const res = await fetch('/offer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sdp: pc.localDescription.sdp, type: 'offer' }),
    });
    if (!res.ok) throw new Error(`/offer returned ${res.status}`);

    const answer = await res.json();
    if (answer.error) throw new Error(answer.error);

    await pc.setRemoteDescription({ type: 'answer', sdp: answer.sdp });

    // Wait up to 10 s for the video element to actually start playing
    await new Promise((resolve, reject) => {
      video().addEventListener('playing', resolve, { once: true });
      setTimeout(reject, 10000);
    });

    setMode('webrtc');
    return true;

  } catch (err) {
    console.warn('WebRTC failed — falling back to MJPEG:', err);
    stopWebRTC();
    return false;
  }
}

// ── MJPEG fallback ────────────────────────────────────────────────────────
function stopMjpeg() {
  const m = mjpeg();
  m.src = '';
  m.style.display = 'none';
  mjpegActive = false;
}

function startMjpeg() {
  stopWebRTC();
  stopMjpeg();
  errorEl().classList.remove('visible');

  const m = mjpeg();
  m.style.display = 'block';
  m.src = `/video_feed?t=${Date.now()}`; // cache-bust after a WebRTC failure
  mjpegActive = true;

  m.onerror = () => { if (mjpegActive) setTimeout(startMjpeg, 3000); };

  setMode('mjpeg');
}

// ── Main entry ────────────────────────────────────────────────────────────
async function startStream() {
  setMode('connecting');
  stopMjpeg();
  stopWebRTC();
  errorEl().classList.remove('visible');

  const ok = await startWebRTC();
  if (!ok) startMjpeg();
}

document.addEventListener('DOMContentLoaded', startStream);
