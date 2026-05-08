'use strict';

// ── State ──────────────────────────────────────────────────────────────────
let webrtcPc  = null;
let webrtcWs  = null;
let mjpegActive = false;

// ── Helpers ────────────────────────────────────────────────────────────────
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
  video().style.display  = 'none';
  mjpeg().style.display  = 'none';
  errorEl().classList.add('visible');
  setMode('error');
}

// ── WebRTC ─────────────────────────────────────────────────────────────────
function stopWebRTC() {
  if (webrtcWs) { try { webrtcWs.close(); } catch (_) {} webrtcWs = null; }
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

    // Prefer H.264 when available to match server encoding
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

    // Open WebSocket signaling channel
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${proto}//${location.host}/ws/webrtc`);
    webrtcWs = ws;

    await new Promise((resolve, reject) => {
      ws.onopen  = resolve;
      ws.onerror = reject;
      setTimeout(reject, 5000);
    });

    ws.onmessage = async (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'answer') {
        await pc.setRemoteDescription({ type: 'answer', sdp: msg.sdp });
      }
    };

    ws.onclose = () => {
      if (!video().srcObject) { stopWebRTC(); startMjpeg(); }
    };

    // Create offer and wait for ICE gathering to embed all candidates
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    await new Promise(resolve => {
      if (pc.iceGatheringState === 'complete') return resolve();
      const timeout = setTimeout(resolve, 3000);
      pc.onicegatheringstatechange = () => {
        if (pc.iceGatheringState === 'complete') {
          clearTimeout(timeout);
          resolve();
        }
      };
    });

    ws.send(JSON.stringify({ type: 'offer', sdp: pc.localDescription.sdp }));

    // Wait up to 10 s for the video element to start playing
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

// ── MJPEG fallback ─────────────────────────────────────────────────────────
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
  // cache-bust so the browser re-fetches after a WebRTC failure
  m.src = `/video_feed?t=${Date.now()}`;
  mjpegActive = true;

  m.onerror = () => {
    // retry after 3 s if the camera is not yet ready
    if (mjpegActive) setTimeout(startMjpeg, 3000);
  };

  setMode('mjpeg');
}

// ── Main entry ─────────────────────────────────────────────────────────────
async function startStream() {
  setMode('connecting');
  stopMjpeg();
  stopWebRTC();
  errorEl().classList.remove('visible');

  const ok = await startWebRTC();
  if (!ok) startMjpeg();
}

document.addEventListener('DOMContentLoaded', startStream);
