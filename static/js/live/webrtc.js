/*
  WebRTC mesh video calling for EIAVO Live Classes.

  - Every browser tab connects to the same Flask-SocketIO server for SIGNALING
    only (exchanging offers/answers/ICE candidates). Actual audio/video never
    touches the server — it flows directly between browsers (peer-to-peer).
  - The teacher hosting the class always renders into the large "spotlight" tile.
    Everyone else renders into the small filmstrip tiles below.
  - Students wait for teacher approval (waiting room) before joining the mesh,
    unless the teacher isn't in the room yet (fallback: let them straight in).
  - View can be toggled between "speaker" (spotlight + filmstrip) and
    "gallery" (uniform grid) — see setViewMode() below.
*/

const ICE_SERVERS = {
  iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
};

const socket = io();
window._socket = socket;

let localStream = null;
const peerConnections = {}; // sid -> RTCPeerConnection
const peerMeta = {}; // sid -> { name, isHost }
window._remoteStreams = {}; // sid -> { stream, name, isHost } — read by recorder.js
window.peerConnections = peerConnections;
window.peerMeta = peerMeta;

const { roomCode, isHost, userName, hostName } = window.ROOM_CONFIG;

let viewMode = "speaker"; // "speaker" | "gallery"

function notifyParticipantsChanged() {
  window.dispatchEvent(new CustomEvent("participants-changed"));
}

async function initMedia() {
  try {
    localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
  } catch (err) {
    console.warn("Camera/mic unavailable, joining audio/video-less:", err);
    localStream = new MediaStream();
  }

  window._localStreamRef = localStream;
  window._originalVideoTrack = localStream.getVideoTracks()[0] || null;
  // What we send when NOT screen sharing — plain camera by default, swapped
  // out by background.js when a virtual background is turned on.
  window._cameraOrBackgroundTrack = window._originalVideoTrack;

  // Show my own video immediately: spotlight if I'm the host, filmstrip otherwise.
  if (isHost) {
    renderSpotlightStream(localStream, `${userName}`, true);
  } else {
    upsertFilmstripTile("local", localStream, `${userName} (you)`, false);
  }

  socket.emit("live_request_to_join", { room_code: roomCode });
}

socket.on("join_approved", () => {
  window.dispatchEvent(new CustomEvent("admitted"));
  socket.emit("live_join", { room_code: roomCode });
});

<<<<<<< HEAD
=======
socket.on("waiting_for_host", () => {
  window.dispatchEvent(new CustomEvent("waiting-for-host"));
});

socket.on("join_denied", () => {
  window.dispatchEvent(new CustomEvent("join-denied"));
});

socket.on("join_request", (data) => {
  window.dispatchEvent(new CustomEvent("join-request", { detail: data }));
});
>>>>>>> e8a283c500fce796fe507c9644ad903c9829aa6c
socket.on("live_class_ended", () => {
  window.location.href = window.ROOM_CONFIG.classUrl;
});

function createPeerConnection(targetSid, meta) {
  const pc = new RTCPeerConnection(ICE_SERVERS);
  peerConnections[targetSid] = pc;
  peerMeta[targetSid] = meta;

  localStream.getTracks().forEach((track) => pc.addTrack(track, localStream));

  pc.onicecandidate = (event) => {
    if (event.candidate) {
      socket.emit("live_signal", {
        target: targetSid,
        signal: { type: "candidate", candidate: event.candidate },
      });
    }
  };

  pc.ontrack = (event) => {
    const stream = event.streams[0];
    window._remoteStreams[targetSid] = { stream, name: meta.name, isHost: meta.isHost };
    if (meta.isHost) {
      renderSpotlightStream(stream, meta.name, false);
    } else {
      upsertFilmstripTile(targetSid, stream, meta.name, true);
    }
    if (viewMode === "gallery") renderGalleryView();
  };

  pc.onconnectionstatechange = () => {
    if (["failed", "closed", "disconnected"].includes(pc.connectionState)) {
      teardownPeer(targetSid);
    }
  };

  notifyParticipantsChanged();
  return pc;
}

async function callPeer(targetSid, meta) {
  const pc = createPeerConnection(targetSid, meta);
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  socket.emit("live_signal", { target: targetSid, signal: { type: "offer", sdp: offer } });
}

