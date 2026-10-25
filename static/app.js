(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const clampPercent = (value) => Math.max(0, Math.min(100, Math.round(Number(value) || 0)));

  const previewCanvas = $("preview");
  const previewCtx = previewCanvas.getContext("2d", { alpha: false });
  const sceneCanvas = document.createElement("canvas");
  sceneCanvas.width = 720;
  sceneCanvas.height = 470;
  const sceneCtx = sceneCanvas.getContext("2d", { alpha: false, willReadFrequently: true });

  const elements = {
    slider: $("isolation"),
    percentValue: $("percentValue"),
    stateWord: $("stateWord"),
    panel: document.querySelector(".control-panel"),
    status: $("status"),
    statusLabel: $("statusLabel"),
    displayName: $("displayName"),
    previewBadge: $("previewBadge"),
    crtcLabel: $("crtcLabel"),
    redBar: $("redBar"),
    greenBar: $("greenBar"),
    blueBar: $("blueBar"),
    redValue: $("redValue"),
    greenValue: $("greenValue"),
    blueValue: $("blueValue"),
    greenRole: $("greenRole"),
    blueRole: $("blueRole"),
    greenChannel: document.querySelector('[data-channel="green"]'),
    blueChannel: document.querySelector('[data-channel="blue"]'),
    redChannel: document.querySelector('[data-channel="red"]'),
    restoreButton: $("restoreButton"),
    presets: Array.from(document.querySelectorAll(".preset")),
    errorMessage: $("errorMessage"),
    errorText: $("errorText"),
    toast: $("toast"),
  };

  const state = {
    ready: false,
    supported: false,
    transportError: false,
    percent: 0,
  };

  const pending = {
    value: null,
    inFlight: false,
    timer: null,
    lastAt: 0,
  };

  let basePixels = null;
  let workingPixels = null;
  let previewFrame = null;
  let previewTarget = 0;
  let toastTimer = null;
  const swatches = Array.from(document.querySelectorAll(".swatch")).map((element) => {
    const [red, green, blue] = element.dataset.rgb.split(",").map(Number);
    return { element, red, green, blue };
  });

  // ---------------------------------------------------------------------------
  // Preview scene
  // ---------------------------------------------------------------------------

  function cloud(ctx, x, y, scale, opacity) {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(scale, scale);
    ctx.globalAlpha = opacity;
    ctx.fillStyle = "#f1eee7";
    ctx.beginPath();
    ctx.ellipse(0, 0, 34, 20, 0, 0, Math.PI * 2);
    ctx.ellipse(34, 6, 28, 16, 0, 0, Math.PI * 2);
    ctx.ellipse(-33, 8, 25, 14, 0, 0, Math.PI * 2);
    ctx.ellipse(5, 13, 50, 12, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  function bird(ctx, x, y, scale) {
    ctx.beginPath();
    ctx.moveTo(x - 7 * scale, y);
    ctx.quadraticCurveTo(x - 2 * scale, y - 4 * scale, x, y);
    ctx.quadraticCurveTo(x + 2 * scale, y - 4 * scale, x + 7 * scale, y);
    ctx.strokeStyle = "rgba(22, 29, 42, 0.52)";
    ctx.lineWidth = 1.2;
    ctx.stroke();
  }

  function drawBalloon(ctx, x, y, scale) {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(scale, scale);

    const envelope = ctx.createLinearGradient(-58, -20, 58, 50);
    envelope.addColorStop(0, "#f0ac44");
    envelope.addColorStop(0.35, "#e54f34");
    envelope.addColorStop(0.7, "#c92f29");
    envelope.addColorStop(1, "#7c1c1c");
    ctx.beginPath();
    ctx.moveTo(0, -70);
    ctx.bezierCurveTo(48, -70, 65, -35, 50, 0);
    ctx.bezierCurveTo(41, 31, 21, 53, 8, 69);
    ctx.lineTo(0, 76);
    ctx.lineTo(-8, 69);
    ctx.bezierCurveTo(-21, 53, -41, 31, -50, 0);
    ctx.bezierCurveTo(-65, -35, -48, -70, 0, -70);
    ctx.closePath();
    ctx.fillStyle = envelope;
    ctx.fill();

    ctx.strokeStyle = "rgba(255, 220, 165, 0.35)";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(-30, -58);
    ctx.bezierCurveTo(-38, -22, -27, 27, 0, 75);
    ctx.moveTo(0, -70);
    ctx.bezierCurveTo(2, -25, 0, 28, 0, 76);
    ctx.moveTo(30, -58);
    ctx.bezierCurveTo(38, -22, 27, 27, 0, 75);
    ctx.stroke();

    ctx.fillStyle = "rgba(255, 225, 162, 0.42)";
    ctx.beginPath();
    ctx.ellipse(-13, -43, 12, 25, -0.3, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = "rgba(66, 40, 26, 0.8)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(-15, 60);
    ctx.lineTo(-10, 89);
    ctx.moveTo(15, 60);
    ctx.lineTo(10, 89);
    ctx.stroke();

    ctx.fillStyle = "#4b2c1b";
    ctx.fillRect(-14, 86, 28, 17);
    ctx.fillStyle = "rgba(241, 187, 105, 0.45)";
    ctx.fillRect(-11, 89, 22, 2);

    ctx.restore();
  }

  function drawBarn(ctx) {
    ctx.save();

    ctx.fillStyle = "rgba(17, 31, 18, 0.32)";
    ctx.beginPath();
    ctx.ellipse(217, 371, 121, 18, 0, 0, Math.PI * 2);
    ctx.fill();

    const wall = ctx.createLinearGradient(145, 265, 305, 380);
    wall.addColorStop(0, "#d84a38");
    wall.addColorStop(0.58, "#c1342a");
    wall.addColorStop(1, "#8f211d");
    ctx.fillStyle = wall;
    ctx.fillRect(145, 262, 160, 108);

    ctx.fillStyle = "rgba(58, 16, 13, 0.3)";
    ctx.fillRect(257, 262, 48, 108);

    ctx.beginPath();
    ctx.moveTo(123, 268);
    ctx.lineTo(225, 204);
    ctx.lineTo(327, 268);
    ctx.closePath();
    ctx.fillStyle = "#481917";
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(132, 266);
    ctx.lineTo(225, 211);
    ctx.lineTo(318, 266);
    ctx.strokeStyle = "rgba(255, 170, 142, 0.32)";
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.fillStyle = "#efe1ce";
    ctx.fillRect(145, 270, 160, 6);

    ctx.strokeStyle = "rgba(75, 24, 19, 0.28)";
    ctx.lineWidth = 1;
    for (let x = 166; x < 300; x += 20) {
      ctx.beginPath();
      ctx.moveTo(x, 276);
      ctx.lineTo(x, 366);
      ctx.stroke();
    }

    ctx.fillStyle = "#40231b";
    ctx.fillRect(196, 313, 58, 57);
    ctx.strokeStyle = "#e6d6c3";
    ctx.lineWidth = 3;
    ctx.strokeRect(196, 313, 58, 57);
    ctx.beginPath();
    ctx.moveTo(199, 316);
    ctx.lineTo(251, 367);
    ctx.moveTo(251, 316);
    ctx.lineTo(199, 367);
    ctx.strokeStyle = "rgba(230, 214, 195, 0.56)";
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.fillStyle = "#f0e5d5";
    ctx.fillRect(266, 286, 29, 25);
    ctx.fillStyle = "#342822";
    ctx.fillRect(270, 290, 21, 17);
    ctx.fillStyle = "#f0e5d5";
    ctx.fillRect(279, 288, 3, 21);
    ctx.fillRect(268, 298, 25, 2);

    ctx.fillStyle = "#f0e5d5";
    ctx.beginPath();
    ctx.moveTo(225, 219);
    ctx.lineTo(245, 235);
    ctx.lineTo(225, 251);
    ctx.lineTo(205, 235);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#481917";
    ctx.beginPath();
    ctx.moveTo(225, 225);
    ctx.lineTo(238, 235);
    ctx.lineTo(225, 245);
    ctx.lineTo(212, 235);
    ctx.closePath();
    ctx.fill();

    ctx.restore();
  }

  function drawFence(ctx) {
    ctx.save();
    ctx.strokeStyle = "rgba(239, 229, 210, 0.7)";
    ctx.lineWidth = 3;
    ctx.lineCap = "round";
    const posts = [
      [55, 365],
      [86, 361],
      [117, 357],
      [148, 354],
    ];
    for (const [x, y] of posts) {
      ctx.beginPath();
      ctx.moveTo(x, y + 12);
      ctx.lineTo(x, y - 25);
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.moveTo(50, 350);
    ctx.lineTo(153, 340);
    ctx.moveTo(52, 362);
    ctx.lineTo(154, 352);
    ctx.strokeStyle = "rgba(239, 229, 210, 0.52)";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();
  }

  function drawScene() {
    const ctx = sceneCtx;
    const W = sceneCanvas.width;
    const H = sceneCanvas.height;

    const sky = ctx.createLinearGradient(0, 0, 0, 338);
    sky.addColorStop(0, "#0b173d");
    sky.addColorStop(0.34, "#1d4b9e");
    sky.addColorStop(0.68, "#3f88c5");
    sky.addColorStop(1, "#9ac9c5");
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, W, 338);

    const haze = ctx.createLinearGradient(0, 244, 0, 342);
    haze.addColorStop(0, "rgba(242, 182, 147, 0)");
    haze.addColorStop(0.7, "rgba(242, 182, 147, 0.18)");
    haze.addColorStop(1, "rgba(242, 182, 147, 0.03)");
    ctx.fillStyle = haze;
    ctx.fillRect(0, 244, W, 98);

    const glow = ctx.createRadialGradient(593, 102, 4, 593, 102, 126);
    glow.addColorStop(0, "rgba(255, 244, 190, 0.96)");
    glow.addColorStop(0.23, "rgba(255, 215, 118, 0.52)");
    glow.addColorStop(0.58, "rgba(255, 170, 83, 0.15)");
    glow.addColorStop(1, "rgba(255, 170, 83, 0)");
    ctx.fillStyle = glow;
    ctx.fillRect(455, 0, 265, 260);

    ctx.beginPath();
    ctx.arc(593, 102, 35, 0, Math.PI * 2);
    ctx.fillStyle = "#ffea9f";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(593, 102, 44, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(255, 236, 172, 0.2)";
    ctx.lineWidth = 1;
    ctx.stroke();

    cloud(ctx, 118, 122, 1.04, 0.6);
    cloud(ctx, 342, 72, 0.6, 0.44);
    cloud(ctx, 610, 220, 0.62, 0.27);
    cloud(ctx, 208, 213, 0.46, 0.2);

    drawBalloon(ctx, 489, 125, 0.94);

    bird(ctx, 359, 137, 0.8);
    bird(ctx, 388, 126, 0.62);
    bird(ctx, 414, 146, 0.48);

    const farMountains = ctx.createLinearGradient(0, 170, 0, 334);
    farMountains.addColorStop(0, "#58618f");
    farMountains.addColorStop(1, "#30365f");
    ctx.fillStyle = farMountains;
    ctx.beginPath();
    ctx.moveTo(0, 304);
    ctx.lineTo(66, 244);
    ctx.lineTo(128, 281);
    ctx.lineTo(210, 188);
    ctx.lineTo(283, 267);
    ctx.lineTo(351, 220);
    ctx.lineTo(426, 279);
    ctx.lineTo(507, 201);
    ctx.lineTo(576, 258);
    ctx.lineTo(644, 220);
    ctx.lineTo(720, 268);
    ctx.lineTo(720, 344);
    ctx.lineTo(0, 344);
    ctx.closePath();
    ctx.fill();

    ctx.fillStyle = "rgba(233, 237, 241, 0.52)";
    for (const [x, y] of [
      [210, 188],
      [507, 201],
      [644, 220],
    ]) {
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x - 23, y + 34);
      ctx.lineTo(x - 5, y + 29);
      ctx.lineTo(x + 4, y + 37);
      ctx.lineTo(x + 18, y + 31);
      ctx.closePath();
      ctx.fill();
    }

    const middleHill = ctx.createLinearGradient(0, 255, 0, 390);
    middleHill.addColorStop(0, "#4b8760");
    middleHill.addColorStop(1, "#214e38");
    ctx.fillStyle = middleHill;
    ctx.beginPath();
    ctx.moveTo(0, 300);
    ctx.bezierCurveTo(106, 250, 218, 264, 330, 310);
    ctx.bezierCurveTo(466, 278, 588, 263, 720, 301);
    ctx.lineTo(720, 470);
    ctx.lineTo(0, 470);
    ctx.closePath();
    ctx.fill();

    const field = ctx.createLinearGradient(0, 306, 0, 470);
    field.addColorStop(0, "#4f8b42");
    field.addColorStop(0.55, "#3c6d34");
    field.addColorStop(1, "#1e381f");
    ctx.fillStyle = field;
    ctx.beginPath();
    ctx.moveTo(0, 336);
    ctx.bezierCurveTo(118, 314, 222, 334, 340, 348);
    ctx.bezierCurveTo(451, 328, 581, 315, 720, 342);
    ctx.lineTo(720, 470);
    ctx.lineTo(0, 470);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = "rgba(255, 226, 127, 0.35)";
    ctx.lineWidth = 1.2;
    for (const [x, y] of [
      [44, 411],
      [68, 424],
      [96, 407],
      [589, 407],
      [624, 431],
      [661, 417],
      [695, 444],
    ]) {
      ctx.beginPath();
      ctx.moveTo(x, y + 5);
      ctx.lineTo(x + 3, y - 10);
      ctx.stroke();
    }

    const shoulder = ctx.createLinearGradient(0, 330, 0, 470);
    shoulder.addColorStop(0, "rgba(92, 69, 50, 0.22)");
    shoulder.addColorStop(1, "rgba(79, 57, 42, 0.82)");
    ctx.fillStyle = shoulder;
    ctx.beginPath();
    ctx.moveTo(234, 470);
    ctx.bezierCurveTo(302, 422, 330, 372, 379, 329);
    ctx.lineTo(443, 329);
    ctx.bezierCurveTo(434, 378, 481, 430, 556, 470);
    ctx.closePath();
    ctx.fill();

    const road = ctx.createLinearGradient(0, 332, 0, 470);
    road.addColorStop(0, "#c2ae90");
    road.addColorStop(0.5, "#c7ae8c");
    road.addColorStop(1, "#8e7158");
    ctx.fillStyle = road;
    ctx.beginPath();
    ctx.moveTo(260, 470);
    ctx.bezierCurveTo(317, 425, 344, 369, 387, 332);
    ctx.lineTo(433, 332);
    ctx.bezierCurveTo(428, 376, 469, 427, 529, 470);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = "rgba(244, 226, 196, 0.3)";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(333, 470);
    ctx.bezierCurveTo(370, 418, 386, 369, 407, 333);
    ctx.stroke();

    drawBarn(ctx);
    drawFence(ctx);

    ctx.strokeStyle = "rgba(20, 43, 22, 0.62)";
    ctx.lineWidth = 1.4;
    for (const [x, y, height] of [
      [18, 468, 23],
      [31, 470, 17],
      [72, 465, 25],
      [88, 470, 18],
      [472, 469, 27],
      [488, 464, 16],
      [647, 468, 24],
      [678, 470, 18],
    ]) {
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.quadraticCurveTo(x - 4, y - height * 0.55, x + 2, y - height);
      ctx.stroke();
    }

    basePixels = ctx.getImageData(0, 0, W, H);
    workingPixels = new ImageData(new Uint8ClampedArray(basePixels.data), W, H);
  }

  function updateSwatches(factor) {
    for (const swatch of swatches) {
      swatch.element.style.backgroundColor =
        `rgb(${swatch.red}, ${Math.round(swatch.green * factor)}, ${Math.round(swatch.blue * factor)})`;
    }
  }

  function renderPreview(percent) {
    const factor = (100 - percent) / 100;
    if (factor >= 0.999) {
      previewCtx.putImageData(basePixels, 0, 0);
    } else {
      const source = basePixels.data;
      const target = workingPixels.data;
      for (let index = 0; index < source.length; index += 4) {
        target[index] = source[index];
        target[index + 1] = source[index + 1] * factor;
        target[index + 2] = source[index + 2] * factor;
        target[index + 3] = source[index + 3];
      }
      previewCtx.putImageData(workingPixels, 0, 0);
    }
    updateSwatches(factor);
    previewCanvas.setAttribute(
      "aria-label",
      percent <= 0
        ? "Simulated display scene at full color."
        : `Simulated display scene with ${percent} percent red isolation; green and blue at ${100 - percent} percent.`
    );
  }

  function schedulePreview(percent) {
    previewTarget = percent;
    if (previewFrame !== null) {
      return;
    }
    previewFrame = requestAnimationFrame(() => {
      previewFrame = null;
      renderPreview(previewTarget);
    });
  }

  // ---------------------------------------------------------------------------
  // UI state
  // ---------------------------------------------------------------------------

  function setStatus(kind, label) {
    elements.status.dataset.state = kind;
    elements.statusLabel.textContent = label;
  }

  function updateStatus() {
    if (!state.ready) {
      setStatus("loading", "Connecting");
      return;
    }
    if (!state.supported) {
      setStatus("error", state.transportError ? "Offline" : "Unavailable");
      return;
    }
    if (state.transportError) {
      setStatus("error", "Link error");
      return;
    }
    if (state.percent > 0) {
      setStatus("active", "Active");
    } else {
      setStatus("ready", "Live");
    }
  }

  function roleFor(value, isRed) {
    if (isRed) {
      return "Preserved";
    }
    if (value <= 0) {
      return "Zeroed";
    }
    if (value >= 100) {
      return "Untouched";
    }
    return "Attenuated";
  }

  function renderAll(rawPercent) {
    const percent = clampPercent(rawPercent);
    const remaining = 100 - percent;
    state.percent = percent;

    elements.slider.value = String(percent);
    elements.slider.style.setProperty("--value", `${percent}%`);
    elements.slider.setAttribute(
      "aria-valuetext",
      percent === 0
        ? "0 percent, full color"
        : `${percent} percent isolated, green and blue at ${remaining} percent`
    );

    elements.percentValue.textContent = String(percent);
    elements.stateWord.textContent =
      percent === 0 ? "Full color" : percent >= 100 ? "Red only" : `${percent}% isolated`;
    elements.panel.dataset.isolated = percent > 0 ? "true" : "false";
    elements.previewBadge.textContent =
      percent === 0 ? "Full spectrum" : percent >= 100 ? "Red only" : `${percent}% cut`;

    elements.redBar.style.width = "100%";
    elements.greenBar.style.width = `${remaining}%`;
    elements.blueBar.style.width = `${remaining}%`;
    elements.redValue.textContent = "100";
    elements.greenValue.textContent = String(remaining);
    elements.blueValue.textContent = String(remaining);
    elements.greenRole.textContent = roleFor(remaining, false);
    elements.blueRole.textContent = roleFor(remaining, false);

    elements.greenChannel.classList.toggle("is-zero", remaining === 0);
    elements.blueChannel.classList.toggle("is-zero", remaining === 0);
    elements.greenChannel.setAttribute(
      "aria-label",
      `Green channel ${remaining} percent, ${elements.greenRole.textContent}`
    );
    elements.blueChannel.setAttribute(
      "aria-label",
      `Blue channel ${remaining} percent, ${elements.blueRole.textContent}`
    );

    for (const preset of elements.presets) {
      preset.setAttribute("aria-pressed", String(Number(preset.dataset.percent) === percent));
    }

    elements.restoreButton.classList.toggle("is-armed", percent > 0);
    updateStatus();
    schedulePreview(percent);
  }

  function showError(message) {
    elements.errorText.textContent = message;
    elements.errorMessage.hidden = false;
  }

  function clearError() {
    elements.errorMessage.hidden = true;
    elements.errorText.textContent = "";
  }

  function showToast(message) {
    elements.toast.textContent = message;
    elements.toast.classList.add("is-visible");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      elements.toast.classList.remove("is-visible");
    }, 2800);
  }

  function setControlsEnabled(enabled) {
    elements.slider.disabled = !enabled;
    elements.restoreButton.disabled = !enabled;
    for (const preset of elements.presets) {
      preset.disabled = !enabled;
    }
  }

  function updateDisplay(outputs) {
    const list = Array.isArray(outputs) ? outputs : [];
    const primary = list.find((output) => output.primary) || list[0];
    if (!primary) {
      elements.displayName.textContent = "X11 display";
      elements.displayName.title = "";
      return;
    }
    let label = primary.name;
    if (primary.resolution) {
      label += ` · ${primary.resolution.replace("x", " × ")}`;
    }
    if (list.length > 1) {
      label += ` +${list.length - 1}`;
    }
    elements.displayName.textContent = label;
    elements.displayName.title = list
      .map((output) => `${output.name}${output.resolution ? ` (${output.resolution})` : ""}`)
      .join(" / ");
  }

  // ---------------------------------------------------------------------------
  // Display updates
  // ---------------------------------------------------------------------------

  async function sendApply(percent) {
    try {
      const response = await fetch("/api/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ percent }),
        cache: "no-store",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || `Display update failed (${response.status}).`);
      }
      state.transportError = false;
      clearError();
      updateStatus();
    } catch (error) {
      state.transportError = true;
      updateStatus();
      showError(error instanceof Error ? error.message : "The display update failed.");
    }
  }

  function queueApply(percent) {
    if (!state.supported) {
      return;
    }
    pending.value = percent;
    pumpApply();
  }

  function pumpApply() {
    if (!state.supported || pending.inFlight || pending.value === null) {
      return;
    }
    if (pending.timer !== null) {
      window.clearTimeout(pending.timer);
      pending.timer = null;
    }
    const wait = Math.max(0, 32 - (performance.now() - pending.lastAt));
    if (wait > 0) {
      pending.timer = window.setTimeout(() => {
        pending.timer = null;
        pumpApply();
      }, wait);
      return;
    }

    const percent = pending.value;
    pending.value = null;
    pending.inFlight = true;
    pending.lastAt = performance.now();
    sendApply(percent).finally(() => {
      pending.inFlight = false;
      if (pending.value !== null) {
        pumpApply();
      }
    });
  }

  function restoreFullColor(announce) {
    if (!state.supported) {
      return;
    }
    const hadEffect = state.percent > 0;
    renderAll(0);
    queueApply(0);
    if (announce && hadEffect) {
      showToast("Full color restored.");
    }
  }

  // ---------------------------------------------------------------------------
  // Boot and events
  // ---------------------------------------------------------------------------

  function bindEvents() {
    elements.slider.addEventListener("input", () => {
      if (!state.supported) {
        return;
      }
      const percent = Number(elements.slider.value);
      renderAll(percent);
      queueApply(percent);
    });

    for (const preset of elements.presets) {
      preset.addEventListener("click", () => {
        if (!state.supported) {
          return;
        }
        const percent = Number(preset.dataset.percent);
        renderAll(percent);
        queueApply(percent);
      });
    }

    elements.restoreButton.addEventListener("click", () => {
      restoreFullColor(true);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !state.supported) {
        return;
      }
      event.preventDefault();
      restoreFullColor(true);
    });

    window.addEventListener("pagehide", () => {
      if (!state.supported) {
        return;
      }
      const payload = new Blob(["{}"], { type: "application/json" });
      const sent = navigator.sendBeacon("/api/reset", payload);
      if (!sent) {
        fetch("/api/reset", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
          keepalive: true,
        }).catch(() => {});
      }
    });
  }

  async function loadState() {
    try {
      const response = await fetch("/api/state", { cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || `Status request failed (${response.status}).`);
      }
      state.ready = true;
      state.supported = Boolean(data.supported);
      state.transportError = false;
      updateDisplay(data.outputs);
      elements.crtcLabel.textContent = `XRandR · ${Number(data.crtcCount) || 0} tables`;

      if (!state.supported) {
        setControlsEnabled(false);
        showError(data.error || "No compatible X11 gamma table was found.");
        renderAll(0);
        updateStatus();
        return;
      }

      clearError();
      setControlsEnabled(true);
      renderAll(Number(data.percent) || 0);
      updateStatus();
    } catch (error) {
      state.ready = true;
      state.supported = false;
      state.transportError = true;
      setControlsEnabled(false);
      setStatus("error", "Offline");
      showError(
        "Could not reach the local Redline service. Restart ./run.sh and reload this page."
      );
      renderAll(0);
      updateStatus();
    }
  }

  drawScene();
  renderPreview(0);
  bindEvents();
  renderAll(0);
  loadState();
})();
