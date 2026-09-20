(function () {
  "use strict";
  function init() {
    const panels = [...document.querySelectorAll(".left-rail, .right-rail")];
    const control = document.getElementById("panelOpacity");
    let opacity = "0.85";
    try { opacity = localStorage.getItem("obeos.panelOpacity") || opacity; } catch (_) {}
    if (!["1", "0.85", "0.7"].includes(opacity)) opacity = "0.85";
    if (control) control.value = opacity;
    let foreground = null;
    const intersects = (a, b) => a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    let pending = false;
    function update() {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => { pending = false; render(); });
    }
    function render() {
      if (document.hidden) return;
      const visible = panels.filter(panel => panel.getClientRects().length && getComputedStyle(panel).visibility !== "hidden");
      const center = document.querySelector(".center-stage, .main-stage, main");
      const targets = center ? [...visible, center] : visible;
      const rectangles = new Map([...new Set([...panels, ...targets])].map(panel => [panel, panel.getBoundingClientRect()]));
      const changes = [];
      for (const panel of panels) {
        const floating = panel.classList.contains("floating-rail");
        const active = panel.matches(":hover") || panel.contains(document.activeElement);
        const rect = rectangles.get(panel);
        const overlapping = targets.some(other => other !== panel && intersects(rect, rectangles.get(other)));
        changes.push([panel, floating && !active && overlapping ? opacity : "1", floating ? (foreground === panel ? "36" : "35") : ""]);
      }
      for (const [panel, alpha, z] of changes) {
        if (panel.style.opacity !== alpha) panel.style.opacity = alpha;
        if (panel.style.zIndex !== z) panel.style.zIndex = z;
      }
    }
    for (const panel of panels) {
      for (const event of ["pointerenter", "pointerdown", "focusin"]) {
        panel.addEventListener(event, () => { foreground = panel; update(); });
      }
      panel.addEventListener("pointerleave", update);
      panel.addEventListener("focusout", () => requestAnimationFrame(update));
    }
    control?.addEventListener("change", () => {
      opacity = control.value;
      try { localStorage.setItem("obeos.panelOpacity", opacity); } catch (_) {}
      update();
    });
    const observer = new MutationObserver(update);
    for (const panel of panels) observer.observe(panel, { attributes: true, attributeFilter: ["class", "style", "hidden"] });
    observer.observe(document.body, { attributes: true, attributeFilter: ["class", "data-viewport-bucket"] });
    const resize = new ResizeObserver(update);
    for (const panel of panels) resize.observe(panel);
    const center = document.querySelector("main");
    if (center) resize.observe(center);
    window.addEventListener("resize", update);
    document.addEventListener("visibilitychange", update);
    update();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