socket.on("existing_peers", async ({ peers, recording }) => {
  window.dispatchEvent(new CustomEvent("recording-status", { detail: { recording: !!recording } }));
  for (const peer of peers) {
    await callPeer(peer.sid, { name: peer.name, isHost: peer.is_host });
  }
});

socket.on("peer_joined", ({ sid, name, is_host }) => {
  // The newcomer will call us (see existing_peers on their side), so we just wait.
  peerMeta[sid] = { name, isHost: is_host };
  notifyParticipantsChanged();
});

socket.on("signal", async ({ sender, signal }) => {
  let pc = peerConnections[sender];

  if (signal.type === "offer") {
    const meta = peerMeta[sender] || { name: "Participant", isHost: false };
    pc = pc || createPeerConnection(sender, meta);
    await pc.setRemoteDescription(new RTCSessionDescription(signal.sdp));
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    socket.emit("live_signal", { target: sender, signal: { type: "answer", sdp: answer } });
  } else if (signal.type === "answer") {
    if (pc) await pc.setRemoteDescription(new RTCSessionDescription(signal.sdp));
  } else if (signal.type === "candidate") {
    if (pc) {
      try {
        await pc.addIceCandidate(new RTCIceCandidate(signal.candidate));
      } catch (err) {
        console.warn("Failed to add ICE candidate", err);
      }
    }
  }
});

socket.on("peer_left", ({ sid }) => teardownPeer(sid));

function teardownPeer(sid) {
  const pc = peerConnections[sid];
  if (pc) {
    pc.close();
    delete peerConnections[sid];
  }
  const meta = peerMeta[sid];
  delete peerMeta[sid];
  delete window._remoteStreams[sid];

  if (meta && meta.isHost) {
    clearSpotlight();
  } else {
    removeFilmstripTile(sid);
  }
  notifyParticipantsChanged();
  if (viewMode === "gallery") renderGalleryView();
}

/* ---------------- Screen share / virtual background support ---------------- */

// Swaps the outgoing video track on every open peer connection (used by
// screen sharing and virtual backgrounds — anything that needs to replace
// what we're sending without a full renegotiation).
window.replaceOutgoingVideoTrack = function (newTrack) {
  Object.values(peerConnections).forEach((pc) => {
    const sender = pc.getSenders().find((s) => s.track && s.track.kind === "video");
    if (sender) sender.replaceTrack(newTrack);
  });
};

// Swaps the video track inside localStream itself, so every <video> element
// bound via srcObject=localStream (spotlight/filmstrip self-preview) updates
// automatically without re-binding.
window.setLocalVideoTrack = function (newTrack) {
  const oldTrack = localStream.getVideoTracks()[0];
  if (oldTrack) localStream.removeTrack(oldTrack);
  if (newTrack) localStream.addTrack(newTrack);
};

window.getLocalStream = function () {
  return localStream;
};

/* ---------------- DOM rendering ---------------- */

function renderSpotlightStream(stream, name, isSelf) {
  const tile = document.getElementById("spotlight-tile");
  const existingVideo = tile.querySelector("video");
  if (existingVideo) existingVideo.remove();
  const avatar = document.getElementById("spotlight-avatar");
  if (avatar) avatar.style.display = "none";

  const video = document.createElement("video");
  video.autoplay = true;
  video.playsInline = true;
  video.muted = isSelf;
  video.srcObject = stream;
  tile.insertBefore(video, tile.firstChild);

  const label = document.getElementById("spotlight-label");
  label.innerHTML = `<span class="host-badge">Teacher</span> ${name}${isSelf ? " (you)" : ""}`;
}

function clearSpotlight() {
  const tile = document.getElementById("spotlight-tile");
  const video = tile.querySelector("video");
  if (video) video.remove();
  const avatar = document.getElementById("spotlight-avatar");
  if (avatar) avatar.style.display = "flex";
  document.getElementById("spotlight-label").innerHTML =
    `<span class="host-badge">Teacher</span> ${hostName}`;
}

