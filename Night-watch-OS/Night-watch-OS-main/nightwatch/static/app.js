const $ = (id) => document.getElementById(id);

const state = {
  shift: null,
  tasks: [],
  focus: false,
  lastSystemOkAt: 0,
};

function fmtTime(d) {
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function fmtDate(d) {
  return d.toLocaleDateString([], { weekday: "short", month: "short", day: "2-digit" });
}

function fmtDt(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j?.detail) msg = j.detail;
    } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

function setFocus(on) {
  state.focus = on;
  document.body.classList.toggle("focus", on);
  $("focusToggle").setAttribute("aria-pressed", on ? "true" : "false");
  $("focusToggle").textContent = on ? "Exit focus" : "Focus";
  try {
    localStorage.setItem("nightwatch.focus", on ? "1" : "0");
  } catch {}
}

function renderShift() {
  const s = state.shift;
  if (!s) {
    $("shiftMeta").textContent = "No active shift.";
    $("shiftEnd").disabled = true;
    $("shiftNotes").disabled = true;
    $("saveNotes").disabled = true;
    $("shiftNotes").value = "";
    return;
  }
  $("shiftMeta").textContent = `Active since ${fmtDt(s.started_at)}`;
  $("shiftEnd").disabled = false;
  $("shiftNotes").disabled = false;
  $("saveNotes").disabled = false;
  $("shiftNotes").value = s.notes || "";
}

function renderTasks() {
  const list = $("taskList");
  list.innerHTML = "";

  const total = state.tasks.length;
  const done = state.tasks.filter((t) => t.completed_at).length;
  $("taskMeta").textContent = `${total} total / ${done} done`;

  if (total === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.style.padding = "6px 2px";
    li.textContent = state.shift ? "No tasks yet." : "Start a shift to use the ledger.";
    list.appendChild(li);
    return;
  }

  for (const t of state.tasks) {
    const li = document.createElement("li");
    li.className = "task" + (t.completed_at ? " task--done" : "");

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "task__check";
    cb.checked = !!t.completed_at;
    cb.addEventListener("change", async () => {
      try {
        if (cb.checked) await api(`/api/tasks/${t.id}/complete`, { method: "POST" });
        else await api(`/api/tasks/${t.id}/reopen`, { method: "POST" });
        await refreshTasks();
      } catch (e) {
        cb.checked = !cb.checked;
        toast(e.message);
      }
    });

    const title = document.createElement("div");
    title.className = "task__title";
    title.textContent = t.title;

    const actions = document.createElement("div");
    actions.className = "task__actions";

    const del = document.createElement("button");
    del.className = "iconbtn";
    del.type = "button";
    del.textContent = "Del";
    del.addEventListener("click", async () => {
      try {
        await api(`/api/tasks/${t.id}`, { method: "DELETE" });
        await refreshTasks();
      } catch (e) {
        toast(e.message);
      }
    });

    actions.appendChild(del);
    li.appendChild(cb);
    li.appendChild(title);
    li.appendChild(actions);
    list.appendChild(li);
  }
}

function renderSystem(sys) {
  $("cpuVal").textContent = `${sys.cpu_percent.toFixed(0)}%`;
  $("ramVal").textContent = `${sys.ram_percent.toFixed(0)}% (${sys.ram_used_mb} / ${sys.ram_total_mb} MB)`;
  $("diskVal").textContent = `${sys.disk_percent.toFixed(0)}% (${sys.disk_used_gb} / ${sys.disk_total_gb} GB)`;
  $("tempVal").textContent = sys.temp_c == null ? "—" : `${sys.temp_c.toFixed(1)}°C`;
  $("netVal").textContent = sys.network_up ? "UP" : "DOWN";
  $("netVal").style.color = sys.network_up ? "var(--text)" : "var(--red)";
  $("systemMeta").textContent = `updated ${fmtTime(new Date(sys.at))}`;
}

function toast(msg) {
  $("shiftHint").textContent = msg;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => ($("shiftHint").textContent = ""), 2600);
}

async function refreshShift() {
  state.shift = await api("/api/shift/current");
  renderShift();
}

async function refreshTasks() {
  state.tasks = await api("/api/tasks/current");
  renderTasks();
}

async function pollSystem() {
  try {
    const sys = await api("/api/system");
    state.lastSystemOkAt = Date.now();
    $("hbDot").classList.add("dot--ok");
    renderSystem(sys);
  } catch {
    $("hbDot").classList.remove("dot--ok");
    const staleFor = Date.now() - state.lastSystemOkAt;
    $("systemMeta").textContent = staleFor ? `offline (${Math.round(staleFor / 1000)}s)` : "offline";
  }
}

function startClock() {
  const tick = () => {
    const d = new Date();
    $("clockTime").textContent = fmtTime(d);
    $("clockDate").textContent = fmtDate(d);
  };
  tick();
  setInterval(tick, 1000);
}

async function boot() {
  startClock();

  // URL override for screenshots / kiosk launches.
  try {
    const qs = new URLSearchParams(location.search);
    if (qs.get("focus") === "1") setFocus(true);
  } catch {}

  try {
    const saved = localStorage.getItem("nightwatch.focus");
    if (saved === "1") setFocus(true);
  } catch {}

  $("focusToggle").addEventListener("click", () => setFocus(!state.focus));

  $("shiftStart").addEventListener("click", async () => {
    try {
      const r = await api("/api/shift/start", { method: "POST", body: "{}" });
      state.shift = r.shift;
      renderShift();
      await refreshTasks();
      if (r.carried_task_count) toast(`Carried ${r.carried_task_count} task(s).`);
      else if (r.already_active) toast("Shift already active.");
      else toast("Shift started.");
    } catch (e) {
      toast(e.message);
    }
  });

  $("shiftEnd").addEventListener("click", async () => {
    try {
      await api("/api/shift/end", { method: "POST", body: "{}" });
      toast("Shift ended.");
      await refreshShift();
      state.tasks = [];
      renderTasks();
    } catch (e) {
      toast(e.message);
    }
  });

  $("saveNotes").addEventListener("click", async () => {
    try {
      if (!state.shift) return;
      const notes = $("shiftNotes").value || "";
      const s = await api(`/api/shift/${state.shift.id}/notes`, {
        method: "PUT",
        body: JSON.stringify({ notes }),
      });
      state.shift = s;
      toast("Notes saved.");
    } catch (e) {
      toast(e.message);
    }
  });

  $("taskForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!state.shift) return toast("Start a shift first.");
    const title = $("taskTitle").value.trim();
    if (!title) return;
    $("taskTitle").value = "";
    try {
      await api("/api/tasks", { method: "POST", body: JSON.stringify({ title }) });
      await refreshTasks();
    } catch (e) {
      toast(e.message);
    }
  });

  await refreshShift();
  await refreshTasks();
  await pollSystem();
  setInterval(pollSystem, 3000);
  setInterval(refreshShift, 5000);
  setInterval(refreshTasks, 5000);
}

boot();

