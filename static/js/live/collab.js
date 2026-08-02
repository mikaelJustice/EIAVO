document.addEventListener("DOMContentLoaded", () => {
  const socket = window._socket;
  const { roomCode, isHost, userName } = window.ROOM_CONFIG;

  /* =========================================================
     Participants panel
     ========================================================= */
  const participantsPanel = document.getElementById("participants-panel");
  const participantsList = document.getElementById("participants-list");
  const btnParticipants = document.getElementById("btn-participants");
  const participantsClose = document.getElementById("participants-close");
  const btnMuteAll = document.getElementById("btn-mute-all");

  const raisedHands = new Set(); // sids (or "local") with hand raised

  function renderParticipants() {
    if (!participantsList) return;
    participantsList.innerHTML = "";

    const entries = [{ sid: "local", name: `${userName} (you)`, isHost }];
    Object.entries(window.peerMeta || {}).forEach(([sid, meta]) => {
      entries.push({ sid, name: meta.name, isHost: meta.isHost });
    });
    // Teacher first, then alphabetical.
    entries.sort((a, b) => (b.isHost - a.isHost) || a.name.localeCompare(b.name));

    entries.forEach((p) => {
      const row = document.createElement("div");
      row.className = "participant-row";
      const handIcon = raisedHands.has(p.sid) ? " ✋" : "";
      row.innerHTML = `
        <span class="participant-name">${p.isHost ? '<span class="host-badge">Teacher</span> ' : ""}${escapeHtml(p.name)}${handIcon}</span>
      `;
      if (isHost && p.sid !== "local") {
        const removeBtn = document.createElement("button");
        removeBtn.className = "btn btn-ghost btn-sm";
        removeBtn.textContent = "Remove";
        removeBtn.addEventListener("click", () => {
          if (confirm(`Remove ${p.name} from the live class?`)) {
            socket.emit("live_host_remove_participant", { room_code: roomCode, sid: p.sid });
          }
        });
        row.appendChild(removeBtn);
      }
      participantsList.appendChild(row);
    });
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  window.addEventListener("participants-changed", renderParticipants);
  window.addEventListener("admitted", renderParticipants);

  if (btnParticipants) {
    btnParticipants.addEventListener("click", () => {
      participantsPanel.classList.add("open");
      renderParticipants();
    });
  }
  if (participantsClose) {
    participantsClose.addEventListener("click", () => participantsPanel.classList.remove("open"));
  }
  if (btnMuteAll) {
    btnMuteAll.addEventListener("click", () => {
      socket.emit("live_host_mute_all", { room_code: roomCode });
    });
  }

  /* =========================================================
     Chat
     ========================================================= */
  const chatPanel = document.getElementById("chat-panel");
  const chatMessages = document.getElementById("chat-messages");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const btnChat = document.getElementById("btn-chat");
  const chatClose = document.getElementById("chat-close");
  const chatUnreadBadge = document.getElementById("chat-unread");
  let unread = 0;

  function appendChatMessage(name, message, isSelf) {
    const item = document.createElement("div");
    item.className = "chat-item" + (isSelf ? " chat-item-self" : "");
    item.innerHTML = `<div class="chat-meta"></div><div class="chat-bubble"></div>`;
    item.querySelector(".chat-meta").textContent = name;
    item.querySelector(".chat-bubble").textContent = message;
    chatMessages.appendChild(item);
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  if (socket) {
    socket.on("chat_message", ({ name, message }) => {
      const isSelf = name === userName;
      appendChatMessage(name, message, isSelf);
      if (!chatPanel.classList.contains("open") && !isSelf) {
        unread += 1;
        if (chatUnreadBadge) {
          chatUnreadBadge.textContent = unread;
          chatUnreadBadge.style.display = "inline-flex";
        }
      }
    });
  }

  if (btnChat) {
    btnChat.addEventListener("click", () => {
      chatPanel.classList.add("open");
      unread = 0;
      if (chatUnreadBadge) chatUnreadBadge.style.display = "none";
      chatInput.focus();
    });
  }
  if (chatClose) {
    chatClose.addEventListener("click", () => chatPanel.classList.remove("open"));
  }
  if (chatForm) {
    chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const message = chatInput.value.trim();
      if (!message) return;
      socket.emit("live_chat_message", { message });
      chatInput.value = "";
    });
  }

  /* =========================================================
     Reactions
     ========================================================= */
  const reactionToasts = document.getElementById("reaction-toasts");
  const reactionButtons = document.querySelectorAll(".reaction-btn");

  reactionButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const emoji = btn.dataset.emoji;
      socket.emit("live_reaction", { emoji });
      showReactionToast(userName, emoji);
    });
  });

  socket.on("reaction", ({ name, emoji }) => {
    if (name !== userName) showReactionToast(name, emoji);
  });

  function showReactionToast(name, emoji) {
    if (!reactionToasts) return;
    const toast = document.createElement("div");
    toast.className = "reaction-toast";
    toast.innerHTML = `<span class="reaction-emoji">${emoji}</span><span class="reaction-name"></span>`;
    toast.querySelector(".reaction-name").textContent = name;
    reactionToasts.appendChild(toast);
    setTimeout(() => toast.remove(), 2600);
  }

  /* =========================================================
     Hand raise
     ========================================================= */
  const btnHandRaise = document.getElementById("btn-hand-raise");
  let handRaised = false;

  if (btnHandRaise) {
    btnHandRaise.addEventListener("click", () => {
      handRaised = !handRaised;
      btnHandRaise.classList.toggle("active-on", handRaised);
      socket.emit("live_hand_raise", { raised: handRaised });
      if (handRaised) raisedHands.add("local"); else raisedHands.delete("local");
      if (window.setTileHandRaised) window.setTileHandRaised("local", handRaised);
      renderParticipants();
    });
  }

  socket.on("hand_raise", ({ sid, raised }) => {
    if (raised) raisedHands.add(sid); else raisedHands.delete(sid);
    if (window.setTileHandRaised) window.setTileHandRaised(sid, raised);
    renderParticipants();
  });

  /* =========================================================
     Screen share presence badges
     ========================================================= */
  socket.on("screen_share", ({ sid, sharing }) => {
    if (window.setTileScreenSharing) window.setTileScreenSharing(sid, sharing);
  });

  /* =========================================================
     Host controls: force mute / removal
     ========================================================= */
  socket.on("force_mute", () => {
    const stream = window.getLocalStream ? window.getLocalStream() : window.localStream;
    if (stream) stream.getAudioTracks().forEach((t) => (t.enabled = false));
    const btnMic = document.getElementById("btn-mic");
    if (btnMic) {
      btnMic.classList.add("active-off");
      const icon = btnMic.querySelector(".icon");
      const label = btnMic.querySelector(".ctrl-label");
      if (icon && window.ICONS) icon.innerHTML = window.ICONS.micOff;
      if (label) label.textContent = "Unmute";
      btnMic.dataset.forcedMute = "true";
    }
    showReactionToast("Teacher", "🔇 muted everyone");
  });

  socket.on("removed_by_host", () => {
    alert("The teacher has removed you from this live class.");
    window.location.href = window.ROOM_CONFIG.classUrl;
  });

  /* =========================================================
     View mode toggle (speaker / gallery)
     ========================================================= */
  const btnViewMode = document.getElementById("btn-view-mode");
  if (btnViewMode) {
    btnViewMode.addEventListener("click", () => {
      const next = window.getViewMode() === "gallery" ? "speaker" : "gallery";
      window.setViewMode(next);
      const icon = btnViewMode.querySelector(".icon");
      const label = btnViewMode.querySelector(".ctrl-label");
      if (icon && window.ICONS) icon.innerHTML = window.ICONS[next === "gallery" ? "speakerView" : "grid"];
      if (label) label.textContent = next === "gallery" ? "Speaker" : "Layout";
      btnViewMode.title = next === "gallery" ? "Switch to speaker view" : "Switch to gallery view";
    });
  }
});