function upsertFilmstripTile(key, stream, name, isRemote) {
  const empty = document.getElementById("filmstrip-empty");
  if (empty) empty.remove();

  let tile = document.getElementById(`tile-${key}`);
  if (!tile) {
    tile = document.createElement("div");
    tile.className = "mini-tile";
    tile.id = `tile-${key}`;
    document.getElementById("filmstrip").appendChild(tile);
  }
  tile.innerHTML = "";
  tile.dataset.name = name;

  const video = document.createElement("video");
  video.autoplay = true;
  video.playsInline = true;
  video.muted = !isRemote; // mute local self-view to avoid echo
  video.srcObject = stream;
  tile.appendChild(video);

  const label = document.createElement("div");
  label.className = "tile-label";
  label.textContent = name;
  tile.appendChild(label);

  if (viewMode === "gallery") renderGalleryView();
}

function removeFilmstripTile(key) {
  const tile = document.getElementById(`tile-${key}`);
  if (tile) tile.remove();

  const strip = document.getElementById("filmstrip");
  if (!strip.querySelector(".mini-tile")) {
    const empty = document.createElement("div");
    empty.className = "filmstrip-empty";
    empty.id = "filmstrip-empty";
    empty.textContent = "Waiting for others to join…";
    strip.appendChild(empty);
  }
}

// Sets/clears a small ✋ badge on a participant's tile label.
window.setTileHandRaised = function (sid, raised) {
  const key = sid === "local" ? "local" : sid;
  const isSpotlightSelf = isHost && sid === "local";
  const isSpotlightHost = peerMeta[sid] && peerMeta[sid].isHost;

  let labelEl;
  if (isSpotlightSelf || isSpotlightHost) {
    labelEl = document.getElementById("spotlight-label");
  } else {
    const tile = document.getElementById(`tile-${key}`);
    labelEl = tile ? tile.querySelector(".tile-label") : null;
  }
  if (!labelEl) return;

  const existingBadge = labelEl.querySelector(".hand-badge");
  if (raised && !existingBadge) {
    const badge = document.createElement("span");
    badge.className = "hand-badge";
    badge.textContent = " ✋";
    labelEl.appendChild(badge);
  } else if (!raised && existingBadge) {
    existingBadge.remove();
  }
};

// Sets/clears a "Presenting" badge on a participant's tile.
window.setTileScreenSharing = function (sid, sharing) {
  const tile = peerMeta[sid] && peerMeta[sid].isHost
    ? document.getElementById("spotlight-tile")
    : document.getElementById(`tile-${sid}`);
  if (!tile) return;
  let badge = tile.querySelector(".presenting-badge");
  if (sharing && !badge) {
    badge = document.createElement("div");
    badge.className = "presenting-badge";
    badge.textContent = "🖥️ Presenting";
    tile.appendChild(badge);
  } else if (!sharing && badge) {
    badge.remove();
  }
};

/* ---------------- View mode: speaker vs gallery ---------------- */

window.setViewMode = function (mode) {
  viewMode = mode;
  const stageArea = document.getElementById("stage-area");
  const gallery = document.getElementById("gallery-grid");
  if (mode === "gallery") {
    stageArea.style.display = "none";
    gallery.style.display = "grid";
    renderGalleryView();
  } else {
    gallery.style.display = "none";
    stageArea.style.display = "flex";
    // Move tiles back to their normal homes (moving, not cloning, keeps
    // the <video> elements' live playback state intact).
    document.querySelector(".stage-area").insertBefore(
      document.getElementById("spotlight-tile"),
      document.getElementById("filmstrip-wrap")
    );
    const filmstrip = document.getElementById("filmstrip");
    Array.from(gallery.querySelectorAll(".mini-tile")).forEach((t) => filmstrip.appendChild(t));
    if (!filmstrip.querySelector(".mini-tile")) {
      const empty = document.createElement("div");
      empty.className = "filmstrip-empty";
      empty.id = "filmstrip-empty";
      empty.textContent = "Waiting for others to join…";
      filmstrip.appendChild(empty);
    }
  }
  window.dispatchEvent(new CustomEvent("view-mode-changed", { detail: { mode } }));
};

function renderGalleryView() {
  const gallery = document.getElementById("gallery-grid");
  const spotlight = document.getElementById("spotlight-tile");
  gallery.appendChild(spotlight); // move (not clone) — preserves video playback
  document.querySelectorAll("#filmstrip .mini-tile").forEach((t) => gallery.appendChild(t));
}

window.getViewMode = function () {
  return viewMode;
};

initMedia();
