/*
  Virtual backgrounds — runs entirely in the browser (no server involved).
  Uses MediaPipe's Selfie Segmentation model to separate the person from the
  background on every frame, composites either a blur or a replacement image
  behind them onto a <canvas>, then feeds that canvas as the outgoing video
  track (same swap mechanism screen sharing uses).
*/
(function () {
  let mode = "none"; // "none" | "blur" | "image"
  let segmenter = null;
  let rawVideo = null;
  let canvas = null;
  let ctx = null;
  let bgImageEl = null;
  let active = false;
  let lastResults = null;

  function ensureCanvas() {
    if (canvas) return;
    canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 480;
    ctx = canvas.getContext("2d");
  }

  function ensureSegmenter() {
    if (segmenter) return segmenter;
    if (typeof SelfieSegmentation === "undefined") return null;
    segmenter = new SelfieSegmentation({
      locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/selfie_segmentation/${file}`,
    });
    segmenter.setOptions({ modelSelection: 1 });
    segmenter.onResults((results) => {
      lastResults = results;
    });
    return segmenter;
  }

  function drawFrame() {
    if (!active) return;
    if (lastResults) {
      const { segmentationMask, image } = lastResults;
      ctx.save();
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      ctx.drawImage(segmentationMask, 0, 0, canvas.width, canvas.height);
      ctx.globalCompositeOperation = "source-in";
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

      ctx.globalCompositeOperation = "destination-over";
      if (mode === "blur") {
        ctx.filter = "blur(14px)";
        ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
        ctx.filter = "none";
      } else if (mode === "image" && bgImageEl && bgImageEl.complete) {
        ctx.drawImage(bgImageEl, 0, 0, canvas.width, canvas.height);
      } else {
        ctx.fillStyle = "#0E1A22";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
      }
      ctx.restore();
    }
    requestAnimationFrame(drawFrame);
  }

  async function pumpFrames() {
    if (!active) return;
    if (rawVideo && rawVideo.readyState >= 2 && segmenter) {
      await segmenter.send({ image: rawVideo });
    }
    requestAnimationFrame(pumpFrames);
  }

  async function start(newMode) {
    if (active) return; // already running — mode var already updated by caller

    const camTrack = window._originalVideoTrack;
    if (!camTrack) return;

    if (!ensureSegmenter()) {
      console.warn("Virtual backgrounds unavailable (MediaPipe didn't load).");
      return;
    }

    ensureCanvas();
    rawVideo = document.createElement("video");
    rawVideo.srcObject = new MediaStream([camTrack]);
    rawVideo.muted = true;
    rawVideo.playsInline = true;
    try {
      await rawVideo.play();
    } catch (err) {
      console.warn("Could not start background processing:", err);
      return;
    }

    active = true;
    pumpFrames();
    drawFrame();

    const processedTrack = canvas.captureStream(24).getVideoTracks()[0];
    window._cameraOrBackgroundTrack = processedTrack;
    window.setLocalVideoTrack(processedTrack);
    window.replaceOutgoingVideoTrack(processedTrack);
  }

  function stop() {
    active = false;
    mode = "none";
    window._cameraOrBackgroundTrack = window._originalVideoTrack;
    window.setLocalVideoTrack(window._originalVideoTrack);
    window.replaceOutgoingVideoTrack(window._originalVideoTrack);
  }

  window.VirtualBackground = {
    async setMode(newMode, imageUrl) {
      if (newMode === "none") {
        stop();
        return;
      }
      mode = newMode;
      if (newMode === "image" && imageUrl) {
        bgImageEl = new Image();
        bgImageEl.crossOrigin = "anonymous";
        bgImageEl.src = imageUrl;
      }
      await start(newMode);
    },
    isActive() {
      return active;
    },
    currentMode() {
      return mode;
    },
  };
})();
