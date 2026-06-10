/* ✨ Dekorativní efekty (žádná aplikační logika – když selžou, web jede dál):
   1) světlo sledující kurzor, 2) 3D náklon karet odměn za myší.
   Jen desktop s myší; respektuje prefers-reduced-motion. */
(() => {
  try {
    if (!matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    // --- světlo kurzoru (fixed vrstva, pointer-events: none) ---
    const glow = document.createElement("div");
    glow.style.cssText =
      "position:fixed;width:540px;height:540px;border-radius:50%;pointer-events:none;z-index:1;" +
      "mix-blend-mode:screen;transform:translate(-50%,-50%);left:-999px;top:-999px;" +
      "background:radial-gradient(circle,rgba(0,229,255,.07),rgba(255,45,122,.05) 45%,transparent 70%)";
    document.body.appendChild(glow);
    let gx = -999, gy = -999, raf = 0;
    addEventListener("pointermove", (e) => {
      gx = e.clientX; gy = e.clientY;
      if (!raf) raf = requestAnimationFrame(() => { glow.style.left = gx + "px"; glow.style.top = gy + "px"; raf = 0; });
    }, { passive: true });

    // --- 3D tilt karet (delegace – funguje i pro karty přidané později) ---
    const MAX_DEG = 7;
    document.addEventListener("pointermove", (e) => {
      const c = e.target.closest && e.target.closest(".da-card");
      if (!c) return;
      const r = c.getBoundingClientRect();
      const rx = ((e.clientY - r.top) / r.height - 0.5) * -2 * MAX_DEG;
      const ry = ((e.clientX - r.left) / r.width - 0.5) * 2 * MAX_DEG;
      c.style.transform = `perspective(900px) rotateX(${rx.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg) translateY(-5px)`;
    }, { passive: true });
    document.addEventListener("pointerout", (e) => {
      const c = e.target.closest && e.target.closest(".da-card");
      if (c && !c.contains(e.relatedTarget)) c.style.transform = "";
    }, { passive: true });
  } catch (e) { /* dekorace nesmí nikdy shodit web */ }
})();
