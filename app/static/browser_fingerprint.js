/* Browser-side fingerprint collector — approximate, browser-dependent signals. */
(() => {
  "use strict";

  const out = document.getElementById("output");

  function hashString(str) {
    return crypto.subtle.digest("SHA-256", new TextEncoder().encode(str)).then((buf) =>
      [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("")
    );
  }

  function canvasFingerprint() {
    const canvas = document.createElement("canvas");
    canvas.width = 240;
    canvas.height = 60;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.textBaseline = "alphabetic";
    ctx.font = "14px 'Arial'";
    ctx.fillStyle = "#f60";
    ctx.fillRect(10, 10, 120, 30);
    ctx.fillStyle = "#069";
    ctx.fillText("http-lab-fingerprint-©", 12, 42);
    ctx.fillStyle = "rgba(102, 204, 0, 0.7)";
    ctx.fillText("abcdefghijklmnop", 60, 25);
    try {
      return ctx.canvas.toDataURL();
    } catch (_) {
      return null;
    }
  }

  function webglInfo() {
    const canvas = document.createElement("canvas");
    const gl =
      canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
    if (!gl) return null;
    const get = (param) => {
      try {
        return gl.getParameter(param);
      } catch (_) {
        return null;
      }
    };
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    return {
      vendor: ext ? get(ext.UNMASKED_VENDOR_WEBGL) : get(gl.VENDOR),
      renderer: ext ? get(ext.UNMASKED_RENDERER_WEBGL) : get(gl.RENDERER),
      version: get(gl.VERSION),
      shading_language_version: get(gl.SHADING_LANGUAGE_VERSION),
    };
  }

  function uaBreakdown(ua) {
    const result = {};
    const patterns = [
      ["firefox", /Firefox\/([\d.]+)/],
      ["chrome", /Chrome\/([\d.]+)/],
      ["safari", /Safari\/([\d.]+)/],
      ["edge", /Edg\/([\d.]+)/],
      ["opera", /OPR\/([\d.]+)/],
      ["webkit", /AppleWebKit\/([\d.]+)/],
    ];
    for (const [name, re] of patterns) {
      const m = ua.match(re);
      if (m) result[name] = m[1];
    }
    return result;
  }

  async function collect() {
    const data = {};
    const fields = [
      ["userAgent", navigator.userAgent],
      ["platform", navigator.platform],
      ["language", navigator.language],
      ["languages", navigator.languages],
      ["hardwareConcurrency", navigator.hardwareConcurrency],
      ["deviceMemory", navigator.deviceMemory], // GB; Chrome only, may be absent
      ["maxTouchPoints", navigator.maxTouchPoints],
      ["webdriver", navigator.webdriver],
      ["cookieEnabled", navigator.cookieEnabled],
      ["doNotTrack", navigator.doNotTrack],
      ["online", navigator.onLine],
    ];
    for (const [key, value] of fields) {
      if (value !== undefined) data[key] = value;
    }

    data.timezone = (() => {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const offset = -new Date().getTimezoneOffset() / 60;
      return { zone: tz || "unknown", utc_offset_hours: offset };
    })();

    data.screen = {
      width: screen.width,
      height: screen.height,
      color_depth: screen.colorDepth,
      pixel_ratio: window.devicePixelRatio || 1,
    };
    data.viewport = {
      width: window.innerWidth,
      height: window.innerHeight,
      outer_width: window.outerWidth,
      outer_height: window.outerHeight,
    };
    data.touch = {
      supported: "ontouchstart" in window || navigator.maxTouchPoints > 0,
    };
    data.prefer_reduced_motion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    data.webgl = webglInfo();
    const canvas = canvasFingerprint();
    if (canvas) {
      data.canvas = { width: 240, height: 60 };
      try {
        data.canvas.sha256 = await hashString(canvas);
      } catch (_) {
        data.canvas.sha256 = null;
      }
      data.canvas.note = "Hash of a lightweight canvas drawing; approximate and not unique per machine.";
    }

    const ua = navigator.userAgent;
    data.browser = uaBreakdown(ua);
    data.automation = {
      webdriver_flag: navigator.webdriver,
      note: "navigator.webdriver is true in many automated browsers, but privacy browsers and automation can mask it.",
    };
    data.disclaimer = [
      "Values approximate and browser-dependent.",
      "deviceMemory, WebGL renderer, concurrency and canvas hashes vary across machines, OSes and privacy modes.",
      "No fingerprint value here is a stable identity; this page is for understanding what browsers expose.",
    ];

    out.textContent = JSON.stringify(data, null, 2);
  }

  document.getElementById("collect").addEventListener("click", collect);
  document.getElementById("clear").addEventListener("click", () => {
    out.textContent = "Press \"Collect fingerprint\" to gather browser-side signals.";
  });
})();
