(function () {
  "use strict";

  const state = {
    devices: [],
    selectedDeviceId: "blackcomputer",
    liveSource: null,
    loading: false,
    devicesRequest: null,
  };

  const $ = (id) => document.getElementById(id);

  const el = {
    pairingScreen: $("pairingScreen"),
    appShell: $("appShell"),
    pairingForm: $("pairingForm"),
    pairingToken: $("pairingToken"),
    pairingMessage: $("pairingMessage"),
    pairState: $("pairState"),
    transportState: $("transportState"),
    clock: $("clock"),
    refreshButton: $("refreshButton"),
    deviceList: $("deviceList"),
    focusRole: $("focusRole"),
    focusTitle: $("focusTitle"),
    focusStatus: $("focusStatus"),
    metricBand: $("metricBand"),
    sensorGrid: $("sensorGrid"),
    historyList: $("historyList"),
    diagnoseButton: $("diagnoseButton"),
    refreshDeviceButton: $("refreshDeviceButton"),
    startSensorsButton: $("startSensorsButton"),
    stopSensorsButton: $("stopSensorsButton"),
    chatForm: $("chatForm"),
    chatInput: $("chatInput"),
    chatStatus: $("chatStatus"),
    eventList: $("eventList"),
    notificationList: $("notificationList"),
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtTime(value) {
    if (!value) return "never";
    const raw = Number(value);
    const ms = raw > 100000000000 ? raw : raw * 1000;
    try {
      return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "unknown";
    }
  }

  function fmtNumber(value, suffix) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "n/a";
    return `${Math.round(n)}${suffix || ""}`;
  }

  async function fetchJSON(url, options) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...(options || {}),
    });
    if (response.status === 401) {
      showPairing("Pairing required.");
      throw new Error("Pairing required.");
    }
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) {
      throw new Error(payload.error || payload.reason_code || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  function showPairing(message) {
    el.appShell.hidden = true;
    el.pairingScreen.hidden = false;
    if (message) el.pairingMessage.textContent = message;
  }

  function showApp() {
    el.pairingScreen.hidden = true;
    el.appShell.hidden = false;
    el.pairState.textContent = "paired";
    el.pairState.classList.add("online");
  }

  function statusClass(status) {
    const value = String(status || "unknown").toLowerCase();
    if (value === "online" || value === "healthy") return "online";
    if (value === "stale" || value === "warning" || value === "partial") return "stale";
    if (value === "offline" || value === "error" || value === "degraded") return "offline";
    return "";
  }

  function selectedDevice() {
    return state.devices.find((device) => device.id === state.selectedDeviceId) || state.devices[0] || null;
  }

  function renderDevices() {
    const devices = state.devices;
    if (!devices.length) {
      el.deviceList.innerHTML = `<div class="empty-state">No devices registered.</div>`;
      return;
    }
    el.deviceList.innerHTML = devices
      .map((device) => {
        const active = device.id === state.selectedDeviceId ? "active" : "";
        const badgeClass = statusClass(device.status);
        return `
          <button class="device-card ${active}" type="button" data-device-id="${escapeHtml(device.id)}">
            <div>
              <div class="device-label">${escapeHtml(device.label || device.id)}</div>
              <div class="device-meta">${escapeHtml(device.role || "device")} | seen ${escapeHtml(device.freshness_seconds == null ? "never" : `${device.freshness_seconds}s ago`)}</div>
            </div>
            <span class="state-badge ${badgeClass}">${escapeHtml(device.status || "unknown")}</span>
          </button>
        `;
      })
      .join("");
    el.deviceList.querySelectorAll("[data-device-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        state.selectedDeviceId = button.dataset.deviceId || "blackcomputer";
        await loadDeviceDetail();
        renderDevices();
      });
    });
  }

  function metricEntries(device) {
    const health = device.health || {};
    const telemetry = device.telemetry || {};
    const queue = telemetry.queue || {};
    return [
      ["CPU", fmtNumber(health.cpu_percent, "%")],
      ["RAM", fmtNumber(health.ram_percent, "%")],
      ["Disk", fmtNumber(health.disk_percent, "%")],
      ["Temp", health.temp_c == null ? "n/a" : `${Number(health.temp_c).toFixed(1)} C`],
      ["Health", health.overall || health.system_health || device.status || "unknown"],
      ["Backend", health.backend || "n/a"],
      ["Queue", queue.pending_requests == null ? "n/a" : String(queue.pending_requests)],
      ["Seen", device.freshness_seconds == null ? "never" : `${device.freshness_seconds}s`],
    ];
  }

  function renderFocus(device) {
    if (!device) {
      el.focusTitle.textContent = "No device";
      el.focusStatus.textContent = "unknown";
      el.metricBand.innerHTML = "";
      el.sensorGrid.innerHTML = "";
      el.historyList.innerHTML = "";
      return;
    }
    el.focusRole.textContent = device.role || "Device";
    el.focusTitle.textContent = device.label || device.id;
    el.focusStatus.textContent = device.status || "unknown";
    el.focusStatus.className = `state-badge ${statusClass(device.status)}`;
    el.metricBand.innerHTML = metricEntries(device)
      .map(([key, value]) => `
        <div class="metric">
          <div class="metric-key">${escapeHtml(key)}</div>
          <div class="metric-value">${escapeHtml(value)}</div>
        </div>
      `)
      .join("");
    renderSensors(device);
    renderHistory(device);
  }

  function renderSensors(device) {
    const sensors = device.sensors || {};
    const hiddenSensorNames = new Set(["screen", "screen_stream", "desktop", "remote_desktop", "vnc", "rdp", "webrtc", "moonlight"]);
    const knownSensorNames = new Set([
      "audio_probe",
      "camera",
      "capture_policy",
      "microphone",
      "observations",
      "perception_gate",
      "presence",
      "sensor_mode",
      "speaker",
    ]);
    const entries = Object.entries(sensors).filter(([name, sensor]) => {
      const normalized = String(name).toLowerCase();
      if (hiddenSensorNames.has(normalized)) return false;
      if (knownSensorNames.has(normalized)) return true;
      return Boolean(sensor && typeof sensor === "object" && ("available" in sensor || "active" in sensor || "state" in sensor || "status" in sensor));
    });
    if (!entries.length) {
      el.sensorGrid.innerHTML = `<div class="empty-state">No sensor state reported.</div>`;
      return;
    }
    el.sensorGrid.innerHTML = entries
      .map(([name, sensor]) => {
        const available = sensor && sensor.available ? "available" : "unavailable";
        const active = sensor && sensor.active ? "active" : "idle";
        const badgeClass = sensor && sensor.active ? "online" : sensor && sensor.available ? "stale" : "offline";
        return `
          <div class="sensor">
            <strong>${escapeHtml(name)}</strong>
            <div class="device-meta">${escapeHtml(available)}</div>
            <div class="state-badge ${badgeClass}">${escapeHtml(active)}</div>
          </div>
        `;
      })
      .join("");
  }

  function renderHistory(device) {
    const history = Array.isArray(device.history) ? device.history.slice(-18).reverse() : [];
    if (!history.length) {
      el.historyList.innerHTML = `<div class="empty-state">No heartbeat history yet.</div>`;
      return;
    }
    el.historyList.innerHTML = history
      .map((item) => `
        <div class="history-row">
          <strong>${escapeHtml(item.status || item.sensor_mode || "update")}</strong>
          <div class="event-meta">${escapeHtml(fmtTime(item.timestamp))} | ${escapeHtml(item.source || "device")}</div>
        </div>
      `)
      .join("");
  }

  async function loadDevices() {
    if (state.devicesRequest) {
      return state.devicesRequest;
    }
    state.devicesRequest = (async () => {
      state.loading = true;
      try {
        const payload = await fetchJSON("/api/devices");
        state.devices = payload.devices || [];
        if (!state.devices.some((device) => device.id === state.selectedDeviceId) && state.devices.length) {
          state.selectedDeviceId = state.devices[0].id;
        }
        renderDevices();
        await loadDeviceDetail();
      } finally {
        state.loading = false;
        state.devicesRequest = null;
      }
    })();
    return state.devicesRequest;
  }

  async function loadDeviceDetail() {
    const deviceId = state.selectedDeviceId || "blackcomputer";
    const device = await fetchJSON(`/api/devices/${encodeURIComponent(deviceId)}`);
    renderFocus(device);
  }

  async function loadEvents() {
    try {
      const payload = await fetchJSON("/api/events?limit=20");
      const events = payload.events || [];
      el.eventList.innerHTML = events.length
        ? events
            .slice(0, 20)
            .map((event) => `
              <div class="event-row">
                <strong>${escapeHtml(event.type || "event")}</strong>
                <div class="event-meta">${escapeHtml(event.source || "source")} | ${escapeHtml(fmtTime(event.timestamp))}</div>
                <div>${escapeHtml(summarize(event.payload))}</div>
              </div>
            `)
            .join("")
        : `<div class="empty-state">No events yet.</div>`;
    } catch {
      el.eventList.innerHTML = `<div class="empty-state">Events unavailable.</div>`;
    }
  }

  async function loadNotifications() {
    try {
      const payload = await fetchJSON("/api/notifications?limit=12");
      const notifications = payload.notifications || payload.items || [];
      el.notificationList.innerHTML = notifications.length
        ? notifications
            .map((item) => `
              <div class="notification-row">
                <strong>${escapeHtml(item.title || item.category || "Notification")}</strong>
                <div class="event-meta">${escapeHtml(item.level || "info")} | ${escapeHtml(fmtTime(item.created_at || item.timestamp))}</div>
                <div>${escapeHtml(item.message || "")}</div>
              </div>
            `)
            .join("")
        : `<div class="empty-state">No notifications.</div>`;
    } catch {
      el.notificationList.innerHTML = `<div class="empty-state">Notifications unavailable.</div>`;
    }
  }

  function summarize(payload) {
    if (payload == null) return "";
    const raw = typeof payload === "string" ? payload : JSON.stringify(payload);
    return raw.length > 140 ? `${raw.slice(0, 140)}...` : raw;
  }

  async function diagnoseSelected() {
    const deviceId = state.selectedDeviceId || "blackcomputer";
    const result = await fetchJSON(`/api/devices/${encodeURIComponent(deviceId)}/diagnose`, {
      method: "POST",
      body: "{}",
    });
    el.chatStatus.textContent = (result.recommendations || []).join(" ");
  }

  async function refreshSelected() {
    const deviceId = state.selectedDeviceId || "blackcomputer";
    await fetchJSON(`/api/devices/${encodeURIComponent(deviceId)}/refresh`, { method: "POST", body: "{}" });
    await loadDevices();
  }

  async function setSensors(active) {
    const deviceId = state.selectedDeviceId || "sentinel";
    if (active && !window.confirm("Start gated sensors for this device?")) {
      return;
    }
    const endpoint = active ? "start" : "stop";
    const mode = active ? "live_vision" : "off";
    await fetchJSON(`/api/devices/${encodeURIComponent(deviceId)}/sensors/${endpoint}`, {
      method: "POST",
      body: JSON.stringify({ mode, consent: { approved: true, level: 1, source: "legion" } }),
    });
    await loadDeviceDetail();
    await loadEvents();
  }

  async function sendChat(event) {
    event.preventDefault();
    const message = el.chatInput.value.trim();
    if (!message) return;
    el.chatInput.value = "";
    el.chatStatus.textContent = "Accepted by D.A.I.S.E.";
    try {
      const payload = await fetchJSON("/api/chat", {
        method: "POST",
        body: JSON.stringify({ message, background: true, analysis_mode: "fast" }),
      });
      el.chatStatus.textContent = payload.message || "Request accepted.";
    } catch (error) {
      el.chatStatus.textContent = error.message;
    }
  }

  async function pair(event) {
    event.preventDefault();
    const token = el.pairingToken.value.trim();
    if (!token) return;
    el.pairingMessage.textContent = "Pairing...";
    try {
      await fetchJSON("/api/legion/pair", {
        method: "POST",
        body: JSON.stringify({ token, label: "Legion" }),
      });
      el.pairingToken.value = "";
      showApp();
      await bootData();
    } catch (error) {
      el.pairingMessage.textContent = error.message;
    }
  }

  function startClock() {
    const tick = () => {
      el.clock.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    };
    tick();
    window.setInterval(tick, 1000);
  }

  function connectLiveStream() {
    if (state.liveSource) {
      state.liveSource.close();
    }
    try {
      const source = new EventSource("/api/live/stream");
      state.liveSource = source;
      source.addEventListener("transport", () => {
        el.transportState.textContent = "live";
        el.transportState.classList.add("online");
      });
      source.addEventListener("heartbeat", () => {
        el.transportState.textContent = "live";
      });
      source.addEventListener("device_update", async () => {
        await loadDevices();
      });
      source.addEventListener("chat_complete", async (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          el.chatStatus.textContent = payload.response || "D.A.I.S.E. complete.";
          await loadEvents();
        } catch {
          el.chatStatus.textContent = "D.A.I.S.E. complete.";
        }
      });
      source.addEventListener("chat_error", (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          el.chatStatus.textContent = payload.error || "D.A.I.S.E. error.";
        } catch {
          el.chatStatus.textContent = "D.A.I.S.E. error.";
        }
      });
      source.onerror = () => {
        el.transportState.textContent = "polling";
        el.transportState.classList.remove("online");
      };
    } catch {
      el.transportState.textContent = "polling";
    }
  }

  async function bootData() {
    await Promise.all([loadDevices(), loadEvents(), loadNotifications()]);
    connectLiveStream();
  }

  async function boot() {
    startClock();
    el.pairingForm.addEventListener("submit", pair);
    el.chatForm.addEventListener("submit", sendChat);
    el.refreshButton.addEventListener("click", () => bootData().catch(() => {}));
    el.diagnoseButton.addEventListener("click", () => diagnoseSelected().catch((error) => (el.chatStatus.textContent = error.message)));
    el.refreshDeviceButton.addEventListener("click", () => refreshSelected().catch((error) => (el.chatStatus.textContent = error.message)));
    el.startSensorsButton.addEventListener("click", () => setSensors(true).catch((error) => (el.chatStatus.textContent = error.message)));
    el.stopSensorsButton.addEventListener("click", () => setSensors(false).catch((error) => (el.chatStatus.textContent = error.message)));

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/legion/service-worker.js").catch(() => {});
    }

    try {
      showApp();
      await bootData();
      window.setInterval(() => {
        loadDevices().catch(() => {});
        loadEvents().catch(() => {});
        loadNotifications().catch(() => {});
      }, 15000);
    } catch {
      showPairing("Enter the Blackcomputer pairing token.");
    }
  }

  boot();
})();
