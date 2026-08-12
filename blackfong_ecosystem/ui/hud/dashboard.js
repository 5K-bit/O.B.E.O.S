(function () {
  "use strict";

  // #region agent log
  const debugTelemetryEnabled = window.BLACKFONG_HUD_DEBUG_TELEMETRY === true;
  const debugTelemetryEndpoint = window.BLACKFONG_HUD_DEBUG_TELEMETRY_URL || "";

  function debugHudLog(hypothesisId, location, message, data) {
    if (!debugTelemetryEnabled || !debugTelemetryEndpoint) {
      return;
    }
    fetch(debugTelemetryEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "9e7edf" },
      body: JSON.stringify({
        sessionId: "9e7edf",
        hypothesisId,
        location,
        message,
        data: data || {},
        timestamp: Date.now(),
        runId: "ui-hud",
      }),
    }).catch(() => {});
  }
  // #endregion

  const state = {
    mode: "operator",
    workspaceTab: "chat",
    drawerTab: "detail",
    layer: "live_brain",
    viewportBucket: "wide",
    sessionId: null,
    requestId: null,
    sessions: [],
    commands: [],
    commandQuery: "",
    commandHistory: [],
    localMessages: [],
    expandedMessages: new Set(),
    streamingReply: null,
    currentTrace: null,
    cognition: null,
    selectedNode: null,
    generalPollHandle: null,
    tracePollHandle: null,
    dragOrigin: null,
    graphPositions: {},
    fxTick: 0,
    liveSource: null,
    liveConnected: false,
    liveRetryHandle: null,
    operatorSummary: null,
    fileScopeRoots: null,
    fileScopeDetail: null,
    traceSummaryHtml: "",
    tinyLogTail: null,
    integrations: null,
    redamonStatus: null,
    verification: null,
    gpsStatus: null,
    layout: {
      leftWidth: 300,
      rightWidth: 360,
      collapsedLeft: false,
      collapsedRight: false,
      floatingLeft: false,
      floatingRight: false,
      focusedView: false,
      sectionHeights: {},
    },
  };

  const LAYOUT_SCHEMA_VERSION = 2;
  const LEGACY_LAYOUT_STORAGE_KEYS = [
    "blackfong.thoughtglass.layout.v1",
    "blackfong.thoughtglass.layout",
    "blackfong.hud.layout.v1",
  ];
  const LAYOUT_STORAGE_KEY = `blackfong.thoughtglass.layout.v${LAYOUT_SCHEMA_VERSION}`;
  const SHORTCUT_OVERRIDES_STORAGE_KEY = "blackfong.shortcut.overrides.v1";
  const COMMAND_HISTORY_STORAGE_KEY = "blackfong.thoughtglass.commandHistory.v1";
  const UI_STATE_STORAGE_KEY = "blackfong.thoughtglass.uiState.v1";

  const el = {
    appShell: document.querySelector(".app-shell"),
    topbar: document.querySelector(".topbar"),
    mainGrid: document.querySelector(".main-grid"),
    layoutScrim: document.getElementById("layoutScrim"),
    leftRail: document.getElementById("leftRail"),
    rightRail: document.getElementById("rightRail"),
    toggleLeftRailButton: document.getElementById("toggleLeftRailButton"),
    toggleRightRailButton: document.getElementById("toggleRightRailButton"),
    leftRailToggle: document.getElementById("leftRailToggle"),
    floatLeftRailButton: document.getElementById("floatLeftRailButton"),
    dockLeftRailButton: document.getElementById("dockLeftRailButton"),
    floatRightRailButton: document.getElementById("floatRightRailButton"),
    dockRightRailButton: document.getElementById("dockRightRailButton"),
    rightRailToggle: document.getElementById("rightRailToggle"),
    resetLayoutButton: document.getElementById("resetLayoutButton"),
    statusChip: document.getElementById("statusChip"),
    backendChip: document.getElementById("backendChip"),
    layerChip: document.getElementById("layerChip"),
    transportChip: document.getElementById("transportChip"),
    workspaceTitle: document.getElementById("workspaceTitle"),
    workspaceSubhead: document.getElementById("workspaceSubhead"),
    metricsGrid: document.getElementById("metricsGrid"),
    memoryPanel: document.getElementById("memoryPanel"),
    sessionsPanel: document.getElementById("sessionsPanel"),
    sessionMeta: document.getElementById("sessionMeta"),
    operatorMode: document.getElementById("operatorMode"),
    thoughtglassMode: document.getElementById("thoughtglassMode"),
    workspaceMaps: document.getElementById("workspaceMaps"),
    workspaceTiny: document.getElementById("workspaceTiny"),
    workspaceFiles: document.getElementById("workspaceFiles"),
    workspaceSecurity: document.getElementById("workspaceSecurity"),
    workspaceQueue: document.getElementById("workspaceQueue"),
    workspaceTrace: document.getElementById("workspaceTrace"),
    operatorRibbon: document.getElementById("operatorRibbon"),
    thoughtglassRibbon: document.getElementById("thoughtglassRibbon"),
    chatTraceSummary: document.getElementById("chatTraceSummary"),
    thoughtglassTraceSummary: document.getElementById("thoughtglassTraceSummary"),
    drawerTraceSummary: document.getElementById("drawerTraceSummary"),
    pendingBanner: document.getElementById("pendingBanner"),
    pendingLabel: document.getElementById("pendingLabel"),
    pendingDetail: document.getElementById("pendingDetail"),
    pendingBadge: document.getElementById("pendingBadge"),
    conversationStatusChip: document.getElementById("conversationStatusChip"),
    messagesPanel: document.getElementById("messagesPanel"),
    chatInput: document.getElementById("chatInput"),
    agentSelect: document.getElementById("agentSelect"),
    analysisSelect: document.getElementById("analysisSelect"),
    sendButton: document.getElementById("sendButton"),
    traceButton: document.getElementById("traceButton"),
    pinButton: document.getElementById("pinButton"),
    refreshButton: document.getElementById("refreshButton"),
    newSessionButton: document.getElementById("newSessionButton"),
    whatChangedButton: document.getElementById("whatChangedButton"),
    tracePanel: document.getElementById("tracePanel"),
    actionsPanel: document.getElementById("actionsPanel"),
    notificationsPanel: document.getElementById("notificationsPanel"),
    notificationsReadAllButton: document.getElementById("notificationsReadAllButton"),
    testToastButton: document.getElementById("testToastButton"),
    commandPalette: document.getElementById("commandPalette"),
    commandSearchInput: document.getElementById("commandSearchInput"),
    commandHistory: document.getElementById("commandHistory"),
    openFilesWorkspaceButton: document.getElementById("openFilesWorkspaceButton"),
    focusFilesDrawerButton: document.getElementById("focusFilesDrawerButton"),
    scopePathInput: document.getElementById("scopePathInput"),
    scopeSummaryPanel: document.getElementById("scopeSummaryPanel"),
    scopePanel: document.getElementById("scopePanel"),
    scopeDetailPanel: document.getElementById("scopeDetailPanel"),
    tinyPanel: document.getElementById("tinyPanel"),
    tinyLogPanel: document.getElementById("tinyLogPanel"),
    thoughtglassGraph: document.getElementById("thoughtglassGraph"),
    thoughtglassFx: document.getElementById("thoughtglassFx"),
    thoughtglassStage: document.getElementById("thoughtglassStage"),
    thoughtglassCards: document.getElementById("thoughtglassCards"),
    chatBrainPanel: document.getElementById("chatBrainPanel"),
    thoughtglassDetails: document.getElementById("thoughtglassDetails"),
    thoughtglassFocusBadge: document.getElementById("thoughtglassFocusBadge"),
    toastStack: document.getElementById("toastStack"),
    verificationPanel: document.getElementById("verificationPanel"),
    redamonPanel: document.getElementById("redamonPanel"),
    integrationPanel: document.getElementById("integrationPanel"),
    gpsStatusPanel: document.getElementById("gpsStatusPanel"),
    gpsRouteView: document.getElementById("gpsRouteView"),
    redamonObjectiveInput: document.getElementById("redamonObjectiveInput"),
    redamonTargetInput: document.getElementById("redamonTargetInput"),
    refreshIntegrationsButton: document.getElementById("refreshIntegrationsButton"),
    workbenchTabButtons: Array.from(document.querySelectorAll("[data-workspace-tab]")),
    drawerTabButtons: Array.from(document.querySelectorAll("[data-drawer-tab]")),
    drawerPanels: {
      detail: document.getElementById("drawerDetailPanel"),
      notifications: document.getElementById("drawerNotificationsPanel"),
      commands: document.getElementById("drawerCommandsPanel"),
    },
    layerButtons: Array.from(document.querySelectorAll(".layer-chip")),
    tinyButtons: Array.from(document.querySelectorAll("[data-tiny-action]")),
    fileScopeButtons: Array.from(document.querySelectorAll("[data-file-scope-action]")),
    verifyButtons: Array.from(document.querySelectorAll("[data-verify-mode]")),
    linkedSessionChip: document.getElementById("linkedSessionChip"),
    keyboardShortcutsPanel: document.getElementById("keyboardShortcutsPanel"),
    redamonButtons: Array.from(document.querySelectorAll("[data-redamon-action]")),
    gpsButtons: Array.from(document.querySelectorAll("[data-gps-action]")),
    columnResizers: Array.from(document.querySelectorAll("[data-column-resizer]")),
    popoutButtons: Array.from(document.querySelectorAll("[data-open-workspace-window]")),
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  const SECTION_HEIGHT_LIMITS = {
    chat_ribbon: { min: 140, max: 300 },
    chat_messages: { min: 220, max: 960 },
    chat_composer: { min: 180, max: 360 },
  };

  function normalizeSectionHeight(sectionId, height) {
    const limits = SECTION_HEIGHT_LIMITS[sectionId];
    if (!limits) {
      return clamp(Number(height) || 0, 120, 900);
    }
    return clamp(Number(height) || 0, limits.min, limits.max);
  }

  function sanitizeLayoutSectionHeights(sectionHeights) {
    const next = {};
    Object.entries(sectionHeights || {}).forEach(([sectionId, height]) => {
      const normalized = normalizeSectionHeight(sectionId, height);
      if (normalized > 0) {
        next[sectionId] = normalized;
      }
    });
    return next;
  }

  function logChatLayoutState(location) {
    const panel = el.messagesPanel;
    if (!panel) {
      return;
    }
    const ribbon = document.querySelector('[data-resizable-section="chat_ribbon"]');
    debugHudLog("I", location, "Chat layout metrics", {
      messages_clientHeight: panel.clientHeight,
      messages_scrollHeight: panel.scrollHeight,
      messages_scrollable: panel.scrollHeight > panel.clientHeight + 4,
      ribbon_clientHeight: ribbon?.clientHeight ?? null,
      ribbon_flex: ribbon?.style?.flex || "",
      sectionHeights: { ...(state.layout.sectionHeights || {}) },
    });
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function debounce(fn, delay = 180) {
    let handle = null;
    return (...args) => {
      window.clearTimeout(handle);
      handle = window.setTimeout(() => fn(...args), delay);
    };
  }

  function clearLegacyLayoutState() {
    LEGACY_LAYOUT_STORAGE_KEYS.forEach((key) => {
      try {
        window.localStorage.removeItem(key);
      } catch (error) {
        // Ignore storage failures in restrictive browser modes.
      }
    });
  }

  function currentViewportBucket() {
    const width = window.innerWidth || document.documentElement.clientWidth || 1440;
    if (width >= 1400) {
      return "wide";
    }
    if (width >= 1100) {
      return "medium";
    }
    if (width >= 768) {
      return "narrow";
    }
    return "mobile";
  }

  function isOverlayRail(side) {
    const bucket = state.viewportBucket || "wide";
    if (side === "right") {
      return bucket !== "wide" || state.layout.floatingRight || state.layout.focusedView;
    }
    return bucket === "narrow" || bucket === "mobile" || state.layout.floatingLeft || state.layout.focusedView;
  }

  function shouldShowLayoutScrim() {
    if (state.layout.focusedView && (!state.layout.collapsedLeft || !state.layout.collapsedRight)) {
      return true;
    }
    const leftOpen = !state.layout.collapsedLeft && isOverlayRail("left");
    const rightOpen = !state.layout.collapsedRight && isOverlayRail("right");
    return leftOpen || rightOpen;
  }

  function updateLayoutScrim() {
    if (!el.layoutScrim) {
      return;
    }
    el.layoutScrim.classList.toggle("hidden", !shouldShowLayoutScrim());
  }

  function syncUrlState() {
    const url = new URL(window.location.href);
    url.searchParams.set("workspace", state.workspaceTab || "chat");
    url.searchParams.set("drawer", state.drawerTab || "detail");
    url.searchParams.set("layer", state.layer || "live_brain");
    if (state.layout.focusedView) {
      url.searchParams.set("focus", "1");
    } else {
      url.searchParams.delete("focus");
    }
    window.history.replaceState({}, "", `${url.pathname}?${url.searchParams.toString()}`);
  }

  function applyRouteStateFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const workspace = params.get("workspace");
    const drawer = params.get("drawer");
    const layer = params.get("layer");
    const focus = params.get("focus");
    if (workspace) {
      state.workspaceTab = workspace;
    }
    if (drawer) {
      state.drawerTab = drawer;
    }
    if (layer) {
      state.layer = layer;
    }
    state.layout.focusedView = focus === "1" || focus === "true";
  }

  function buildWorkspaceUrl(workspace, focused = true) {
    const url = new URL(window.location.href);
    url.searchParams.set("workspace", workspace || "chat");
    url.searchParams.set("drawer", state.drawerTab || "detail");
    url.searchParams.set("layer", state.layer || "live_brain");
    if (focused) {
      url.searchParams.set("focus", "1");
    } else {
      url.searchParams.delete("focus");
    }
    return url.toString();
  }

  function openWorkspaceWindow(workspace) {
    window.open(buildWorkspaceUrl(workspace, true), `obeos-${workspace || "workspace"}`);
  }

  function updateViewportState() {
    state.viewportBucket = currentViewportBucket();
    document.body.dataset.viewportBucket = state.viewportBucket;
    document.body.classList.toggle("focused-workspace", Boolean(state.layout.focusedView));
    const topOffset = Math.round((el.topbar?.getBoundingClientRect().bottom || 90) + 8);
    document.documentElement.style.setProperty("--dock-top-offset", `${topOffset}px`);
    updateLayoutScrim();
  }

  function loadLayoutState() {
    try {
      clearLegacyLayoutState();
      const parsed = JSON.parse(window.localStorage.getItem(LAYOUT_STORAGE_KEY) || "{}");
      state.layout = {
        leftWidth: Number(parsed.leftWidth) || state.layout.leftWidth,
        rightWidth: Number(parsed.rightWidth) || state.layout.rightWidth,
        collapsedLeft: Boolean(parsed.collapsedLeft),
        collapsedRight: Boolean(parsed.collapsedRight),
        floatingLeft: Boolean(parsed.floatingLeft),
        floatingRight: Boolean(parsed.floatingRight),
        focusedView: state.layout.focusedView,
        sectionHeights: sanitizeLayoutSectionHeights(
          parsed.sectionHeights && typeof parsed.sectionHeights === "object" ? parsed.sectionHeights : {},
        ),
      };
      state.layout.sectionHeights = sanitizeLayoutSectionHeights(state.layout.sectionHeights);
    } catch (error) {
      state.layout = {
        leftWidth: 300,
        rightWidth: 360,
        collapsedLeft: false,
        collapsedRight: false,
        floatingLeft: false,
        floatingRight: false,
        focusedView: false,
        sectionHeights: {},
      };
    }
  }

  function loadCommandHistory() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(COMMAND_HISTORY_STORAGE_KEY) || "[]");
      state.commandHistory = Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      state.commandHistory = [];
    }
  }

  function loadUiState() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(UI_STATE_STORAGE_KEY) || "{}");
      if (parsed.workspaceTab) {
        state.workspaceTab = parsed.workspaceTab;
      }
      if (parsed.drawerTab) {
        state.drawerTab = parsed.drawerTab;
      }
      if (parsed.layer) {
        state.layer = parsed.layer;
      }
      if (parsed.commandQuery) {
        state.commandQuery = String(parsed.commandQuery);
      }
    } catch (error) {
      state.workspaceTab = "chat";
      state.drawerTab = "detail";
      state.layer = "live_brain";
      state.commandQuery = "";
    }
  }

  function persistLayoutState() {
    try {
      const persisted = { ...state.layout };
      delete persisted.focusedView;
      window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(persisted));
    } catch (error) {
      // Ignore storage failures in restrictive browser modes.
    }
  }

  function resetLayoutState() {
    state.layout = {
      leftWidth: 300,
      rightWidth: 360,
      collapsedLeft: false,
      collapsedRight: false,
      floatingLeft: false,
      floatingRight: false,
      focusedView: state.layout.focusedView,
      sectionHeights: {},
    };
    persistLayoutState();
    applyLayoutState();
  }

  function applyLayoutState() {
    const leftWidth = state.layout.collapsedLeft ? 68 : clamp(Number(state.layout.leftWidth) || 300, 220, 520);
    const rightWidth = state.layout.collapsedRight ? 68 : clamp(Number(state.layout.rightWidth) || 360, 280, 560);
    document.documentElement.style.setProperty("--left-rail-width", `${leftWidth}px`);
    document.documentElement.style.setProperty("--right-rail-width", `${rightWidth}px`);
    el.leftRail?.classList.toggle("collapsed", state.layout.collapsedLeft);
    el.rightRail?.classList.toggle("collapsed", state.layout.collapsedRight);
    el.leftRail?.classList.toggle("floating-rail", state.layout.floatingLeft);
    el.rightRail?.classList.toggle("floating-rail", state.layout.floatingRight);
    if (state.layout.floatingLeft || isOverlayRail("left")) {
      document.documentElement.style.setProperty("--left-rail-width", "0px");
    }
    if (state.layout.floatingRight || isOverlayRail("right")) {
      document.documentElement.style.setProperty("--right-rail-width", "0px");
    }
    if (el.leftRailToggle) {
      el.leftRailToggle.textContent = state.layout.collapsedLeft ? "Show" : "Hide";
    }
    if (el.rightRailToggle) {
      el.rightRailToggle.textContent = state.layout.collapsedRight ? "Show" : "Hide";
    }
    if (el.toggleLeftRailButton) {
      el.toggleLeftRailButton.textContent = state.layout.collapsedLeft ? "Navigator" : "Hide Navigator";
    }
    if (el.toggleRightRailButton) {
      el.toggleRightRailButton.textContent = state.layout.collapsedRight ? "Utilities" : "Hide Utilities";
    }
    Object.entries(state.layout.sectionHeights || {}).forEach(([sectionId, height]) => {
      const section = document.querySelector(`[data-resizable-section="${sectionId}"]`);
      if (!section) {
        return;
      }
      const normalized = normalizeSectionHeight(sectionId, height);
      if (!normalized) {
        return;
      }
      section.style.flex = `0 0 ${normalized}px`;
    });
    updateViewportState();
  }

  function persistCommandHistory() {
    try {
      window.localStorage.setItem(COMMAND_HISTORY_STORAGE_KEY, JSON.stringify(state.commandHistory.slice(0, 24)));
    } catch (error) {
      // Ignore storage failures in restrictive browser modes.
    }
  }

  function persistUiState() {
    try {
      window.localStorage.setItem(
        UI_STATE_STORAGE_KEY,
        JSON.stringify({
          workspaceTab: state.workspaceTab,
          drawerTab: state.drawerTab,
          layer: state.layer,
          commandQuery: state.commandQuery,
        }),
      );
    } catch (error) {
      // Ignore storage failures in restrictive browser modes.
    }
  }

  function prettyJson(value) {
    return `<pre class="tiny-preview">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
  }

  function formatTime(value) {
    if (!value) {
      return "now";
    }
    const date = new Date(Number(value) * 1000);
    if (Number.isNaN(date.getTime())) {
      return "now";
    }
    return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
  }

  function summarize(value, limit = 220) {
    const text = typeof value === "string" ? value : JSON.stringify(value);
    if (!text) {
      return "";
    }
    return text.length > limit ? `${text.slice(0, limit)}...` : text;
  }

  function displayAgentName(value) {
    const normalized = String(value || "daise").trim().toLowerCase();
    const mapping = {
      jarvis: "D.A.I.S.E General",
      daise: "D.A.I.S.E",
      "d.a.i.s.e": "D.A.I.S.E",
      coding: "Coding",
      lilo: "Lilo",
      stitch: "Stitch",
      voice: "Voice",
      operator: "Operator",
      system: "System",
    };
    return mapping[normalized] || String(value || "D.A.I.S.E");
  }

  function formatStateLabel(value) {
    return String(value || "idle").replace(/_/g, " ");
  }

  function metricEntries(metrics) {
    return Object.entries(metrics || {}).filter(([, value]) => value !== null && value !== undefined && value !== "");
  }

  function collectNodeConnections(snapshot, nodeId) {
    const nodeLabels = Object.fromEntries((snapshot.nodes || []).map((node) => [node.id, node.label || node.id]));
    return (snapshot.edges || [])
      .filter((edge) => edge.source === nodeId || edge.target === nodeId)
      .map((edge) => ({
        label: edge.label || "route",
        peer: nodeLabels[edge.source === nodeId ? edge.target : edge.source] || (edge.source === nodeId ? edge.target : edge.source),
        state: edge.state || "idle",
        confidence: Number(edge.confidence || 0),
      }));
  }

  async function fetchJSON(url, options) {
    const started = performance.now();
    const action = `${options?.method || "GET"} ${url}`;
    try {
      const response = await fetch(url, options);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const error = new Error(body.error || `Request failed: ${response.status}`);
        error.payload = body;
        throw error;
      }
      debugHudLog("H", "dashboard.js:fetchJSON", "HUD API ok", {
        action,
        status: response.status,
        duration_ms: Math.round(performance.now() - started),
      });
      return body;
    } catch (error) {
      debugHudLog("H", "dashboard.js:fetchJSON", "HUD API failed", {
        action,
        duration_ms: Math.round(performance.now() - started),
        error: error.message,
      });
      throw error;
    }
  }

  async function fetchOptionalJSON(url, fallback) {
    try {
      return await fetchJSON(url);
    } catch (error) {
      return {
        ...(fallback || {}),
        success: false,
        error: error.message,
        human_reason: error.message,
      };
    }
  }

  function showToast(message, level = "info") {
    const toast = document.createElement("div");
    toast.className = `toast ${level}`;
    toast.innerHTML = `<strong>${escapeHtml(level.toUpperCase())}</strong><div>${escapeHtml(message)}</div>`;
    el.toastStack.appendChild(toast);
    window.setTimeout(() => {
      toast.remove();
    }, 4200);
  }

  function setTransportState(mode, status) {
    const normalizedMode = mode || "polling";
    const normalizedStatus = status || "idle";
    el.transportChip.textContent = `transport ${normalizedMode} ${normalizedStatus}`;
    el.transportChip.classList.toggle("active", normalizedStatus === "connected");
  }

  function installColumnResizers() {
    const minCenterWidth = 420;
    const resizerSize = 20;
    el.columnResizers.forEach((resizer) => {
      resizer.addEventListener("pointerdown", (event) => {
        if (!el.mainGrid) {
          return;
        }
        event.preventDefault();
        resizer.classList.add("dragging");
        const rect = el.mainGrid.getBoundingClientRect();
        const kind = resizer.dataset.columnResizer;
        const onMove = (moveEvent) => {
          const gridWidth = rect.width;
          if (kind === "left") {
            const maxLeft = gridWidth - state.layout.rightWidth - minCenterWidth - resizerSize * 2;
            state.layout.leftWidth = clamp(moveEvent.clientX - rect.left, 220, Math.max(220, maxLeft));
          } else {
            const maxRight = gridWidth - state.layout.leftWidth - minCenterWidth - resizerSize * 2;
            state.layout.rightWidth = clamp(rect.right - moveEvent.clientX, 280, Math.max(280, maxRight));
          }
          applyLayoutState();
        };
        const onUp = () => {
          resizer.classList.remove("dragging");
          window.removeEventListener("pointermove", onMove);
          window.removeEventListener("pointerup", onUp);
          persistLayoutState();
        };
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);
      });
    });
  }

  function installPanelResizers() {
    const containers = [
      document.querySelector(".left-rail"),
      document.querySelector(".right-rail"),
      document.querySelector("#operatorMode .chat-workspace"),
      document.getElementById("operatorMode"),
      document.getElementById("thoughtglassMode"),
    ].filter(Boolean);

    containers.forEach((container) => {
      const sections = Array.from(container.querySelectorAll(":scope > .panel-section[data-resizable-section]"));
      sections.forEach((section, index) => {
        if (index === sections.length - 1) {
          return;
        }
        const next = sections[index + 1];
        const handle = document.createElement("div");
        handle.className = "panel-resizer";
        handle.setAttribute("role", "separator");
        handle.setAttribute("aria-orientation", "horizontal");
        handle.addEventListener("pointerdown", (event) => {
          event.preventDefault();
          handle.classList.add("dragging");
          const sectionRect = section.getBoundingClientRect();
          const nextRect = next.getBoundingClientRect();
          const startY = event.clientY;
          const startSection = sectionRect.height;
          const startNext = nextRect.height;
          const onMove = (moveEvent) => {
            const delta = moveEvent.clientY - startY;
            const nextSectionHeight = normalizeSectionHeight(
              section.dataset.resizableSection,
              startSection + delta,
            );
            const nextSiblingHeight = normalizeSectionHeight(
              next.dataset.resizableSection,
              startNext - delta,
            );
            section.style.flex = `0 0 ${nextSectionHeight}px`;
            next.style.flex = `0 0 ${nextSiblingHeight}px`;
            state.layout.sectionHeights[section.dataset.resizableSection] = nextSectionHeight;
            state.layout.sectionHeights[next.dataset.resizableSection] = nextSiblingHeight;
          };
          const onUp = () => {
            handle.classList.remove("dragging");
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
            persistLayoutState();
          };
          window.addEventListener("pointermove", onMove);
          window.addEventListener("pointerup", onUp);
        });
        next.before(handle);
      });
    });
  }

  function setDrawerTab(tab) {
    state.drawerTab = tab || "detail";
    el.drawerTabButtons.forEach((button) => {
      button.classList.toggle("active", button.dataset.drawerTab === state.drawerTab);
    });
    Object.entries(el.drawerPanels).forEach(([panelKey, panel]) => {
      if (!panel) {
        return;
      }
      panel.classList.toggle("active", panelKey === state.drawerTab);
    });
    if (state.layout.collapsedRight) {
      state.layout.collapsedRight = false;
      persistLayoutState();
      applyLayoutState();
    }
    persistUiState();
    syncUrlState();
  }

  function setWorkspaceTab(tab) {
    const normalized = tab || "chat";
    state.workspaceTab = normalized;
    const tabMap = {
      chat: el.operatorMode,
      thoughtglass: el.thoughtglassMode,
      maps: el.workspaceMaps,
      tiny: el.workspaceTiny,
      files: el.workspaceFiles,
      security: el.workspaceSecurity,
      queue: el.workspaceQueue,
      trace: el.workspaceTrace,
    };
    const labels = {
      chat: {
        title: "Operator",
        subhead: "Conversation, trace, and live brain state stay together so the control plane feels local instead of scattered.",
      },
      thoughtglass: {
        title: "Thoughtglass",
        subhead: "System visibility gets the whole center when cognition is the task.",
      },
      maps: {
        title: "GPS + Maps",
        subhead: "Shared route awareness stays bounded, offline-aware, and operator-readable.",
      },
      tiny: {
        title: "TinyProgrammer",
        subhead: "Native window state, stream state, BBS, and logs stay in one bounded cockpit.",
      },
      files: {
        title: "Files + Scope",
        subhead: "Machine-wide read-map first. Governed writes stay inside governed project roots.",
      },
      security: {
        title: "Security + Integrations",
        subhead: "Verification, RedAmon review mode, and optional external tools stay visible without becoming ambient authority.",
      },
      queue: {
        title: "Queue",
        subhead: "Operator intents, action states, and durable command history stay readable here.",
      },
      trace: {
        title: "Trace",
        subhead: "Full request detail stays separate from the conversation workspace.",
      },
    };
    Object.entries(tabMap).forEach(([panelKey, panel]) => {
      if (panel) {
        panel.classList.toggle("active", panelKey === normalized);
      }
    });
    el.workbenchTabButtons.forEach((button) => {
      button.classList.toggle("active", button.dataset.workspaceTab === normalized);
    });
    const meta = labels[normalized] || labels.chat;
    if (el.workspaceTitle) {
      el.workspaceTitle.textContent = meta.title;
    }
    if (el.workspaceSubhead) {
      el.workspaceSubhead.textContent = meta.subhead;
    }
    state.mode = normalized === "thoughtglass" ? "thoughtglass" : "operator";
    if (normalized === "thoughtglass") {
      refreshCognition().catch(() => {});
    }
    if (normalized === "trace") {
      refreshTrace().catch(() => {});
      setDrawerTab("detail");
    }
    if (normalized === "tiny") {
      refreshTiny().catch(() => {});
      setDrawerTab("detail");
    }
    if (normalized === "maps") {
      refreshGpsStatus().catch(() => {});
      setDrawerTab("detail");
    }
    if (normalized === "files") {
      refreshFileScopes().catch(() => {});
      setDrawerTab("detail");
    }
    if (normalized === "security") {
      Promise.all([refreshVerification("surfaces"), refreshIntegrations(), refreshRedamonStatus()]).catch(() => {});
      setDrawerTab("detail");
    }
    if (normalized === "queue") {
      refreshActions().catch(() => {});
      setDrawerTab("commands");
    }
    persistUiState();
    syncUrlState();
  }

  function setMode(mode) {
    if (mode === "thoughtglass") {
      setWorkspaceTab("thoughtglass");
      return;
    }
    if (state.workspaceTab === "thoughtglass") {
      setWorkspaceTab("chat");
      return;
    }
    state.mode = "operator";
  }

  function toggleRail(side) {
    if (side === "left") {
      state.layout.collapsedLeft = !state.layout.collapsedLeft;
    } else {
      state.layout.collapsedRight = !state.layout.collapsedRight;
    }
    persistLayoutState();
    applyLayoutState();
  }

  function floatRail(side) {
    if (side === "left") {
      state.layout.floatingLeft = true;
      state.layout.collapsedLeft = false;
    } else {
      state.layout.floatingRight = true;
      state.layout.collapsedRight = false;
    }
    persistLayoutState();
    applyLayoutState();
  }

  function dockRail(side) {
    if (side === "left") {
      state.layout.floatingLeft = false;
    } else {
      state.layout.floatingRight = false;
    }
    persistLayoutState();
    applyLayoutState();
  }

  function setLayer(layer) {
    state.layer = layer || "live_brain";
    el.layerChip.textContent = `layer ${state.layer.replace(/_/g, " ")}`;
    el.layerButtons.forEach((button) => {
      button.classList.toggle("active", button.dataset.layer === state.layer);
    });
    persistUiState();
    syncUrlState();
  }

  function pushCommandHistory(entry) {
    state.commandHistory.unshift({
      timestamp: Date.now(),
      command: entry.command || "command",
      result: entry.result || entry,
    });
    state.commandHistory = state.commandHistory.slice(0, 24);
    persistCommandHistory();
    renderCommandHistory();
  }

  function renderCommandHistory() {
    if (!state.commandHistory.length) {
      el.commandHistory.innerHTML = `<div class="command-card"><strong>No command history yet.</strong><div class="micro-copy">Command results stay visible here instead of flashing and disappearing.</div></div>`;
      return;
    }
    el.commandHistory.innerHTML = state.commandHistory
      .map((entry) => {
        const result = entry.result || {};
        return `
          <div class="command-card">
            <div class="message-head">
              <strong>${escapeHtml(entry.command)}</strong>
              <span>${formatTime(entry.timestamp / 1000)}</span>
            </div>
            <div>${escapeHtml(summarize(result.summary_text || result.human_reason || result.status || result.error || ""))}</div>
            ${prettyJson(result)}
          </div>
        `;
      })
      .join("");
  }

  function renderMetrics(metrics) {
    const cards = [
      { label: "Requests", value: metrics.total_requests || 0 },
      { label: "Sessions", value: metrics.active_sessions || 0 },
      { label: "Avg Response", value: `${Number(metrics.avg_response_time || 0).toFixed(2)}s` },
      { label: "Errors", value: `${Math.round(Number(metrics.error_rate || 0) * 100)}%` },
      { label: "Memory", value: `${Number(metrics.memory_usage_mb || 0).toFixed(1)} MB` },
      { label: "Health", value: String(metrics.system_health || "unknown").toUpperCase() },
    ];
    el.metricsGrid.innerHTML = cards
      .map((card) => `<div class="metric-card"><span>${escapeHtml(card.label)}</span><strong>${escapeHtml(card.value)}</strong></div>`)
      .join("");
  }

  function renderSessions(sessions) {
    state.sessions = sessions;
    if (!state.sessionId && sessions.length) {
      state.sessionId = sessions[0].session_id;
    }
    el.sessionMeta.textContent = state.sessionId ? `Session ${state.sessionId}` : "No session selected";
    el.sessionsPanel.innerHTML = sessions.length
      ? sessions
          .map((session) => {
            const active = session.session_id === state.sessionId ? "active" : "";
            return `
              <button class="session-card button button-subtle ${active}" data-session-id="${escapeHtml(session.session_id)}">
                <strong>${escapeHtml(session.preview || session.session_id)}</strong>
                <div class="micro-copy">${escapeHtml(session.status || "active")} | ${session.total_requests || 0} requests</div>
              </button>
            `;
          })
          .join("")
      : `<div class="session-card"><strong>No sessions yet.</strong><div class="micro-copy">Start a conversation and it will appear here.</div></div>`;
    el.sessionsPanel.querySelectorAll("[data-session-id]").forEach((button) => {
      button.addEventListener("click", () => {
        state.sessionId = button.dataset.sessionId;
        state.requestId = null;
        state.localMessages = [];
        refreshSessionSurfaces();
      });
    });
  }

  function renderMemory(payload) {
    const memories = payload.memories || [];
    const stats = payload.stats || {};
    const statCard = `
      <div class="memory-card">
        <strong>Memory State</strong>
        <div class="micro-copy">Pinned ${stats.pinned_memories || 0} | Summaries ${stats.recent_summaries || 0} | Sessions ${stats.sessions || 0}</div>
      </div>
    `;
    const cards = memories.length
      ? memories
          .map((memory) => `
            <div class="memory-card">
              <strong>${escapeHtml(memory.title || memory.agent || "Memory")}</strong>
              <div>${escapeHtml(summarize(memory.text || memory.summary || memory.response_preview || ""))}</div>
            </div>
          `)
          .join("")
      : `<div class="memory-card"><strong>No memory matches.</strong><div class="micro-copy">Pinned memories, rolling summaries, and recent session facts will surface here.</div></div>`;
    el.memoryPanel.innerHTML = statCard + cards;
  }

  function looksTechnicalPayload(text) {
    const full = String(text || "").trim();
    if (!full) {
      return false;
    }
    const newlineCount = (full.match(/\n/g) || []).length;
    const jsonLike = (/^[\[{]/.test(full) && /[\]}]$/.test(full)) || full.startsWith("```json");
    const logLike = newlineCount >= 12 && /(traceback|exception|error|warn|info|debug|\d{4}-\d{2}-\d{2})/i.test(full);
    const codeLike = full.includes("```") || (newlineCount >= 12 && /[{}[\];=<>()]/.test(full));
    return jsonLike || logLike || codeLike;
  }

  function shouldCollapseMessageBody(text) {
    const full = String(text || "");
    return full.length > 2400 && looksTechnicalPayload(full);
  }

  function isMessagePanelNearBottom() {
    if (!el.messagesPanel) {
      return true;
    }
    const distance = el.messagesPanel.scrollHeight - el.messagesPanel.scrollTop - el.messagesPanel.clientHeight;
    return distance < 96;
  }

  function updateConversationStatus(label, status = "idle") {
    if (!el.conversationStatusChip) {
      return;
    }
    el.conversationStatusChip.textContent = label || "idle";
    el.conversationStatusChip.classList.toggle("active", status === "active" || status === "streaming");
    el.conversationStatusChip.classList.toggle("danger-chip", status === "error");
  }

  function formatMessageBody(text, messageIndex) {
    const full = String(text || "");
    if (!shouldCollapseMessageBody(full)) {
      return `<div class="message-body">${escapeHtml(full)}</div>`;
    }
    const isExpanded = state.expandedMessages.has(messageIndex);
    if (isExpanded) {
      return `<div class="message-body">${escapeHtml(full)}</div><button type="button" class="message-expand" data-msg-index="${messageIndex}" data-action="collapse">Show less</button>`;
    }
    return `<div class="message-body collapsed">${escapeHtml(full)}</div><button type="button" class="message-expand" data-msg-index="${messageIndex}" data-action="expand">Show more</button>`;
  }

  function renderMessages(payload) {
    const messages = payload.messages || [];
    const shouldStick = isMessagePanelNearBottom();
    const previousScrollTop = el.messagesPanel?.scrollTop || 0;
    const previousScrollHeight = el.messagesPanel?.scrollHeight || 0;
    state.lastRenderedMessages = messages;
    const combined = messages.slice();
    if (state.localMessages.length) {
      state.localMessages.forEach((message) => combined.push(message));
    }
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
          .map((message, index) => {
            const role = message.role === "assistant" ? "assistant" : message.role === "system" ? "system" : "user";
            const label = role === "assistant" ? "D.A.I.S.E" : role === "system" ? "System" : "You";
            const body = message.streaming
              ? `<div class="message-body">${escapeHtml(message.text || message.content || "")}</div>`
              : formatMessageBody(message.text || message.content || "", index);
            return `
              <article class="message ${role}">
                <div class="message-head">
                  <strong>${escapeHtml(label)}</strong>
                  <span>${formatTime(message.timestamp || message.created_at || 0)}</span>
                </div>
                ${body}
              </article>
            `;
          })
          .join("")
      : `<div class="message system"><div class="message-head"><strong>System</strong><span>idle</span></div><div class="message-body">Start a conversation with D.A.I.S.E to populate this session.</div></div>`;
    if (shouldStick) {
      el.messagesPanel.scrollTop = el.messagesPanel.scrollHeight;
    } else {
      const heightDelta = el.messagesPanel.scrollHeight - previousScrollHeight;
      el.messagesPanel.scrollTop = previousScrollTop + Math.max(0, heightDelta);
    }
    window.requestAnimationFrame(() => logChatLayoutState("dashboard.js:renderMessages"));
  }

  function renderTraceRibbon(target, ribbon) {
    target.innerHTML = (ribbon || [])
      .map((step) => `<div class="trace-step ${escapeHtml(step.status || "pending")}">${escapeHtml(step.label || step.id || "STEP")}</div>`)
      .join("");
  }

  function buildTraceSummaryHtml(trace) {
    const report = (trace.reports || [])[0] || {};
    const latestEvent = ((trace.events || []).slice(-1)[0] || {}).payload || {};
    const latestEventText = latestEvent.message || latestEvent.response || JSON.stringify(latestEvent || {});
    const fastPathLine = trace.fast_path || (trace.result || {}).fast_path
      ? `<div class="micro-copy">Fast path: single-agent quick route</div>`
      : "";
    return `
      <div class="trace-summary-card">
        <strong>Phase</strong>
        <div>${escapeHtml(trace.phase || "idle")}</div>
        <div class="micro-copy">${escapeHtml(trace.status || "idle")}</div>
        ${fastPathLine}
      </div>
      <div class="trace-summary-card">
        <strong>Route</strong>
        <div>${escapeHtml(displayAgentName(trace.selected_agent || (trace.selection || {}).selected_agent || "daise"))}</div>
        <div class="micro-copy">${escapeHtml([trace.provider || "", trace.model || ""].filter(Boolean).join(" | ") || "provider pending")}</div>
      </div>
      <div class="trace-summary-card">
        <strong>Latest Report</strong>
        <div>${escapeHtml(summarize(report.response || report.evidence_summary || trace.phase_message || "", 180) || "Waiting for the first report.")}</div>
      </div>
      <div class="trace-summary-card">
        <strong>Latest Event</strong>
        <div>${escapeHtml(summarize(latestEventText, 180) || "No recent trace event.")}</div>
      </div>
    `;
  }

  function renderTrace(trace) {
    state.currentTrace = trace;
    const phase = trace && trace.phase ? trace.phase : "idle";
    const status = trace && trace.status ? trace.status : "idle";
    const statusLabel = status === "pending" ? `${phase}` : status === "complete" ? "complete" : status === "error" ? "failed" : status;
    updateConversationStatus(statusLabel, status === "pending" ? "streaming" : status === "error" ? "error" : "idle");
    el.pendingBadge.textContent = `${phase} | ${status}`;
    renderTraceRibbon(el.operatorRibbon, trace.trace_ribbon || buildRibbonFromTrace(trace));
    renderTraceRibbon(el.thoughtglassRibbon, trace.trace_ribbon || buildRibbonFromTrace(trace));
    const reports = trace.reports || [];
    const events = (trace.events || []).slice(-8).reverse();
    el.tracePanel.innerHTML = `
      <div class="trace-event">
        <strong>Phase</strong>
        <div>${escapeHtml(phase)}</div>
        <div class="micro-copy">${escapeHtml(trace.phase_message || "")}</div>
      </div>
      <div class="trace-event">
        <strong>Selected Agent</strong>
        <div>${escapeHtml(displayAgentName(trace.selected_agent || (trace.selection || {}).selected_agent || "daise"))}</div>
        <div class="micro-copy">${escapeHtml(trace.provider || "")} ${escapeHtml(trace.model || "")}</div>
      </div>
      ${reports.map((report) => `
        <div class="trace-event">
          <strong>${escapeHtml(displayAgentName(report.agent || "agent"))}</strong>
          <div>${escapeHtml(summarize(report.response || report.evidence_summary || "", 160))}</div>
          <div class="micro-copy">confidence ${Number(report.confidence || 0).toFixed(2)}</div>
        </div>
      `).join("")}
      ${events.map((event) => `
        <div class="trace-event">
          <strong>${escapeHtml(event.event_type || "event")}</strong>
          <div>${escapeHtml(summarize((event.payload || {}).message || JSON.stringify(event.payload || {}), 170))}</div>
        </div>
      `).join("")}
    `;
    const traceSummaryHtml = buildTraceSummaryHtml(trace);
    state.traceSummaryHtml = traceSummaryHtml;
    if (el.chatTraceSummary) {
      el.chatTraceSummary.innerHTML = traceSummaryHtml;
    }
    if (el.thoughtglassTraceSummary) {
      el.thoughtglassTraceSummary.innerHTML = traceSummaryHtml;
    }
    if (el.drawerTraceSummary) {
      el.drawerTraceSummary.innerHTML = `
        <div class="section-kicker">Live Detail</div>
        <h3>Request Summary</h3>
        <div class="trace-summary-strip">${traceSummaryHtml}</div>
      `;
    }
    const pending = status === "pending";
    el.pendingBanner.classList.toggle("hidden", !pending);
    el.pendingLabel.textContent = pending ? `Phase: ${phase}` : status === "error" ? "Request failed" : "Idle";
    el.pendingDetail.textContent = trace.phase_message || (pending ? "D.A.I.S.E is still working through the live workflow." : "No active request.");
  }

  function buildRibbonFromTrace(trace) {
    const phase = (trace && trace.phase) || "idle";
    const status = (trace && trace.status) || "idle";
    const steps = [
      ["INPUT", "accepted"],
      ["INTENT", "policy"],
      ["MEMORY", "memory"],
      ["ROUTING", "routing"],
      ["TOOL/AGENT", "agent"],
      ["VALIDATION", "evaluation"],
      ["RESPONSE", "complete"],
    ];
    const rank = { accepted: 0, policy: 1, memory: 2, routing: 3, agent: 4, evaluation: 5, complete: 6, error: 6, idle: -1 };
    const current = rank[phase] == null ? -1 : rank[phase];
    return steps.map(([label, key], index) => {
      let stepStatus = "pending";
      if (index < current || status === "complete") {
        stepStatus = "complete";
      }
      if (key === phase && status === "pending") {
        stepStatus = "active";
      }
      if (status === "error" && index >= Math.max(current, 0)) {
        stepStatus = "fault";
      }
      return { label, status: stepStatus };
    });
  }

  function renderActions(payload) {
    const actions = payload.actions || [];
    el.actionsPanel.innerHTML = actions.length
      ? actions
          .map((action) => `
            <div class="action-card">
              <div class="message-head">
                <strong>${escapeHtml(action.action || "action")}</strong>
                <span>${escapeHtml(action.status || "unknown")}</span>
              </div>
              <div>${escapeHtml(action.text || action.human_reason || "")}</div>
              <div class="micro-copy">owner ${escapeHtml(action.owned_by || "daise")} | proposed ${escapeHtml(action.proposed_by || "operator")}</div>
            </div>
          `)
          .join("")
      : `<div class="action-card"><strong>No operator actions yet.</strong></div>`;
  }

  function renderNotifications(payload) {
    const notifications = payload.notifications || [];
    el.notificationsPanel.innerHTML = notifications.length
      ? notifications
          .map((item) => `
            <div class="notification-card">
              <div class="message-head">
                <strong>${escapeHtml(item.title || "Notification")}</strong>
                <span>${escapeHtml(item.level || "info")}</span>
              </div>
              <div>${escapeHtml(item.message || "")}</div>
            </div>
          `)
          .join("")
      : `<div class="notification-card"><strong>Inbox clear.</strong><div class="micro-copy">Notifications for TinyProgrammer, requests, voice, and backend issues will appear here.</div></div>`;
  }

  function renderOperatorSummary(summary) {
    state.operatorSummary = summary || state.operatorSummary;
    const payload = state.operatorSummary || {};
    const system = payload.system || {};
    const backend = system.backend || {};
    const health = system.health || {};
    const tiny = payload.tinyprogrammer || {};
    const overall = health.overall || health.status || tiny.state || "idle";
    if (el.statusChip) {
      el.statusChip.textContent = `D.A.I.S.E ${overall}`;
    }
    if (el.backendChip && !state.requestId) {
      const backendLabel = [backend.provider || "provider", backend.model || "model"].filter(Boolean).join(" ");
      const tinyLabel = tiny.state ? ` | Tiny ${tiny.state}` : "";
      el.backendChip.textContent = `${backendLabel || "backend unknown"}${tinyLabel}`;
    }
  }

  function renderGpsStatus(payload) {
    state.gpsStatus = payload || state.gpsStatus;
    const gps = state.gpsStatus || {};
    const latestFix = gps.latest_fix || {};
    const activeRoute = gps.active_route || {};
    const cards = [
      {
        title: "Provider",
        value: String(gps.provider_type || "offline").replace(/_/g, " "),
        meta: `${gps.status || "unknown"} | ${gps.recording ? "recording" : "standby"}`,
      },
      {
        title: "Current Position",
        value: latestFix.latitude != null && latestFix.longitude != null
          ? `${Number(latestFix.latitude).toFixed(4)}, ${Number(latestFix.longitude).toFixed(4)}`
          : "No fix yet",
        meta: latestFix.timestamp || gps.last_update_at || "waiting",
      },
      {
        title: "Accuracy",
        value: latestFix.accuracy_m != null ? `${Number(latestFix.accuracy_m).toFixed(1)} m` : "unknown",
        meta: latestFix.speed_kmh != null ? `${Number(latestFix.speed_kmh).toFixed(1)} km/h` : "speed unavailable",
      },
      {
        title: "Route Session",
        value: activeRoute.route_session_id ? `points ${activeRoute.point_count || 0}` : "No active route",
        meta: activeRoute.route_session_id
          ? `${Number(activeRoute.distance_km || 0).toFixed(2)} km | ${activeRoute.started_at || "started"}`
          : gps.offline_state || "offline",
      },
    ];
    if (el.gpsStatusPanel) {
      el.gpsStatusPanel.innerHTML = cards
        .map((card) => `
          <div class="metric-card">
            <span>${escapeHtml(card.title)}</span>
            <strong>${escapeHtml(card.value)}</strong>
            <div class="micro-copy">${escapeHtml(card.meta)}</div>
          </div>
        `)
        .join("");
    }
    drawGpsRoute(gps);
  }

  function drawGpsRoute(gps) {
    if (!el.gpsRouteView) {
      return;
    }
    const svg = el.gpsRouteView;
    const width = 760;
    const height = 360;
    const padding = 36;
    const trail = Array.isArray(gps.trail) ? gps.trail : [];
    const latestFix = gps.latest_fix || {};
    const points = trail.length ? trail : (latestFix.latitude != null && latestFix.longitude != null ? [latestFix] : []);
    svg.innerHTML = `
      <rect x="0" y="0" width="${width}" height="${height}" rx="20" class="gps-map-bg"></rect>
      <text x="24" y="30" class="gps-map-title">OBEOS route view</text>
      <text x="24" y="52" class="gps-map-subtitle">${escapeHtml(String(gps.offline_state || gps.status || "offline"))}</text>
    `;
    if (!points.length) {
      svg.innerHTML += `<text x="${width / 2}" y="${height / 2}" text-anchor="middle" class="gps-map-empty">No GPS fix available yet. Attach GNSS or switch to simulator.</text>`;
      return;
    }
    const latitudes = points.map((point) => Number(point.latitude || 0));
    const longitudes = points.map((point) => Number(point.longitude || 0));
    const minLat = Math.min(...latitudes);
    const maxLat = Math.max(...latitudes);
    const minLon = Math.min(...longitudes);
    const maxLon = Math.max(...longitudes);
    const latSpan = Math.max(0.0008, maxLat - minLat);
    const lonSpan = Math.max(0.0008, maxLon - minLon);
    const projected = points.map((point) => {
      const x = padding + ((Number(point.longitude || 0) - minLon) / lonSpan) * (width - padding * 2);
      const y = height - padding - ((Number(point.latitude || 0) - minLat) / latSpan) * (height - padding * 2);
      return { x, y, point };
    });
    const polylinePoints = projected.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
    svg.innerHTML += `<polyline class="gps-route-line" points="${polylinePoints}"></polyline>`;
    projected.forEach((entry, index) => {
      const isLatest = index === projected.length - 1;
      svg.innerHTML += `<circle class="${isLatest ? "gps-route-point latest" : "gps-route-point"}" cx="${entry.x.toFixed(1)}" cy="${entry.y.toFixed(1)}" r="${isLatest ? 6 : 3.2}"></circle>`;
    });
    const current = projected[projected.length - 1];
    svg.innerHTML += `
      <text x="${Math.min(width - 170, current.x + 10).toFixed(1)}" y="${Math.max(68, current.y - 12).toFixed(1)}" class="gps-map-current">
        current fix | ${Number(current.point.latitude || 0).toFixed(4)}, ${Number(current.point.longitude || 0).toFixed(4)}
      </text>
    `;
  }

  function renderVerification(payload) {
    state.verification = payload || state.verification;
    const report = state.verification || {};
    const checks = report.checks || report.results?.surfaces?.checks || {};
    const blockers = report.blockers || [];
    const nextActions = report.recommended_next_actions || [];
    const rows = Object.entries(checks)
      .map(
        ([key, value]) => `
          <div class="verification-card">
            <strong>${escapeHtml(key.replace(/_/g, " "))}</strong>
            <div class="security-meta">
              <span class="security-pill">${escapeHtml(value.status || "unknown")}</span>
            </div>
            ${prettyJson(value)}
          </div>
        `,
      )
      .join("");
    el.verificationPanel.innerHTML = `
      <div class="verification-card">
        <div class="section-kicker">Verification Mode</div>
        <strong>${escapeHtml(report.mode || "surfaces")}</strong>
        <div class="micro-copy">${escapeHtml(report.status || "unknown")}</div>
        ${report.summary ? `<div class="micro-copy">${escapeHtml(report.summary)}</div>` : ""}
      </div>
      ${rows || `<div class="verification-card"><strong>No structured checks yet.</strong></div>`}
      ${
        blockers.length
          ? `<div class="verification-card"><div class="section-kicker">Blockers</div>${blockers
              .map((item) => `<div>${escapeHtml(item)}</div>`)
              .join("")}</div>`
          : ""
      }
      ${
        nextActions.length
          ? `<div class="verification-card"><div class="section-kicker">Next Actions</div>${nextActions
              .map((item) => `<div>${escapeHtml(item)}</div>`)
              .join("")}</div>`
          : ""
      }
    `;
  }

  function renderIntegrations(payload) {
    state.integrations = payload || state.integrations;
    const integrations = state.integrations?.integrations || [];
    el.integrationPanel.innerHTML = integrations.length
      ? integrations
          .map((item) => {
            const classes = [
              "integration-card",
              item.available ? "available" : "",
              item.visual_reference_only ? "reference-only" : "",
            ]
              .filter(Boolean)
              .join(" ");
            const launch = item.launch_contract;
            return `
              <div class="${classes}">
                <div class="section-kicker">${escapeHtml(item.category || "integration")}</div>
                <strong>${escapeHtml(item.label || item.integration_id)}</strong>
                <div class="micro-copy">${escapeHtml(item.summary || "")}</div>
                <div class="security-meta">
                  <span class="integration-pill">${escapeHtml(item.available ? "available" : "missing")}</span>
                  <span class="integration-pill">${escapeHtml(item.role || "external")}</span>
                </div>
                ${
                  launch
                    ? `<div class="micro-copy">${escapeHtml((launch.command_line || []).join(" "))}</div>`
                    : `<div class="micro-copy">${escapeHtml((item.obeos_policy || {}).identity_rule || "Optional external integration.")}</div>`
                }
              </div>
            `;
          })
          .join("")
      : `<div class="integration-card"><strong>No tacklebox projects detected.</strong><div class="micro-copy">Optional integrations will appear here when discovered under the tacklebox root.</div></div>`;
  }

  function renderRedAmon(payload) {
    state.redamonStatus = payload || state.redamonStatus;
    const mode = state.redamonStatus || {};
    const integration = mode.integration || {};
    const active = mode.active_mode;
    const history = mode.history || [];
    el.redamonPanel.innerHTML = `
      <div class="security-mode-card ${active ? "active" : "warning"}">
        <div class="section-kicker">Mode Banner</div>
        <strong>${escapeHtml(mode.banner?.title || "RedAmon mode")}</strong>
        <div class="micro-copy">${escapeHtml(mode.banner?.summary || "")}</div>
        <div class="security-meta">
          <span class="security-pill">${escapeHtml(integration.available ? "available" : "missing")}</span>
          <span class="security-pill">consent level ${escapeHtml(mode.consent_rule || "n/a")}</span>
          <span class="security-pill">${escapeHtml(active ? "active review" : "idle review")}</span>
        </div>
      </div>
      <div class="security-mode-card">
        <div class="section-kicker">Current Action</div>
        ${mode.current_action ? prettyJson(mode.current_action) : `<div class="micro-copy">No active reviewed launch action.</div>`}
      </div>
      <div class="security-mode-card">
        <div class="section-kicker">Execution History</div>
        ${
          history.length
            ? history
                .map(
                  (item) => `<div class="detail-row"><strong>${escapeHtml(item.action || "action")}</strong><span>${escapeHtml(item.status || "unknown")}</span></div>`,
                )
                .join("")
            : `<div class="micro-copy">No RedAmon workflow history yet.</div>`
        }
      </div>
    `;
  }

  function renderCommandPalette(commands) {
    state.commands = Array.isArray(commands) ? commands : [];
    const query = state.commandQuery.trim().toLowerCase();
    const preferred = [
      "/status",
      "/summary",
      "/trace",
      "/whatchanged",
      "/cognition",
      "/cognition forge",
      "/awareness",
      "/diagnose",
      "/tiny status",
      "/tiny recent",
      "/tiny log",
      "/files policy",
      "/files roots",
      "/docker status",
      "/desktop workspace",
      "/obeos vm-acceptance-snapshot",
    ];
    const source = query
      ? state.commands.filter((entry) => {
          const haystack = `${entry.command || ""} ${entry.description || ""} ${entry.category || ""}`.toLowerCase();
          return haystack.includes(query);
        })
      : preferred.map((commandName) => state.commands.find((entry) => entry.command === commandName)).filter(Boolean);
    const items = source.slice(0, 32);
    el.commandPalette.innerHTML = items
      .map(
        (command) => `
          <button class="button button-subtle command-button" data-command="${escapeHtml(command.command)}" title="${escapeHtml(command.description || "")}">
            <span>${escapeHtml(command.command)}</span>
            <small>${escapeHtml(command.category || "Command")}</small>
          </button>
        `,
      )
      .join("") || `<div class="command-card"><strong>No commands found.</strong><div class="mini">Try /status, files, tiny, or cognition.</div></div>`;
  }

  function renderFileScopes(summary, roots, detail) {
    state.operatorSummary = summary || state.operatorSummary;
    state.fileScopeRoots = roots || state.fileScopeRoots;
    state.fileScopeDetail = detail || state.fileScopeDetail;
    const fileSummary = (state.operatorSummary || {}).files || {};
    const rootsPayload = state.fileScopeRoots || {};
    const discovered = rootsPayload.discovered_roots || [];
    const approved = discovered.filter((item) => item.state === "approved");
    const governed = discovered.filter((item) => item.state === "governed");
    const candidates = discovered.filter((item) => item.state === "candidate").slice(0, 6);
    const detailPayload = state.fileScopeDetail;
    const detailBlock = detailPayload
      ? `<div class="scope-root-card">
          <strong>${escapeHtml(detailPayload.action || "detail")}</strong>
          <div class="micro-copy">${escapeHtml(detailPayload.path || detailPayload.root || detailPayload.human_reason || "latest result")}</div>
          <div class="scope-detail">${escapeHtml(summarize(detailPayload.content || detailPayload.entry || detailPayload, 1200))}</div>
        </div>`
      : "";
    const rootsOverview = `
      <div class="scope-root-card">
        <strong>Read + Map Rule</strong>
        <div class="micro-copy">${escapeHtml((rootsPayload.policy || {}).mode || "read_map_first")}</div>
        <div class="micro-copy">${escapeHtml((rootsPayload.policy || {}).write_boundary || "governed_project_roots_only")}</div>
      </div>
      <div class="scope-root-card">
        <strong>Counts</strong>
        <div class="micro-copy">${escapeHtml(String((fileSummary.governed_roots || []).length || governed.length))} governed</div>
        <div class="micro-copy">${escapeHtml(String((fileSummary.approved_roots || []).length || approved.length))} approved</div>
        <div class="micro-copy">${escapeHtml(String((rootsPayload.counts || {}).candidates || candidates.length))} candidate</div>
      </div>
    `;
    if (el.scopeSummaryPanel) {
      el.scopeSummaryPanel.innerHTML = `
        ${rootsOverview}
        ${approved.slice(0, 3).map((item) => `
          <div class="scope-root-card">
            <strong>${escapeHtml(item.name || item.path || "approved")}</strong>
            <div class="micro-copy">${escapeHtml(item.state || "approved")}</div>
            <div>${escapeHtml(item.path || "")}</div>
          </div>
        `).join("")}
        ${candidates.length ? `
          <div class="scope-root-card">
            <strong>Candidate Roots</strong>
            ${candidates.map((item) => `<div class="micro-copy">${escapeHtml(item.name || item.path || "candidate")}</div>`).join("")}
          </div>
        ` : ""}
      `;
    }
    el.scopePanel.innerHTML = `
      <div class="scope-summary">
        <strong>Approved Roots</strong>
        <div class="micro-copy">${escapeHtml(String((fileSummary.approved_roots || []).length || approved.length))} approved | ${escapeHtml(String((fileSummary.governed_roots || []).length || governed.length))} governed</div>
        <div class="micro-copy">${escapeHtml((rootsPayload.policy || {}).write_boundary || "governed_project_roots_only")}</div>
      </div>
      ${governed.slice(0, 3).map((item) => `
        <div class="scope-root-card">
          <strong>${escapeHtml(item.name || item.path || "governed")}</strong>
          <div class="micro-copy">governed</div>
          <div>${escapeHtml(item.path || "")}</div>
        </div>
      `).join("")}
      ${approved.slice(0, 4).map((item) => `
        <div class="scope-root-card">
          <strong>${escapeHtml(item.name || item.path || "approved")}</strong>
          <div class="micro-copy">${escapeHtml(item.human_reason || "approved read-map scope")}</div>
          <div>${escapeHtml(item.path || "")}</div>
        </div>
      `).join("")}
      ${candidates.length ? `
        <div class="scope-root-card">
          <strong>Candidate Roots</strong>
          ${candidates.map((item) => `<div class="micro-copy">${escapeHtml(item.name || item.path || "candidate")} | ${escapeHtml(item.path || "")}</div>`).join("")}
        </div>
      ` : ""}
      ${detailBlock}
    `;
    if (el.scopeDetailPanel) {
      const entryChildren = detailPayload && detailPayload.entry && Array.isArray(detailPayload.entry.children) ? detailPayload.entry.children : [];
      const contentPreview = detailPayload && typeof detailPayload.content === "string"
        ? `<div class="tiny-preview">${escapeHtml(detailPayload.content)}</div>`
        : detailPayload && detailPayload.entry
          ? `
            <div class="detail-list">
              ${entryChildren.length ? entryChildren.slice(0, 10).map((child) => `
                <div class="detail-row">
                  <strong>${escapeHtml(child.name || child.path || child.kind || "entry")}</strong>
                  <span>${escapeHtml([child.kind || "", child.state || ""].filter(Boolean).join(" | ") || child.path || "")}</span>
                </div>
              `).join("") : `<div class="micro-copy">No nested entries were returned for this result.</div>`}
            </div>
          `
          : `<p>Approve, map, or read a path to pin file detail here.</p>`;
      el.scopeDetailPanel.innerHTML = `
        <div class="section-kicker">Files</div>
        <h3>Latest Scope Result</h3>
        <div class="micro-copy">${escapeHtml(detailPayload?.action || "detail")} ${detailPayload?.path ? `| ${detailPayload.path}` : ""}</div>
        ${detailPayload?.human_reason ? `<p>${escapeHtml(detailPayload.human_reason)}</p>` : ""}
        ${contentPreview}
      `;
    }
  }

  function renderTiny(status, recent, bbs, logTail) {
    state.tinyLogTail = logTail || state.tinyLogTail;
    const reasonPills = [
      status.stream_reason ? `<span class="reason-pill">stream ${escapeHtml(status.stream_reason)}</span>` : "",
      status.bbs_reason ? `<span class="reason-pill">bbs ${escapeHtml(status.bbs_reason)}</span>` : "",
      status.schedule_reason ? `<span class="reason-pill">schedule ${escapeHtml(status.schedule_reason)}</span>` : "",
      status.live_control_reason ? `<span class="reason-pill">live ${escapeHtml(status.live_control_reason)}</span>` : "",
      status.native_window_state ? `<span class="reason-pill">native ${escapeHtml(status.native_window_state)}</span>` : "",
      status.resource_state ? `<span class="reason-pill">resources ${escapeHtml(status.resource_state)}</span>` : "",
    ].join("");
    const recentPrograms = (recent.programs || []).slice(0, 3);
    const bbsPosts = (bbs.posts || []).slice(0, 3);
    const process = status.process || {};
    const logCard = logTail && (logTail.content || logTail.human_reason)
      ? `
        <div class="tiny-card">
          <strong>Managed Log Tail</strong>
          <div class="micro-copy">${escapeHtml(logTail.log_path || status.log_path || "")}</div>
          <div class="tiny-preview">${escapeHtml(logTail.content || logTail.human_reason || "")}</div>
        </div>
      `
      : "";
    const livePanel = status.stream_available
      ? `
        <div class="tiny-card">
          <strong>Embedded Live View</strong>
          <div class="tiny-live-frame">
            <img src="${escapeHtml(`${status.stream_url || "/stream"}?t=${Date.now()}`)}" alt="TinyProgrammer live stream">
          </div>
        </div>
      `
      : `
        <div class="tiny-card">
          <strong>Embedded Live View</strong>
          <div class="tiny-live-frame tiny-live-empty">
            <div>
              <div>${escapeHtml(status.stream_human_reason || "Live stream unavailable.")}</div>
              <div class="micro-copy">Use Launch to ensure TinyProgrammer starts with stream enabled.</div>
            </div>
          </div>
        </div>
      `;
    el.tinyPanel.innerHTML = `
      <div class="tiny-card">
        <strong>${escapeHtml(status.online ? "Online" : status.state || "Offline")}</strong>
        <div class="micro-copy">${escapeHtml(status.current_program_type || "no active program")} | mood ${escapeHtml(status.mood || "unknown")} | ownership ${escapeHtml(process.ownership || "offline")}</div>
        <div class="micro-copy">Mode ${escapeHtml(process.management_state || status.mode || "offline")} | PID ${escapeHtml(process.pid || "none")} | CPU ${escapeHtml(status.cpu_percent || 0)} | RAM ${escapeHtml(status.memory_mb || 0)} MB</div>
        <div class="micro-copy">Native ${escapeHtml(status.native_window_human_reason || "unknown")} </div>
        <div class="micro-copy">Autostart ${escapeHtml(status.autostart_enabled)} | Shutdown ${escapeHtml(status.shutdown_policy || "ask")} | Stream required ${escapeHtml(status.stream_required)}</div>
        <div class="button-row tight">${reasonPills}</div>
      </div>
      ${livePanel}
      ${recentPrograms.length ? recentPrograms.map((program) => `
        <div class="tiny-card">
          <strong>${escapeHtml(program.filename || program.id || "creation")}</strong>
          <div class="micro-copy">${escapeHtml(program.program_type || "program")} | ${escapeHtml(program.mood || "steady")}</div>
          <div>${escapeHtml(summarize(program.thought_process || program.summary || "", 140))}</div>
          ${program.code_preview ? `<div class="tiny-preview">${escapeHtml(program.code_preview)}</div>` : ""}
        </div>
      `).join("") : `<div class="tiny-card"><strong>No recent creations.</strong></div>`}
      ${bbsPosts.length ? `
        <div class="tiny-card">
          <strong>BBS Activity</strong>
          ${bbsPosts.map((post) => `<div class="micro-copy">${escapeHtml(post.title || post.author || "post")} | ${escapeHtml(summarize(post.text || post.content || "", 90))}</div>`).join("")}
        </div>
      ` : `<div class="tiny-card"><strong>BBS</strong><div class="micro-copy">${escapeHtml(bbs.bbs_reason || "No recent BBS activity.")}</div></div>`}
      ${logCard}
    `;
    if (el.tinyLogPanel) {
      el.tinyLogPanel.innerHTML = `
        <div class="section-kicker">Tiny Log</div>
        <h3>Managed Companion Log</h3>
        <div class="detail-list">
          <div class="detail-row"><strong>Ownership</strong><span>${escapeHtml(process.ownership || status.ownership || "offline")}</span></div>
          <div class="detail-row"><strong>Mode</strong><span>${escapeHtml(process.management_state || status.management_state || status.state || "offline")}</span></div>
          <div class="detail-row"><strong>Native Window</strong><span>${escapeHtml(status.native_window_human_reason || "unknown")}</span></div>
          <div class="detail-row"><strong>Live Stream</strong><span>${escapeHtml(status.stream_human_reason || "unknown")}</span></div>
        </div>
        <div class="micro-copy">${escapeHtml((logTail && (logTail.log_path || logTail.human_reason)) || status.log_path || "No managed log path yet.")}</div>
        <div class="tiny-preview">${escapeHtml((logTail && (logTail.content || logTail.human_reason)) || "Launch TinyProgrammer or request the log to populate this panel.")}</div>
      `;
    }
  }

  function renderThoughtglassCards(snapshot) {
    const overlays = snapshot.overlays || {};
    const clusters = {};
    (snapshot.nodes || []).forEach((node) => {
      const zone = node.zone || "other";
      clusters[zone] = (clusters[zone] || 0) + 1;
    });
    const cards = [
      {
        title: "Backend",
        body: `${overlays.backend?.provider || "unknown"} / ${overlays.backend?.model || "unknown"}`,
        meta: overlays.backend?.status || "unknown",
      },
      {
        title: "Queue",
        body: `${overlays.queue?.pending_requests || 0} pending`,
        meta: overlays.queue?.backlog_health || "unknown",
      },
      {
        title: "Memory",
        body: `${overlays.memory?.session_messages || 0} session hits`,
        meta: `${overlays.memory?.pinned_memories || 0} pinned`,
      },
      {
        title: "Companion",
        body: `${overlays.tinyprogrammer?.state || "offline"}`,
        meta: overlays.tinyprogrammer?.mood || "steady",
      },
      {
        title: "Confidence",
        body: Number(overlays.confidence || 0).toFixed(2),
        meta: `${snapshot.faults?.length || 0} faults`,
      },
      {
        title: "Clusters",
        body: Object.entries(clusters)
          .slice(0, 3)
          .map(([zone, count]) => `${zone}:${count}`)
          .join(" | ") || "no zones",
        meta: `${snapshot.nodes?.length || 0} total nodes`,
      },
      {
        title: "View State",
        body: snapshot.summaries?.focus_label || snapshot.focus?.selected_node || "wide field",
        meta: `zoom ${Number(snapshot.focus?.zoom || 1).toFixed(2)} | orbit ${Number(snapshot.focus?.orbit || 0).toFixed(2)}`,
      },
    ];
    el.thoughtglassCards.innerHTML = cards
      .map((card) => `
        <div class="overlay-card">
          <div class="section-kicker">${escapeHtml(card.title)}</div>
          <strong>${escapeHtml(card.body)}</strong>
          <div class="micro-copy">${escapeHtml(card.meta)}</div>
        </div>
      `)
      .join("");
  }

  function renderThoughtglassDetails(node) {
    if (!node) {
      el.thoughtglassDetails.innerHTML = `
        <div class="section-kicker">Inspection</div>
        <h3>Nothing selected</h3>
        <p>Click a node or switch layers to inspect the live system state.</p>
        <div class="detail-list">
          <div class="detail-row"><strong>Mouse</strong><span>Click to select, drag background to pan, wheel to zoom.</span></div>
          <div class="detail-row"><strong>Keyboard</strong><span>Use 1-6 to switch layers and Enter to inspect the selected node.</span></div>
        </div>
      `;
      return;
    }
    const snapshot = state.cognition || {};
    const metrics = metricEntries(node.metrics).slice(0, 8);
    const connections = collectNodeConnections(snapshot, node.id).slice(0, 6);
    const provenance = node.provenance || {};
    const freshness = provenance.observed_at ? `${Math.max(0, Math.round(Date.now() / 1000 - Number(provenance.observed_at)))}s old` : "live";
    el.thoughtglassDetails.innerHTML = `
      <div class="section-kicker">${escapeHtml(node.kind || "node")} | ${escapeHtml(node.zone || "zone")}</div>
      <h3>${escapeHtml(node.label || node.id)}</h3>
      <div class="detail-tags">
        <span class="detail-pill">${escapeHtml(formatStateLabel(node.state || "idle"))}</span>
        <span class="detail-pill">emphasis ${Number(node.emphasis || 0).toFixed(2)}</span>
        <span class="detail-pill">${escapeHtml(freshness)}</span>
      </div>
      <div class="detail-grid">
        <div class="detail-block">
          <div class="section-kicker">Signals</div>
          ${metrics.length ? metrics.map(([key, value]) => `
            <div class="detail-row"><strong>${escapeHtml(String(key).replace(/_/g, " "))}</strong><span>${escapeHtml(String(value))}</span></div>
          `).join("") : `<div class="micro-copy">No live metrics exposed for this node.</div>`}
        </div>
        <div class="detail-block">
          <div class="section-kicker">Connections</div>
          ${connections.length ? connections.map((connection) => `
            <div class="detail-row">
              <strong>${escapeHtml(connection.label)}</strong>
              <span>${escapeHtml(connection.peer)} | ${escapeHtml(formatStateLabel(connection.state))} | ${connection.confidence.toFixed(2)}</span>
            </div>
          `).join("") : `<div class="micro-copy">No connected live routes on the current layer.</div>`}
        </div>
      </div>
      <div class="detail-block">
        <div class="section-kicker">Provenance</div>
        <div class="detail-row"><strong>source</strong><span>${escapeHtml(provenance.source || "thoughtglass_projection")}</span></div>
        <div class="detail-row"><strong>method</strong><span>${escapeHtml(provenance.collection_method || "telemetry_projection")}</span></div>
      </div>
    `;
  }

  function getZoneLayout(nodes, snapshot) {
    const centerX = 450 + Number(snapshot.focus?.pan_x || 0) * 80;
    const centerY = 310 + Number(snapshot.focus?.pan_y || 0) * 60;
    const zoom = Number(snapshot.focus?.zoom || 1);
    const orbit = Number(snapshot.focus?.orbit || 0);
    const buckets = {};
    nodes.forEach((node) => {
      buckets[node.zone] = buckets[node.zone] || [];
      buckets[node.zone].push(node);
    });

    function placeArc(zone, radius, start, end) {
      const items = buckets[zone] || [];
      return items.map((node, index) => {
        const angle = items.length === 1 ? (start + end) / 2 : start + ((end - start) * index) / Math.max(items.length - 1, 1);
        return [node.id, {
          x: centerX + Math.cos(angle + orbit) * radius * zoom,
          y: centerY + Math.sin(angle + orbit) * radius * zoom,
        }];
      });
    }

    const positions = Object.fromEntries([
      ["blackfong_core", { x: centerX, y: centerY }],
      ["daise_interface", { x: centerX, y: centerY - 70 * zoom }],
      ...placeArc("memory", 170, Math.PI * 0.75, Math.PI * 1.3),
      ...placeArc("agent", 190, -Math.PI * 0.3, Math.PI * 0.35),
      ...placeArc("sensor", 210, Math.PI * 0.55, Math.PI * 1.05),
      ...placeArc("tool", 240, -Math.PI * 0.05, Math.PI * 0.55),
      ...placeArc("forge", 250, Math.PI * 0.2, Math.PI * 0.6),
      ...placeArc("task", 155, Math.PI * 1.35, Math.PI * 1.8),
      ...placeArc("queue", 145, Math.PI * 1.45, Math.PI * 1.65),
    ]);
    return positions;
  }

  function renderGraph(snapshot) {
    state.cognition = snapshot;
    state.selectedNode = snapshot.focus?.selected_node || state.selectedNode;
    setLayer(snapshot.active_layer || "live_brain");
    state.graphPositions = getZoneLayout(snapshot.nodes || [], snapshot);
    const svg = el.thoughtglassGraph;
    svg.innerHTML = "";

    const edges = document.createElementNS("http://www.w3.org/2000/svg", "g");
    (snapshot.edges || []).forEach((edge) => {
      const source = state.graphPositions[edge.source];
      const target = state.graphPositions[edge.target];
      if (!source || !target) {
        return;
      }
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", String(source.x));
      line.setAttribute("y1", String(source.y));
      line.setAttribute("x2", String(target.x));
      line.setAttribute("y2", String(target.y));
      const connected = state.selectedNode && (edge.source === state.selectedNode || edge.target === state.selectedNode);
      line.setAttribute("class", `edge-line ${edge.state || "idle"} ${connected ? "connected" : ""}`);
      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = `${edge.label || "route"} | ${edge.source} -> ${edge.target}`;
      line.appendChild(title);
      edges.appendChild(line);
    });
    svg.appendChild(edges);

    const nodesLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
    (snapshot.nodes || []).forEach((node) => {
      const point = state.graphPositions[node.id];
      if (!point) {
        return;
      }
      const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      group.setAttribute("class", `node-${node.kind || "tool"} ${node.state || "idle"} ${state.selectedNode === node.id ? "node-selected" : ""}`);
      group.setAttribute("tabindex", "0");
      group.dataset.nodeId = node.id;

      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      const radius = Math.max(16, 14 + Number(node.emphasis || 0.2) * 16);
      circle.setAttribute("cx", String(point.x));
      circle.setAttribute("cy", String(point.y));
      circle.setAttribute("r", String(radius));
      group.appendChild(circle);

      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", String(point.x));
      label.setAttribute("y", String(point.y + radius + 18));
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("class", "node-label");
      label.textContent = node.label || node.id;
      group.appendChild(label);

      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = `${node.label || node.id} | ${formatStateLabel(node.state || "idle")}`;
      group.appendChild(title);

      group.addEventListener("click", () => selectNode(node));
      group.addEventListener("dblclick", () => openNodeModule(node));
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openNodeModule(node);
        }
      });
      nodesLayer.appendChild(group);
    });
    svg.appendChild(nodesLayer);

    const selectedNode = (snapshot.nodes || []).find((node) => node.id === state.selectedNode) || null;
    renderThoughtglassDetails(selectedNode);
    renderThoughtglassCards(snapshot);
    el.thoughtglassFocusBadge.textContent = snapshot.summaries?.focus_label || selectedNode?.label || "No focus";
    drawThoughtglassFx(snapshot);
  }

  function drawThoughtglassFx(snapshot) {
    const canvas = el.thoughtglassFx;
    const context = canvas.getContext("2d");
    if (!context) {
      return;
    }
    const width = canvas.width;
    const height = canvas.height;
    state.fxTick += 0.018;
    context.clearRect(0, 0, width, height);

    for (let i = 0; i < 46; i += 1) {
      const x = (Math.sin(i * 9.2 + state.fxTick) * 0.5 + 0.5) * width;
      const y = (Math.cos(i * 6.1 + state.fxTick * 0.8) * 0.5 + 0.5) * height;
      context.fillStyle = i % 5 === 0 ? "rgba(228, 194, 118, 0.12)" : "rgba(255, 255, 255, 0.06)";
      context.beginPath();
      context.arc(x, y, i % 7 === 0 ? 1.8 : 1.0, 0, Math.PI * 2);
      context.fill();
    }

    (snapshot.edges || []).forEach((edge, index) => {
      if (edge.state !== "active") {
        return;
      }
      const source = state.graphPositions[edge.source];
      const target = state.graphPositions[edge.target];
      if (!source || !target) {
        return;
      }
      const progress = (state.fxTick * 0.8 + index * 0.17) % 1;
      const x = source.x + (target.x - source.x) * progress;
      const y = source.y + (target.y - source.y) * progress;
      context.fillStyle = "rgba(228, 194, 118, 0.55)";
      context.beginPath();
      context.arc(x, y, 2.4, 0, Math.PI * 2);
      context.fill();
    });

    (snapshot.nodes || []).forEach((node, index) => {
      const point = state.graphPositions[node.id];
      if (!point) {
        return;
      }
      const isSelected = state.selectedNode === node.id;
      const isActive = node.state === "active" || node.state === "warning" || node.state === "fault";
      if (!isSelected && !isActive) {
        return;
      }
      const radius = 22 + Number(node.emphasis || 0.2) * 14 + Math.sin(state.fxTick * 2 + index) * 2;
      context.strokeStyle = isSelected
        ? "rgba(228, 194, 118, 0.44)"
        : node.kind === "sensor"
          ? "rgba(123, 208, 218, 0.24)"
          : node.state === "fault"
            ? "rgba(255, 128, 118, 0.34)"
            : "rgba(255, 255, 255, 0.14)";
      context.lineWidth = isSelected ? 2.4 : 1.2;
      context.beginPath();
      context.arc(point.x, point.y, radius, 0, Math.PI * 2);
      context.stroke();
    });
  }

  function renderCognition(snapshot) {
    const focus = snapshot.focus || {};
    if (snapshot.active_layer) {
      state.layer = snapshot.active_layer;
    } else if (focus.layer) {
      state.layer = focus.layer;
    }
    renderTraceRibbon(el.thoughtglassRibbon, snapshot.trace_ribbon || []);
    renderChatBrainPanel(snapshot);
    renderGraph(snapshot);
  }

  async function refreshKeyboardShortcuts() {
    if (!el.keyboardShortcutsPanel) {
      return;
    }
    let overrides = {};
    try {
      overrides = JSON.parse(window.localStorage.getItem(SHORTCUT_OVERRIDES_STORAGE_KEY) || "{}");
    } catch (error) {
      overrides = {};
    }
    const payload = await fetchJSON("/api/workspace/shortcuts");
    const shortcuts = (payload.shortcuts || []).map((item) => ({
      ...item,
      sequence: overrides[item.id] || item.sequence,
    }));
    el.keyboardShortcutsPanel.innerHTML = shortcuts.length
      ? `<div class="micro-copy">In-app shortcuts only. Windows owns Win+D, Alt+Tab, and similar system keys.</div>${shortcuts
          .map(
            (item) =>
              `<div class="memory-card"><strong>${escapeHtml(item.sequence)}</strong><div class="micro-copy">${escapeHtml(item.label)}</div></div>`
          )
          .join("")}`
      : `<div class="memory-card"><strong>No shortcuts exported.</strong></div>`;
  }

  function renderChatBrainPanel(snapshot) {
    if (!el.chatBrainPanel || !snapshot) {
      return;
    }
    const summaries = snapshot.summaries || {};
    const overlays = snapshot.overlays || {};
    const backend = overlays.backend || {};
    const queue = overlays.queue || {};
    const faults = snapshot.faults || [];
    el.chatBrainPanel.innerHTML = `
      <div>
        <div class="section-kicker">Thoughtglass</div>
        <strong>${escapeHtml(summaries.focus_label || "Live Brain")}</strong>
        <div class="micro-copy">${escapeHtml(snapshot.ascii_summary || "Cognition is online.")}</div>
      </div>
      <div class="brain-pill-row">
        <span class="status-chip">Layer ${escapeHtml(snapshot.active_layer || state.layer)}</span>
        <span class="status-chip">Backend ${escapeHtml(backend.provider || "unknown")}</span>
        <span class="status-chip">Queue ${escapeHtml(queue.pending_requests || 0)}</span>
        <span class="status-chip ${faults.length ? "danger-chip" : ""}">Faults ${faults.length}</span>
      </div>
    `;
  }

  function scheduleLiveRetry() {
    if (state.liveRetryHandle) {
      return;
    }
    state.liveRetryHandle = window.setTimeout(() => {
      state.liveRetryHandle = null;
      connectLiveStream();
    }, 2500);
  }

  function handleLiveEvent(eventType, payload) {
    if (eventType === "transport") {
      setTransportState(payload.mode || "sse", payload.status || "connected");
      return;
    }
    if (eventType === "heartbeat") {
      return;
    }
    if (eventType === "metrics_update") {
      renderMetrics(payload);
      return;
    }
    if (eventType === "cognition_snapshot") {
      const snapshotLayer = payload.active_layer || "live_brain";
      if (!state.cognition || snapshotLayer === state.layer) {
        renderCognition(payload);
      }
      return;
    }
    if (eventType === "cognition_delta" || eventType === "cognition_focus" || eventType === "cognition_fault") {
      refreshCognition().catch(() => {});
      return;
    }
    if (eventType === "chat_token") {
      if (payload.request_id) {
        state.requestId = payload.request_id;
      }
      if (payload.session_id) {
        state.sessionId = payload.session_id;
      }
      updateConversationStatus("streaming", "streaming");
      state.streamingReply = {
        text: payload.text || `${(state.streamingReply && state.streamingReply.text) || ""}${payload.token || ""}`,
        timestamp: Date.now() / 1000,
        complete: false,
      };
      renderMessages({ messages: state.lastRenderedMessages || [] });
      return;
    }
    if (eventType === "chat_phase") {
      state.requestId = payload.request_id || state.requestId;
      state.sessionId = payload.session_id || state.sessionId;
      const phase = String(payload.phase || "working");
      const phaseLabel = payload.phase_message || phase;
      const status = phase === "accepted" ? "pending" : phase === "complete" ? "finalizing" : phase === "error" ? "error" : "active";
      updateConversationStatus(phaseLabel, status);
      if (payload.fast_path) {
        el.pendingDetail.textContent = "Fast path: single-agent quick route.";
      }
      if (phase === "complete" || phase === "error") {
        state.streamingReply = state.streamingReply ? { ...state.streamingReply, complete: true } : null;
      }
      refreshTrace().catch(() => {});
      refreshOperatorSummary().catch(() => {});
      return;
    }
    if (eventType === "agent_report" || eventType === "agent_selection" || eventType === "chat_complete" || eventType === "chat_error") {
      if (payload.request_id) {
        state.requestId = payload.request_id;
      }
      if (payload.session_id) {
        state.sessionId = payload.session_id;
      }
      refreshTrace().catch(() => {});
      refreshActions().catch(() => {});
      refreshNotifications().catch(() => {});
      refreshOperatorSummary().catch(() => {});
      if (eventType === "chat_complete") {
        const hasError = payload.status === "error" || payload.error || (payload.result && payload.result.error);
        if (hasError) {
          const label = payload.error || (payload.result && payload.result.error) || "failed";
          updateConversationStatus(`failed: ${label}`, "error");
        } else {
          updateConversationStatus("completed", "idle");
        }
        state.streamingReply = state.streamingReply ? { ...state.streamingReply, complete: true } : null;
        refreshMessages().catch(() => {});
        refreshMemory().catch(() => {});
        refreshCognition().catch(() => {});
      }
      if (eventType === "chat_error") {
        const label = payload.error ? `failed: ${payload.error}` : "failed";
        updateConversationStatus(label, "error");
        state.streamingReply = null;
      }
      return;
    }
    if (eventType === "operator_action") {
      refreshActions().catch(() => {});
      refreshNotifications().catch(() => {});
      refreshOperatorSummary().catch(() => {});
      refreshFileScopes().catch(() => {});
      refreshTiny().catch(() => {});
      refreshIntegrations().catch(() => {});
      refreshRedamonStatus().catch(() => {});
      if (state.workspaceTab === "security") {
        refreshVerification("surfaces").catch(() => {});
      }
    }
  }

  function connectLiveStream() {
    if (!window.EventSource) {
      setTransportState("polling", "fallback");
      return;
    }
    if (state.liveSource) {
      state.liveSource.close();
      state.liveSource = null;
    }
    const source = new EventSource("/api/live/stream");
    state.liveSource = source;
    setTransportState("sse", "connecting");

    source.onopen = () => {
      state.liveConnected = true;
      setTransportState("sse", "connected");
    };

    ["transport", "heartbeat", "metrics_update", "cognition_snapshot", "cognition_delta", "cognition_focus", "cognition_fault", "chat_phase", "chat_token", "agent_report", "agent_selection", "chat_complete", "chat_error", "operator_action"].forEach((eventType) => {
      source.addEventListener(eventType, (event) => {
        try {
          handleLiveEvent(eventType, JSON.parse(event.data || "{}"));
        } catch (error) {
          console.error("live event parse error", eventType, error);
        }
      });
    });

    source.onerror = () => {
      state.liveConnected = false;
      setTransportState("polling", "fallback");
      if (state.liveSource) {
        state.liveSource.close();
        state.liveSource = null;
      }
      scheduleLiveRetry();
    };
  }

  async function requestCognitionGesture(payload) {
    const result = await fetchJSON("/api/cognition/gesture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.sessionId,
        request_id: state.requestId,
        ...payload,
      }),
    });
    if (result.snapshot) {
      renderCognition(result.snapshot);
    }
    return result;
  }

  async function selectNode(node) {
    state.selectedNode = node.id;
    setWorkspaceTab("thoughtglass");
    setDrawerTab("detail");
    try {
      await requestCognitionGesture({
        action: "select",
        mode: "thoughtglass",
        target: { node_id: node.id },
      });
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function openNodeModule(node) {
    try {
      setWorkspaceTab("thoughtglass");
      setDrawerTab("detail");
      await requestCognitionGesture({
        action: "open_module",
        mode: "thoughtglass",
        target: { node_id: node.id, module: node.id },
      });
      showToast(`${node.label || node.id} inspected.`, "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function refreshMetrics() {
    const metrics = await fetchJSON("/api/metrics");
    renderMetrics(metrics);
    el.statusChip.textContent = `D.A.I.S.E ${metrics.system_health || "idle"}`;
  }

  async function refreshSessions() {
    const payload = await fetchJSON("/api/sessions");
    renderSessions(payload.sessions || []);
  }

  async function refreshMemory() {
    const query = state.sessionId ? `?session_id=${encodeURIComponent(state.sessionId)}` : "";
    const payload = await fetchJSON(`/api/memory${query}`);
    renderMemory(payload);
  }

  async function refreshMessages() {
    if (!state.sessionId) {
      renderMessages({ messages: [] });
      return;
    }
    const payload = await fetchJSON(`/api/session/${encodeURIComponent(state.sessionId)}/messages`);
    state.localMessages = [];
    renderMessages(payload);
  }

  async function refreshTrace() {
    if (!state.requestId) {
      renderTrace({ phase: "idle", status: "idle", events: [], reports: [] });
      return;
    }
    const trace = await fetchJSON(`/api/request/${encodeURIComponent(state.requestId)}/trace`);
    renderTrace(trace);
    const selected = trace.selected_agent || (trace.selection || {}).selected_agent;
    el.backendChip.textContent = `${trace.provider || "provider"} ${trace.model || "model"}${selected ? ` | ${displayAgentName(selected)}` : ""}`;
    if (trace.status === "complete" || trace.status === "error") {
      stopTracePolling();
      await Promise.all([
        refreshMessages(),
        refreshMemory(),
        refreshActions(),
        refreshNotifications(),
        refreshOperatorSummary(),
        refreshGpsStatus(),
        refreshFileScopes(),
        refreshCognition(),
        refreshTiny(),
        refreshIntegrations(),
        refreshRedamonStatus(),
      ]);
      if (trace.status === "error") {
        showToast(trace.phase_message || "Request failed.", "error");
      }
    }
  }

  async function refreshActions() {
    const query = state.sessionId ? `?session_id=${encodeURIComponent(state.sessionId)}` : "";
    const payload = await fetchOptionalJSON(`/api/operator/actions${query}`, {
      actions: [],
      pending_actions: 0,
      human_reason: "Operator action queue is temporarily unavailable.",
    });
    renderActions(payload);
  }

  async function refreshNotifications() {
    const payload = await fetchJSON("/api/notifications");
    renderNotifications(payload);
  }

  async function refreshOperatorSummary() {
    const query = new URLSearchParams();
    if (state.sessionId) {
      query.set("session_id", state.sessionId);
    }
    if (state.requestId) {
      query.set("request_id", state.requestId);
    }
    const payload = await fetchJSON(`/api/operator/summary?${query.toString()}`);
    renderOperatorSummary(payload);
    renderFileScopes(payload, state.fileScopeRoots, state.fileScopeDetail);
    return payload;
  }

  async function refreshVerification(mode = "surfaces") {
    const payload = await fetchJSON(`/api/verify/${encodeURIComponent(mode)}`);
    renderVerification(payload);
    return payload;
  }

  async function refreshIntegrations() {
    const payload = await fetchJSON("/api/integrations");
    renderIntegrations(payload);
    return payload;
  }

  async function refreshRedamonStatus() {
    const query = new URLSearchParams();
    if (state.sessionId) {
      query.set("session_id", state.sessionId);
    }
    const payload = await fetchJSON(`/api/redamon/status${query.toString() ? `?${query.toString()}` : ""}`);
    renderRedAmon(payload);
    return payload;
  }

  async function refreshFileScopes() {
    const roots = await fetchJSON("/api/files/roots");
    renderFileScopes(state.operatorSummary, roots, state.fileScopeDetail);
  }

  async function refreshGpsStatus() {
    const payload = await fetchOptionalJSON("/api/gps/status", {
      status: "offline",
      provider_type: "offline",
      recording: false,
      latest_fix: null,
      active_route: { point_count: 0, distance_km: 0.0 },
      trail: [],
      offline_state: "offline",
    });
    renderGpsStatus(payload);
    return payload;
  }

  async function refreshCommands() {
    const payload = await fetchJSON("/api/commands");
    renderCommandPalette(payload.commands || []);
  }

  async function refreshTiny() {
    const [status, recent, bbs, logTail] = await Promise.all([
      fetchOptionalJSON("/api/tinyprogrammer/status", {
        online: false,
        state: "unavailable",
        stream_available: false,
        stream_human_reason: "TinyProgrammer status is temporarily unavailable.",
        process: {},
      }),
      fetchOptionalJSON("/api/tinyprogrammer/recent", { online: false, programs: [], reflections: [] }),
      fetchOptionalJSON("/api/tinyprogrammer/bbs", { success: false, posts: [], stats: {}, bbs_reason: "unavailable" }),
      fetchOptionalJSON("/api/tinyprogrammer/log", { content: "", human_reason: "TinyProgrammer log is temporarily unavailable." }),
    ]);
    renderTiny(status, recent, bbs, logTail);
  }

  async function refreshCognition() {
    const query = new URLSearchParams();
    query.set("layer", state.layer);
    if (state.sessionId) {
      query.set("session_id", state.sessionId);
    }
    if (state.requestId) {
      query.set("request_id", state.requestId);
    }
    const snapshot = await fetchJSON(`/api/cognition/snapshot?${query.toString()}`);
    renderCognition(snapshot);
  }

  async function refreshSessionSurfaces() {
    await Promise.all([refreshSessions(), refreshMessages(), refreshMemory(), refreshActions(), refreshOperatorSummary(), refreshFileScopes(), refreshCognition(), refreshTiny()]);
  }

  async function executeCommand(command, payload) {
    try {
      const result = await fetchJSON("/api/commands/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          command,
          session_id: state.sessionId,
          request_id: state.requestId,
          ...(payload || {}),
        }),
      });
      pushCommandHistory({ command, result });
      if (result.trace) {
        renderTrace(result.trace);
      }
      if (result.cognition) {
        setMode("thoughtglass");
        state.layer = result.cognition.active_layer || state.layer;
        renderCognition(result.cognition);
      }
      if (result.command === "/whatchanged") {
        showToast(result.changes?.summary_text || "State changes updated.", "success");
      } else {
        showToast(result.status === "error" ? (result.error || "Command failed.") : `${command} completed.`, result.status === "error" ? "error" : "success");
      }
      await Promise.all([
        refreshActions(),
        refreshNotifications(),
        refreshOperatorSummary(),
        refreshFileScopes(),
        refreshIntegrations(),
        refreshRedamonStatus(),
      ]);
      return result;
    } catch (error) {
      pushCommandHistory({ command, result: error.payload || { error: error.message, status: "error" } });
      showToast(error.message, "error");
      throw error;
    }
  }

  function beginLocalUserMessage(text) {
    state.localMessages.push({
      role: "user",
      text,
      timestamp: Date.now() / 1000,
    });
    renderMessages({ messages: [] });
  }

  async function submitInput() {
    const text = el.chatInput.value.trim();
    if (!text) {
      return;
    }
    el.chatInput.value = "";
    if (text.startsWith("/")) {
      await executeCommand(text);
      return;
    }

    beginLocalUserMessage(text);
    state.streamingReply = { text: "", timestamp: Date.now() / 1000, complete: false };
    renderMessages({ messages: state.lastRenderedMessages || [] });
    try {
      const payload = await fetchJSON("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          session_id: state.sessionId,
          request_id: state.requestId || undefined,
          agent: el.agentSelect.value || undefined,
          analysis_mode: el.analysisSelect.value || "standard",
          transport: "hud",
          input_origin: "hud",
          background: true,
        }),
      });
      state.sessionId = payload.session_id;
      state.requestId = payload.request_id;
      renderTrace({
        phase: payload.phase,
        status: payload.status === "accepted" ? "pending" : payload.status,
        phase_message: payload.message,
        events: [],
        reports: [],
      });
      setMode("operator");
      refreshSessions().catch(() => {});
      startTracePolling();
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function startTracePolling() {
    if (state.liveConnected) {
      return;
    }
    stopTracePolling();
    state.tracePollHandle = window.setInterval(() => {
      refreshTrace().catch((error) => {
        stopTracePolling();
        showToast(error.message, "error");
      });
    }, state.liveConnected ? 1200 : 500);
  }

  function stopTracePolling() {
    if (state.tracePollHandle) {
      window.clearInterval(state.tracePollHandle);
      state.tracePollHandle = null;
    }
  }

  async function pinSession() {
    if (!state.sessionId) {
      showToast("Select or create a session first.", "warning");
      return;
    }
    try {
      await fetchJSON(`/api/session/${encodeURIComponent(state.sessionId)}/pin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: state.requestId }),
      });
      showToast("Session memory pinned.", "success");
      await Promise.all([refreshMemory(), refreshActions()]);
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function newSession() {
    state.sessionId = null;
    state.requestId = null;
    state.localMessages = [];
    setWorkspaceTab("chat");
    renderMessages({ messages: [] });
    renderTrace({ phase: "idle", status: "idle", events: [], reports: [] });
    await Promise.all([refreshSessions(), refreshMemory(), refreshActions(), refreshCognition(), refreshRedamonStatus()]);
    showToast("Started a fresh session context.", "success");
  }

  async function handleTinyAction(action) {
    const endpointMap = {
      status: { url: "/api/tinyprogrammer/status", method: "GET" },
      launch: { url: "/api/tinyprogrammer/launch", method: "POST", body: { open_browser: false, enable_stream: true } },
      native: { url: "/api/tinyprogrammer/launch", method: "POST", body: { open_browser: false, enable_stream: true } },
      open: { url: "/api/tinyprogrammer/open", method: "POST", body: { target: "dashboard", open_browser: true } },
      live: { url: "/api/tinyprogrammer/open", method: "POST", body: { target: "live", open_browser: false, consent: { approved: true, level: 1 } } },
      recent: { url: "/api/tinyprogrammer/recent", method: "GET" },
      bbs: { url: "/api/tinyprogrammer/bbs", method: "GET" },
      log: { url: "/api/tinyprogrammer/log", method: "GET" },
    };
    const spec = endpointMap[action];
    if (!spec) {
      return;
    }
    const button = el.tinyButtons.find((item) => item.dataset.tinyAction === action);
    try {
      if (button) {
        button.disabled = true;
      }
      setWorkspaceTab("tiny");
      if (action === "log") {
        setDrawerTab("detail");
      }
      showToast(`TinyProgrammer ${action} queued.`, "info");
      const result = await fetchJSON(spec.url, spec.method === "POST" ? {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: state.sessionId, request_id: state.requestId, ...(spec.body || {}) }),
      } : undefined);
      pushCommandHistory({ command: `/tiny ${action}`, result });
      await Promise.all([refreshTiny(), refreshActions(), refreshNotifications(), refreshCognition(), refreshOperatorSummary()]);
      showToast(
        result.success === false ? (result.human_reason || result.error || `TinyProgrammer ${action} failed.`) : `TinyProgrammer ${action} complete.`,
        result.success === false ? "error" : "success",
      );
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      if (button) {
        button.disabled = false;
      }
    }
  }

  async function handleFileScopeAction(action) {
    const path = (el.scopePathInput?.value || "").trim();
    try {
      setWorkspaceTab("files");
      setDrawerTab("detail");
      let result = null;
      if (action === "roots") {
        result = await fetchJSON("/api/files/roots");
        state.fileScopeDetail = null;
        renderFileScopes(state.operatorSummary, result, null);
      } else if (action === "approve") {
        result = await fetchJSON("/api/files/roots", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path, session_id: state.sessionId, request_id: state.requestId }),
        });
        state.fileScopeDetail = result;
        await Promise.all([refreshOperatorSummary(), refreshFileScopes(), refreshActions(), refreshNotifications()]);
      } else if (action === "map") {
        const query = new URLSearchParams({ path, session_id: state.sessionId || "", request_id: state.requestId || "" });
        result = await fetchJSON(`/api/files/map?${query.toString()}`);
        state.fileScopeDetail = result;
        renderFileScopes(state.operatorSummary, state.fileScopeRoots, result);
      } else if (action === "read") {
        const query = new URLSearchParams({ path, session_id: state.sessionId || "", request_id: state.requestId || "" });
        result = await fetchJSON(`/api/files/read?${query.toString()}`);
        state.fileScopeDetail = result;
        renderFileScopes(state.operatorSummary, state.fileScopeRoots, result);
      }
      if (result) {
        pushCommandHistory({ command: `/files ${action}${path ? ` ${path}` : ""}`, result });
        showToast(
          result.success === false ? (result.human_reason || result.error || "File scope action failed.") : `File scope ${action} complete.`,
          result.success === false ? "error" : "success",
        );
      }
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function handleGpsAction(action) {
    try {
      setWorkspaceTab("maps");
      let result = null;
      if (action === "refresh") {
        result = await refreshGpsStatus();
      } else if (action.startsWith("provider:")) {
        const mode = action.split(":")[1] || "auto";
        result = await fetchJSON("/api/gps/provider", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode, session_id: state.sessionId, request_id: state.requestId }),
        });
      } else if (action === "record:start") {
        result = await fetchJSON("/api/gps/recording/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: state.sessionId, request_id: state.requestId }),
        });
      } else if (action === "record:stop") {
        result = await fetchJSON("/api/gps/recording/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: state.sessionId, request_id: state.requestId }),
        });
      }
      if (result) {
        pushCommandHistory({ command: `/gps ${action}`, result });
        if (result.snapshot) {
          renderGpsStatus(result.snapshot);
        } else if (action !== "refresh") {
          await refreshGpsStatus();
        }
        await refreshOperatorSummary();
        showToast(`GPS ${action} complete.`, "success");
      }
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  function matchesShortcut(event, sequence) {
    const parts = sequence.toLowerCase().split("+");
    const key = parts[parts.length - 1];
    const needsCtrl = parts.includes("ctrl");
    const needsShift = parts.includes("shift");
    const needsMeta = parts.includes("meta") || parts.includes("cmd");
    if (needsCtrl !== event.ctrlKey) {
      return false;
    }
    if (needsShift !== event.shiftKey) {
      return false;
    }
    if (needsMeta !== event.metaKey) {
      return false;
    }
    if (key === "return") {
      return event.key === "Enter";
    }
    if (key === "tab") {
      return event.key === "Tab";
    }
    return event.key.toLowerCase() === key;
  }

  function installHotkeys() {
    document.addEventListener("keydown", async (event) => {
      const tag = event.target && event.target.tagName ? event.target.tagName.toLowerCase() : "";
      if (tag === "textarea" || tag === "input" || tag === "select") {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
          event.preventDefault();
          await submitInput();
        }
        return;
      }
      if (matchesShortcut(event, "f5")) {
        event.preventDefault();
        await generalRefresh();
        return;
      }
      if (matchesShortcut(event, "ctrl+k")) {
        event.preventDefault();
        setDrawerTab("commands");
        el.commandSearchInput?.focus();
        return;
      }
      if (matchesShortcut(event, "ctrl+w")) {
        event.preventDefault();
        if (state.layout.floatingRight) {
          dockRail("right");
        } else if (state.layout.floatingLeft) {
          dockRail("left");
        } else {
          toggleRail("right");
        }
        return;
      }
      if (matchesShortcut(event, "ctrl+shift+h")) {
        event.preventDefault();
        window.open(buildWorkspaceUrl(state.workspaceTab || "chat", true), "_blank");
        return;
      }
      const layerMap = {
        "1": "live_brain",
        "2": "memory",
        "3": "tasks",
        "4": "sensors",
        "5": "faults",
        "6": "forge",
      };
      if (layerMap[event.key]) {
        event.preventDefault();
        setMode("thoughtglass");
        setLayer(layerMap[event.key]);
        await requestCognitionGesture({ action: "switch_layer", mode: "thoughtglass", target: { layer: layerMap[event.key] }, layer: layerMap[event.key] });
      } else if ((event.key === "Enter" || event.key === " ") && state.selectedNode) {
        event.preventDefault();
        const node = (state.cognition?.nodes || []).find((entry) => entry.id === state.selectedNode);
        if (node) {
          await openNodeModule(node);
        }
      }
    });
  }

  function installThoughtglassPointerControls() {
    el.thoughtglassStage.addEventListener("wheel", async (event) => {
      event.preventDefault();
      const amount = event.deltaY < 0 ? 0.1 : -0.1;
      try {
        await requestCognitionGesture({ action: "zoom", mode: "thoughtglass", delta: { amount } });
      } catch (error) {
        showToast(error.message, "error");
      }
    }, { passive: false });

    el.thoughtglassStage.addEventListener("pointerdown", (event) => {
      if (event.target.closest("[data-node-id]")) {
        return;
      }
      state.dragOrigin = { x: event.clientX, y: event.clientY, moved: false };
    });

    window.addEventListener("pointermove", (event) => {
      if (!state.dragOrigin) {
        return;
      }
      state.dragOrigin.moved = true;
      const dx = event.clientX - state.dragOrigin.x;
      const dy = event.clientY - state.dragOrigin.y;
      const snapshot = state.cognition;
      if (!snapshot) {
        return;
      }
      const next = JSON.parse(JSON.stringify(snapshot));
      next.focus.orbit = Number(next.focus.orbit || 0) + dx * 0.002;
      next.focus.pan_y = Number(next.focus.pan_y || 0) + dy / 600;
      renderGraph(next);
    });

    window.addEventListener("pointerup", async (event) => {
      if (!state.dragOrigin) {
        return;
      }
      const dx = event.clientX - state.dragOrigin.x;
      const dy = event.clientY - state.dragOrigin.y;
      const moved = state.dragOrigin.moved;
      state.dragOrigin = null;
      if (!moved) {
        return;
      }
      try {
        await requestCognitionGesture({
          action: "rotate",
          mode: "thoughtglass",
          delta: { amount: dx * 0.002 },
        });
        await requestCognitionGesture({
          action: "drag",
          mode: "thoughtglass",
          delta: { x: 0, y: dy / 600 },
        });
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }

  async function generalRefresh() {
    if (document.hidden) {
      return;
    }
    await Promise.all([
      refreshMetrics(),
      refreshSessions(),
      refreshMemory(),
      refreshActions(),
      refreshNotifications(),
      refreshOperatorSummary(),
      refreshGpsStatus(),
      refreshFileScopes(),
      refreshTiny(),
      refreshIntegrations(),
      refreshRedamonStatus(),
    ]);
    if (state.workspaceTab === "chat" || state.mode === "thoughtglass" || state.requestId) {
      await refreshCognition();
    }
    if (state.workspaceTab === "security") {
      await refreshVerification("surfaces");
    }
    if (state.requestId) {
      await refreshTrace();
    }
  }

  function hudClick(name, fn) {
    return async (...args) => {
      const started = performance.now();
      try {
        await fn(...args);
        debugHudLog("F", "dashboard.js:click", "HUD click ok", {
          action: name,
          duration_ms: Math.round(performance.now() - started),
        });
      } catch (error) {
        debugHudLog("F", "dashboard.js:click", "HUD click error", {
          action: name,
          duration_ms: Math.round(performance.now() - started),
          error: error.message,
        });
        throw error;
      }
    };
  }

  function installEventHandlers() {
    el.toggleLeftRailButton?.addEventListener("click", hudClick("toggle-left-rail", () => toggleRail("left")));
    el.toggleRightRailButton?.addEventListener("click", hudClick("toggle-right-rail", () => toggleRail("right")));
    el.layoutScrim?.addEventListener("click", hudClick("layout-scrim", async () => {
      if (isOverlayRail("left")) {
        state.layout.collapsedLeft = true;
      }
      if (isOverlayRail("right")) {
        state.layout.collapsedRight = true;
      }
      persistLayoutState();
      applyLayoutState();
    }));
    el.leftRailToggle?.addEventListener("click", hudClick("left-rail-toggle", () => toggleRail("left")));
    el.floatLeftRailButton?.addEventListener("click", hudClick("float-left-rail", () => floatRail("left")));
    el.dockLeftRailButton?.addEventListener("click", hudClick("dock-left-rail", () => dockRail("left")));
    el.rightRailToggle?.addEventListener("click", hudClick("right-rail-toggle", () => toggleRail("right")));
    el.floatRightRailButton?.addEventListener("click", hudClick("float-right-rail", () => floatRail("right")));
    el.dockRightRailButton?.addEventListener("click", hudClick("dock-right-rail", () => dockRail("right")));
    el.workbenchTabButtons.forEach((button) => {
      button.addEventListener("click", hudClick(`workspace-tab:${button.dataset.workspaceTab}`, () => setWorkspaceTab(button.dataset.workspaceTab)));
    });
    el.drawerTabButtons.forEach((button) => {
      button.addEventListener("click", hudClick(`drawer-tab:${button.dataset.drawerTab}`, () => setDrawerTab(button.dataset.drawerTab)));
    });
    el.sendButton.addEventListener("click", hudClick("send", () => submitInput()));
    el.traceButton.addEventListener("click", hudClick("trace", () => {
      setWorkspaceTab("trace");
      if (state.requestId) {
        refreshTrace().catch((error) => showToast(error.message, "error"));
      } else {
        executeCommand("/trace").catch(() => {});
      }
    }));
    el.pinButton.addEventListener("click", hudClick("pin-session", () => pinSession()));
    el.refreshButton.addEventListener("click", hudClick("refresh", () => generalRefresh()));
    el.newSessionButton.addEventListener("click", hudClick("new-session", () => newSession()));
    el.whatChangedButton.addEventListener("click", hudClick("what-changed", () => executeCommand("/whatchanged")));
    el.commandPalette?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-command]");
      if (!button || !el.commandPalette.contains(button)) {
        return;
      }
      executeCommand(button.dataset.command).catch(() => {});
    });
    const updateCommandSearch = debounce(() => {
      state.commandQuery = el.commandSearchInput?.value || "";
      persistUiState();
      renderCommandPalette(state.commands);
    });
    el.commandSearchInput?.addEventListener("input", updateCommandSearch);
    el.commandSearchInput?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        const typed = el.commandSearchInput.value.trim();
        if (typed.startsWith("/")) {
          executeCommand(typed).catch(() => {});
        }
      }
    });
    el.openFilesWorkspaceButton?.addEventListener("click", hudClick("open-files", () => setWorkspaceTab("files")));
    el.focusFilesDrawerButton?.addEventListener("click", hudClick("focus-files", () => {
      setWorkspaceTab("files");
      setDrawerTab("detail");
    }));
    el.notificationsReadAllButton.addEventListener("click", hudClick("notifications-read-all", async () => {
      setDrawerTab("notifications");
      await executeCommand("/notifications read-all");
      await refreshNotifications();
    }));
    el.testToastButton.addEventListener("click", hudClick("test-toast", async () => {
      setDrawerTab("notifications");
      await fetchJSON("/api/notifications/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ desktop: false, session_id: state.sessionId, request_id: state.requestId }),
      });
      await refreshNotifications();
      showToast("Notification test created.", "success");
    }));
    el.resetLayoutButton.addEventListener("click", hudClick("reset-layout", () => {
      resetLayoutState();
      showToast("HUD layout reset.", "success");
    }));
    el.chatInput.addEventListener("keydown", async (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        await submitInput();
      }
    });
    el.messagesPanel?.addEventListener("click", (event) => {
      const button = event.target.closest(".message-expand");
      if (!button || !el.messagesPanel.contains(button)) {
        return;
      }
      const index = Number(button.dataset.msgIndex || -1);
      if (index < 0) {
        return;
      }
      if (button.dataset.action === "expand") {
        state.expandedMessages.add(index);
      } else {
        state.expandedMessages.delete(index);
      }
      renderMessages({ messages: state.lastRenderedMessages || [] });
    });
    el.layerButtons.forEach((button) => {
      button.addEventListener("click", async () => {
        setWorkspaceTab("thoughtglass");
        setLayer(button.dataset.layer);
        try {
          await requestCognitionGesture({
            action: "switch_layer",
            mode: "thoughtglass",
            target: { layer: button.dataset.layer },
            layer: button.dataset.layer,
          });
        } catch (error) {
          showToast(error.message, "error");
        }
      });
    });
    el.tinyButtons.forEach((button) => {
      button.addEventListener("click", () => handleTinyAction(button.dataset.tinyAction));
    });
    el.gpsButtons.forEach((button) => {
      button.addEventListener("click", () => handleGpsAction(button.dataset.gpsAction));
    });
    el.fileScopeButtons.forEach((button) => {
      button.addEventListener("click", () => handleFileScopeAction(button.dataset.fileScopeAction));
    });
    el.verifyButtons.forEach((button) => {
      button.addEventListener("click", () => {
        setWorkspaceTab("security");
        refreshVerification(button.dataset.verifyMode || "surfaces").catch((error) => showToast(error.message, "error"));
      });
    });
    el.redamonButtons.forEach((button) => {
      button.addEventListener("click", async () => {
        const action = button.dataset.redamonAction;
        const objective = el.redamonObjectiveInput?.value?.trim() || "";
        const target = el.redamonTargetInput?.value?.trim() || "";
        try {
          let result;
          if (action === "status") {
            result = await refreshRedamonStatus();
          } else if (action === "enter") {
            result = await fetchJSON("/api/redamon/enter", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ session_id: state.sessionId, request_id: state.requestId, objective }),
            });
            renderRedAmon(result.mode || result);
          } else if (action === "propose") {
            result = await fetchJSON("/api/redamon/propose", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ session_id: state.sessionId, request_id: state.requestId, objective, target }),
            });
            renderRedAmon(result.mode || result);
          } else if (action === "launch") {
            result = await fetchJSON("/api/redamon/launch", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                session_id: state.sessionId,
                request_id: state.requestId,
                consent: { approved: true, level: 2 },
                execute: false,
              }),
            });
            renderRedAmon(result.mode || result);
          }
          if (result) {
            pushCommandHistory({ command: `/redamon ${action}`, result });
            showToast(result.success === false ? (result.error || result.human_reason || `RedAmon ${action} blocked.`) : `RedAmon ${action} ready.`, result.success === false ? "warning" : "success");
            await Promise.all([refreshActions(), refreshNotifications(), refreshOperatorSummary(), refreshIntegrations()]);
          }
        } catch (error) {
          showToast(error.message, "error");
        }
      });
    });
    el.refreshIntegrationsButton?.addEventListener("click", () => {
      setWorkspaceTab("security");
      Promise.all([refreshIntegrations(), refreshRedamonStatus(), refreshVerification("integrations")]).catch((error) => showToast(error.message, "error"));
    });
    el.popoutButtons.forEach((button) => {
      button.addEventListener("click", hudClick(`popout:${button.dataset.openWorkspaceWindow}`, async () => {
        openWorkspaceWindow(button.dataset.openWorkspaceWindow || "chat");
        button.closest("details")?.removeAttribute("open");
      }));
    });
  }

  async function loadSharedSession() {
    try {
      const payload = await fetchJSON("/api/operator/session");
      if (payload.session_id) {
        state.sessionId = payload.session_id;
        el.sessionMeta.textContent = `Session ${state.sessionId} (linked)`;
      }
      const linked = payload.linked_surfaces || [];
      if (el.linkedSessionChip) {
        if (linked.includes("desktop") && linked.includes("hud")) {
          el.linkedSessionChip.textContent = "Desktop + HUD linked";
          el.linkedSessionChip.hidden = false;
        } else if (linked.length >= 2) {
          el.linkedSessionChip.textContent = `${linked.join(" + ")} linked`;
          el.linkedSessionChip.hidden = false;
        } else {
          el.linkedSessionChip.hidden = true;
        }
      }
    } catch (error) {
      // Shared session file may not exist yet on first boot.
    }
  }

  function installFloatingRailDrag() {
    // Mission-control layout uses controlled docks and overlays instead of
    // restoring unstable free-floating pixel positions.
  }

  async function init() {
    debugHudLog("B", "dashboard.js:init", "HUD init start", {});
    loadLayoutState();
    applyRouteStateFromUrl();
    await loadSharedSession();
    loadCommandHistory();
    loadUiState();
    applyRouteStateFromUrl();
    installColumnResizers();
    installPanelResizers();
    applyLayoutState();
    installFloatingRailDrag();
    installEventHandlers();
    installHotkeys();
    installThoughtglassPointerControls();
    if (el.commandSearchInput) {
      el.commandSearchInput.value = state.commandQuery || "";
    }
    renderCommandHistory();
    setLayer(state.layer);
    setDrawerTab(state.drawerTab);
    setWorkspaceTab(state.workspaceTab);
    updateViewportState();
    renderTrace({ phase: "idle", status: "idle", events: [], reports: [] });
    setTransportState("polling", "starting");
    connectLiveStream();
    await Promise.all([refreshCommands(), generalRefresh(), refreshKeyboardShortcuts()]);
    debugHudLog("B", "dashboard.js:init", "HUD init complete", { workspaceTab: state.workspaceTab });
    window.requestAnimationFrame(() => logChatLayoutState("dashboard.js:init"));
    state.generalPollHandle = window.setInterval(() => {
      generalRefresh().catch((error) => showToast(error.message, "error"));
    }, 15000);
    window.addEventListener("resize", debounce(() => {
      updateViewportState();
      applyLayoutState();
    }, 120));
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        generalRefresh().catch((error) => showToast(error.message, "error"));
      }
    });
    window.requestAnimationFrame(function tick() {
      if (state.cognition) {
        drawThoughtglassFx(state.cognition);
      }
      window.requestAnimationFrame(tick);
    });
  }

  window.addEventListener("beforeunload", () => {
    stopTracePolling();
    if (state.liveSource) {
      state.liveSource.close();
    }
    if (state.liveRetryHandle) {
      window.clearTimeout(state.liveRetryHandle);
    }
    if (state.generalPollHandle) {
      window.clearInterval(state.generalPollHandle);
    }
  });

  window.addEventListener("DOMContentLoaded", init);
})();
