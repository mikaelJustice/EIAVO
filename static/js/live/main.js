document.addEventListener("DOMContentLoaded", () => {
  const btnMic = document.getElementById("btn-mic");
  const btnCam = document.getElementById("btn-cam");
  const btnLeave = document.getElementById("btn-leave");
  const btnEndMeeting = document.getElementById("btn-end-meeting");
  const btnNotes = document.getElementById("btn-notes");
  const notesPanel = document.getElementById("notes-panel");
  const notesClose = document.getElementById("notes-close");
  const notesForm = document.getElementById("notes-form");
  const notesInput = document.getElementById("notes-input");
  const notesList = document.getElementById("notes-list");

  let micOn = true;
  let camOn = true;
  let screenSharing = false;

  /* ---------------- Screen sharing ---------------- */
  const btnScreenShare = document.getElementById("btn-screen-share");
  if (btnScreenShare) {
    btnScreenShare.addEventListener("click", async () => {
      if (!screenSharing) {
        let screenStream;
        try {
          screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true });
        } catch (err) {
          return; // user cancelled the picker
        }
        const screenTrack = screenStream.getVideoTracks()[0];

        window.replaceOutgoingVideoTrack(screenTrack);
        window.setLocalVideoTrack(screenTrack);
        screenSharing = true;
        btnScreenShare.classList.add("active-on");
        btnScreenShare.title = "Stop presenting";
        window._socket.emit("live_screen_share", { sharing: true });

        // If the user stops sharing via the browser's native "Stop sharing"
        // control (rather than our button), fall back cleanly too.
        screenTrack.onended = () => stopScreenShare();
      } else {
        stopScreenShare();
      }
    });
  }

  function stopScreenShare() {
    if (!screenSharing) return;
    const restoreTrack = window._cameraOrBackgroundTrack || window._originalVideoTrack;
    window.replaceOutgoingVideoTrack(restoreTrack);
    window.setLocalVideoTrack(restoreTrack);
    screenSharing = false;
    btnScreenShare.classList.remove("active-on");
    btnScreenShare.title = "Share your screen";
    window._socket.emit("live_screen_share", { sharing: false });
  }

  /* ---------------- Virtual backgrounds ---------------- */
  const btnBackground = document.getElementById("btn-background");
  const backgroundMenu = document.getElementById("background-menu");
  if (btnBackground && backgroundMenu) {
    btnBackground.addEventListener("click", () => {
      backgroundMenu.classList.toggle("open");
    });
    backgroundMenu.querySelectorAll("[data-bg-mode]").forEach((opt) => {
      opt.addEventListener("click", async () => {
        const bgMode = opt.dataset.bgMode;
        const bgImage = opt.dataset.bgImage || null;
        backgroundMenu.querySelectorAll("[data-bg-mode]").forEach((o) => o.classList.remove("selected"));
        opt.classList.add("selected");
        backgroundMenu.classList.remove("open");
        if (window.VirtualBackground) {
          await window.VirtualBackground.setMode(bgMode, bgImage);
        }
      });
    });
    document.addEventListener("click", (e) => {
      if (!backgroundMenu.contains(e.target) && e.target !== btnBackground) {
        backgroundMenu.classList.remove("open");
      }
    });
  }

  btnMic.addEventListener("click", () => {
    if (!window.localStream) return;
    micOn = !micOn;
    window.localStream.getAudioTracks().forEach((t) => (t.enabled = micOn));
    btnMic.classList.toggle("active-off", !micOn);
    btnMic.querySelector(".icon").innerHTML = window.ICONS[micOn ? "mic" : "micOff"];
    btnMic.querySelector(".ctrl-label").textContent = micOn ? "Mute" : "Unmute";
    delete btnMic.dataset.forcedMute;
  });

  btnCam.addEventListener("click", () => {
    if (!window.localStream) return;
    camOn = !camOn;
    window.localStream.getVideoTracks().forEach((t) => (t.enabled = camOn));
    btnCam.classList.toggle("active-off", !camOn);
    btnCam.querySelector(".icon").innerHTML = window.ICONS[camOn ? "video" : "videoOff"];
    btnCam.querySelector(".ctrl-label").textContent = camOn ? "Stop video" : "Start video";
  });

  /* ---------------- Recording: teacher decides, everyone sees status live ---------------- */
  const recordingNotice = document.getElementById("recording-notice");

  function setRecordingBanner(isRecording) {
    if (!recordingNotice) return;
    recordingNotice.style.display = isRecording ? "inline-flex" : "none";
  }

  // Late joiners learn the current status from webrtc.js's existing_peers payload.
  window.addEventListener("recording-status", (e) => setRecordingBanner(e.detail.recording));

  if (window._socket) {
    window._socket.on("recording_status", ({ recording }) => setRecordingBanner(recording));
  }

  const btnRecord = document.getElementById("btn-record");
  const recordConsentOverlay = document.getElementById("record-consent-overlay");
  const btnConsentRecord = document.getElementById("btn-consent-record");
  const btnConsentSkip = document.getElementById("btn-consent-skip");

  function setRecordButtonUI(isRecording) {
    if (!btnRecord) return;
    const icon = btnRecord.querySelector(".icon");
    const label = btnRecord.querySelector(".ctrl-label");
    if (icon && window.ICONS) icon.innerHTML = window.ICONS[isRecording ? "stopRecord" : "record"];
    if (label) label.textContent = isRecording ? "Stop" : "Record";
    btnRecord.classList.toggle("active-on", isRecording);
    btnRecord.title = isRecording ? "Stop recording" : "Start recording";
  }

  function beginRecording() {
    if (window.RecordingControl) window.RecordingControl.start();
    window._socket.emit("live_recording_status", { room_code: window.ROOM_CONFIG.roomCode, recording: true });
    setRecordingBanner(true);
    setRecordButtonUI(true);
  }

  async function endRecording() {
    if (window.RecordingControl) await window.RecordingControl.stop();
    window._socket.emit("live_recording_status", { room_code: window.ROOM_CONFIG.roomCode, recording: false });
    setRecordingBanner(false);
    setRecordButtonUI(false);
  }

  if (btnRecord) {
    btnRecord.addEventListener("click", () => {
      const isActive = window.RecordingControl && window.RecordingControl.isActive();
      if (isActive) endRecording();
      else beginRecording();
    });
  }

  if (recordConsentOverlay) {
    // Teacher sees this once, right after joining their own live class.
    window.addEventListener("admitted", () => recordConsentOverlay.classList.add("show"), { once: true });

    if (btnConsentRecord) {
      btnConsentRecord.addEventListener("click", () => {
        beginRecording();
        recordConsentOverlay.classList.remove("show");
      });
    }
    if (btnConsentSkip) {
      btnConsentSkip.addEventListener("click", () => {
        recordConsentOverlay.classList.remove("show");
      });
    }
  }

  if (btnLeave) {
    btnLeave.addEventListener("click", () => {
      window.location.href = window.ROOM_CONFIG.classUrl;
    });
  }

  if (btnEndMeeting) {
    btnEndMeeting.addEventListener("click", async () => {
      btnEndMeeting.disabled = true;
      btnEndMeeting.textContent = "Ending…";
      try {
        if (window.stopRecordingAndWait) {
          await window.stopRecordingAndWait();
        }
        const res = await fetch(window.ROOM_CONFIG.endUrl, { method: "POST" });
        const data = await res.json();
        window.location.href = data.redirect || window.ROOM_CONFIG.classUrl;
      } catch (err) {
        console.error("Failed to end class cleanly:", err);
        window.location.href = window.ROOM_CONFIG.classUrl;
      }
    });
  }

  function loadNotes() {
    fetch(window.ROOM_CONFIG.notesUrl)
      .then((r) => r.json())
      .then((data) => renderNotes(data.notes));
  }

  function renderNotes(notes) {
    notesList.innerHTML = "";
    if (!notes.length) {
      notesList.innerHTML = '<div class="filmstrip-empty" id="notes-empty">No notes yet for this class.</div>';
      return;
    }
    notes.forEach((n) => appendNoteToDom(n, false));
    notesList.scrollTop = notesList.scrollHeight;
  }

  function appendNoteToDom(n, scrollIntoView) {
    const empty = document.getElementById("notes-empty");
    if (empty) empty.remove();

    const item = document.createElement("div");
    item.className = "note-item";
    item.innerHTML = `
      <div class="note-meta">${escapeHtml(n.author)} · ${escapeHtml(n.created_at)}</div>
      <div class="note-content"></div>
    `;
    item.querySelector(".note-content").textContent = n.content;
    notesList.appendChild(item);
    if (scrollIntoView) notesList.scrollTop = notesList.scrollHeight;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  let notesUnread = 0;
  const notesUnreadBadge = document.getElementById("notes-unread");

  btnNotes.addEventListener("click", () => {
    notesPanel.classList.add("open");
    notesUnread = 0;
    if (notesUnreadBadge) notesUnreadBadge.style.display = "none";
    loadNotes();
  });
  notesClose.addEventListener("click", () => notesPanel.classList.remove("open"));

  notesForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const content = notesInput.value.trim();
    if (!content) return;
    notesInput.value = "";
    fetch(window.ROOM_CONFIG.notesUrl, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `content=${encodeURIComponent(content)}`,
    }).catch((err) => console.warn("Failed to save note:", err));
    // The note itself gets added to the panel via the "note_added" socket
    // broadcast below (the server pushes it to everyone in the room,
    // including us), so there's nothing else to do here.
  });

  // Live push: every participant (including whoever just wrote it) gets
  // notes the moment they're saved — no more closing/reopening the panel.
  if (window._socket) {
    window._socket.on("note_added", (note) => {
      appendNoteToDom(note, true);
      if (!notesPanel.classList.contains("open")) {
        notesUnread += 1;
        if (notesUnreadBadge) {
          notesUnreadBadge.textContent = notesUnread;
          notesUnreadBadge.style.display = "inline-flex";
        }
      }
    });
  }
});

// expose localStream to main.js once webrtc.js sets it up
Object.defineProperty(window, "localStream", {
  get() {
    return window._localStreamRef;
  },
});
