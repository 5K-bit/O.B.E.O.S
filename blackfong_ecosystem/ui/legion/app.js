(function () {
  "use strict";

  const LEGION_UI_STATE_KEY = "blackfong.legion.ui.v2";
  const DEFAULT_ANALYSIS_MODE = "standard";

  const state = {
    devices: [],
    selectedDeviceId: "blackcomputer",
    liveSource: null,
    loading: false,
    devicesRequest: null,
    sessionId: null,
    requestId: null,
    analysisMode: DEFAULT_ANALYSIS_MODE,
    messages: [],
    localMessages: [],
    streamingReply: null,
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
    sessionMeta: $("sessionMeta"),
    analysisSelect: $("analysisSelect"),
    messagesPanel: $("messagesPanel"),
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

  function normalizeAnalysisMode(value) {
    const normalized = String(value || "").trim().toLowerCase();
    return normalized === "fast" || normalized === "deep" || normalized === "standard"
      ? normalized
      : DEFAULT_ANALYSIS_MODE;
  }

  function loadUiState() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(LEGION_UI_STATE_KEY) || "{}");
      state.analysisMode = normalizeAnalysisMode(parsed.analysisMode);
    } catch {
      state.analysisMode = DEFAULT_ANALYSIS_MODE;
    }
    if (el.analysisSelect) {
      el.analysisSelect.value = state.analysisMode;
    }
  }

  function persistUiState() {
    try {
      window.localStorage.setItem(
        LEGION_UI_STATE_KEY,
        JSON.stringify({
          analysisMode: state.analysisMode,
        }),
      );
    } catch {
      // Ignore storage failures in restrictive browser modes.
    }
  }

  function fmtTime(value) {
    if (!value) return "now";
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

  function shortId(value) {
    const text = String(value || "").trim();
    if (!text) return "unknown";
    return text.length > 12 ? `${text.slice(0, 8)}...` : text;
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

  function updateSessionMeta() {
    if (!el.sessionMeta) {
      return;
    }
    el.sessionMeta.textContent = state.sessionId
      ? `Linked session ${shortId(state.sessionId)}`
      : "Linking shared session...";
  }

  function updateChatStatus(text, stateKind) {
    if (!el.chatStatus) {
      return;
    }
    el.chatStatus.textContent = String(text || "idle");
    el.chatStatus.classList.remove("online", "stale", "offline");
    const normalized = String(stateKind || "").toLowerCase();
    if (normalized === "complete" || normalized === "streaming" || normalized === "working") {
      el.chatStatus.classList.add("online");
    } else if (normalized === "error" || normalized === "failed") {
      el.chatStatus.classList.add("offline");
    } else if (normalized === "accepted" || normalized === "pending") {
      el.chatStatus.classList.add("stale");
    }
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
      .map(
        ([key, value]) => `
        <div class="metric">
          <div class="metric-key">${escapeHtml(key)}</div>
          <div class="metric-value">${escapeHtml(value)}</div>
        </div>
      `,
      )
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
      .map(
        (item) => `
        <div class="history-row">
          <strong>${escapeHtml(item.status || item.sensor_mode || "update")}</strong>
          <div class="event-meta">${escapeHtml(fmtTime(item.timestamp))} | ${escapeHtml(item.source || "device")}</div>
        </div>
      `,
      )
      .join("");
  }

  function summarize(payload) {
    if (payload == null) return "";
    const raw = typeof payload === "string" ? payload : JSON.stringify(payload);
    return raw.length > 140 ? `${raw.slice(0, 140)}...` : raw;
  }

  function formatMessageBody(text) {
    return escapeHtml(String(text || "")).replace(/\n/g, "<br>");
  }

  function displayMessageLabel(message) {
    const role = String(message.role || "assistant").toLowerCase();
    if (role === "user") return "You";
    if (role === "system") return "System";
    const agent = String(message.agent || message.author || "daise").trim();
    if (!agent || agent.toLowerCase() === "daise") {
      return "D.A.I.S.E";
    }
    return agent;
  }

  function isMessagesPanelNearBottom() {
    if (!el.messagesPanel) {
      return true;
    }
    const distance = el.messagesPanel.scrollHeight - el.messagesPanel.scrollTop - el.messagesPanel.clientHeight;
    return distance < 80;
  }

  function renderMessages(payload) {
    if (!el.messagesPanel) {
      return;
    }
    const incoming = payload && Array.isArray(payload.messages) ? payload.messages : state.messages;
    const shouldStick = isMessagesPanelNearBottom();
    const previousScrollTop = el.messagesPanel.scrollTop || 0;
    const previousScrollHeight = el.messagesPanel.scrollHeight || 0;
    state.messages = incoming;
    const combined = incoming.slice();
    state.localMessages.forEach((message) => combined.push(message));
    if (state.streamingReply && state.streamingReply.text) {
      combined.push({
        role: "assistant",
        text: state.streamingReply.text,
        timestamp: state.streamingReply.timestamp || Date.now() / 1000,
        streaming: !state.streamingReply.complete,
      });
    }
    el.messagesPanel.innerHTML = combined.length
      ? combined
          .map((message) => {
            const role = message.role === "assistant" ? "assistant" : message.role === "system" ? "system" : "user";
            const label = displayMessageLabel(message);
            return `
              <article class="message-card ${role}">
                <div class="message-head">
                  <strong>${escapeHtml(label)}</strong>
                  <span class="event-meta">${escapeHtml(fmtTime(message.timestamp || message.created_at || 0))}</span>
                </div>
                <div class="message-body">${formatMessageBody(message.text || message.content || "")}</div>
              </article>
            `;
          })
          .join("")
      : `<div class="empty-state">The linked O.B.E.O.S conversation will appear here.</div>`;
    if (shouldStick) {
      el.messagesPanel.scrollTop = el.messagesPanel.scrollHeight;
    } else {
      const heightDelta = el.messagesPanel.scrollHeight - previousScrollHeight;
      el.messagesPanel.scrollTop = previousScrollTop + Math.max(0, heightDelta);
    }
  }

  function beginLocalUserMessage(text) {
    state.localMessages.push({
      role: "user",
      text,
      timestamp: Date.now() / 1000,
    });
  }

  function activeRequestMatches(payload) {
    if (!payload || typeof payload !== "object") {
      return false;
    }
    if (state.requestId && payload.request_id && payload.request_id === state.requestId) {
      return true;
    }
    return Boolean(state.sessionId && payload.session_id && payload.session_id === state.sessionId);
  }

  async function loadLegionSession() {
    const payload = await fetchJSON("/api/legion/session");
    if (payload.session_id) {
      state.sessionId = payload.session_id;
      updateSessionMeta();
    }
    return payload;
  }

  async function refreshMessages() {
    if (!state.sessionId) {
      renderMessages({ messages: [] });
      return;
    }
    try {
      const payload = await fetchJSON(`/api/session/${encodeURIComponent(state.sessionId)}/messages`);
      state.localMessages = [];
      state.streamingReply = null;
      renderMessages(payload);
    } catch (error) {
      if (String(error.message || "").toLowerCase().includes("session not found")) {
        state.messages = [];
        state.localMessages = [];
        state.streamingReply = null;
        renderMessages({ messages: [] });
        return;
      }
      throw error;
    }
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
            .map(
              (event) => `
              <div class="event-row">
                <strong>${escapeHtml(event.type || "event")}</strong>
                <div class="event-meta">${escapeHtml(event.source || "source")} | ${escapeHtml(fmtTime(event.timestamp))}</div>
                <div>${escapeHtml(summarize(event.payload))}</div>
              </div>
            `,
            )
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
            .map(
              (item) => `
              <div class="notification-row">
                <strong>${escapeHtml(item.title || item.category || "Notification")}</strong>
                <div class="event-meta">${escapeHtml(item.level || "info")} | ${escapeHtml(fmtTime(item.created_at || item.timestamp))}</div>
                <div>${escapeHtml(item.message || "")}</div>
              </div>
            `,
            )
            .join("")
        : `<div class="empty-state">No notifications.</div>`;
    } catch {
      el.notificationList.innerHTML = `<div class="empty-state">Notifications unavailable.</div>`;
    }
  }

  async function diagnoseSelected() {
    const deviceId = state.selectedDeviceId || "blackcomputer";
    const result = await fetchJSON(`/api/devices/${encodeURIComponent(deviceId)}/diagnose`, {
      method: "POST",
      body: "{}",
    });
    updateChatStatus((result.recommendations || []).join(" ") || "Diagnostics complete.", "complete");
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
    if (!state.sessionId) {
      await loadLegionSession();
    }
    state.analysisMode = normalizeAnalysisMode(el.analysisSelect ? el.analysisSelect.value : state.analysisMode);
    persistUiState();
    el.chatInput.value = "";
    beginLocalUserMessage(message);
    state.requestId = null;
    state.streamingReply = { text: "", timestamp: Date.now() / 1000, complete: false };
    renderMessages({ messages: state.messages });
    updateChatStatus("Accepted by D.A.I.S.E.", "accepted");
    try {
      const payload = await fetchJSON("/api/chat", {
        method: "POST",
        body: JSON.stringify({
          message,
          background: true,
          analysis_mode: state.analysisMode,
          session_id: state.sessionId,
          transport: "legion",
          input_origin: "legion",
        }),
      });
      state.sessionId = payload.session_id || state.sessionId;
      state.requestId = payload.request_id || state.requestId;
      updateSessionMeta();
      updateChatStatus(payload.message || `Accepted (${state.analysisMode})`, "working");
    } catch (error) {
      state.streamingReply = null;
      updateChatStatus(error.message, "error");
      renderMessages({ messages: state.messages });
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
      source.addEventListener("chat_phase", (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          if (!activeRequestMatches(payload)) {
            return;
          }
          updateChatStatus(payload.message || payload.phase || "Working", payload.phase || "pending");
        } catch {
          // Ignore malformed phase events.
        }
      });
      source.addEventListener("chat_token", (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          if (!state.requestId || payload.request_id !== state.requestId) {
            return;
          }
          state.streamingReply = {
            text: String(payload.text || payload.token || ""),
            timestamp: Date.now() / 1000,
            complete: false,
          };
          renderMessages({ messages: state.messages });
          updateChatStatus("Streaming response...", "streaming");
        } catch {
          // Ignore malformed token events.
        }
      });
      source.addEventListener("chat_complete", async (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          if (!activeRequestMatches(payload)) {
            return;
          }
          state.sessionId = payload.session_id || state.sessionId;
          if (payload.request_id) {
            state.requestId = payload.request_id;
          }
          updateSessionMeta();
          state.localMessages = [];
          state.streamingReply = null;
          if (Array.isArray(payload.messages)) {
            renderMessages({ messages: payload.messages });
          } else {
            await refreshMessages();
          }
          updateChatStatus("D.A.I.S.E complete.", "complete");
          await loadEvents();
        } catch {
          updateChatStatus("D.A.I.S.E complete.", "complete");
        }
      });
      source.addEventListener("chat_error", (event) => {
        try {
          const payload = JSON.parse(event.data || "{}");
          if (!activeRequestMatches(payload)) {
            return;
          }
          state.streamingReply = null;
          updateChatStatus(payload.error || "D.A.I.S.E. error.", "error");
          renderMessages({ messages: state.messages });
        } catch {
          updateChatStatus("D.A.I.S.E. error.", "error");
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
    await Promise.all([loadLegionSession(), loadDevices(), loadEvents(), loadNotifications()]);
    await refreshMessages();
    connectLiveStream();
  }

  async function boot() {
    loadUiState();
    updateSessionMeta();
    updateChatStatus("idle", "idle");
    renderMessages({ messages: [] });
    startClock();
    el.pairingForm.addEventListener("submit", pair);
    el.chatForm.addEventListener("submit", sendChat);
    el.analysisSelect?.addEventListener("change", () => {
      state.analysisMode = normalizeAnalysisMode(el.analysisSelect.value);
      persistUiState();
    });
    el.refreshButton.addEventListener("click", () => bootData().catch(() => {}));
    el.diagnoseButton.addEventListener("click", () => diagnoseSelected().catch((error) => updateChatStatus(error.message, "error")));
    el.refreshDeviceButton.addEventListener("click", () => refreshSelected().catch((error) => updateChatStatus(error.message, "error")));
    el.startSensorsButton.addEventListener("click", () => setSensors(true).catch((error) => updateChatStatus(error.message, "error")));
    el.stopSensorsButton.addEventListener("click", () => setSensors(false).catch((error) => updateChatStatus(error.message, "error")));

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
        refreshMessages().catch(() => {});
      }, 15000);
    } catch {
      showPairing("Enter the Blackcomputer pairing token.");
    }
  }

  boot();
})();
