// The figure engine. A figure is a list of steps; the picture at time T is init() followed by
// every started step drawn at its progress, so play, pause, seek and step are exact and nothing
// accumulates between frames.
window.FK = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs, parent) => {
    const el = document.createElementNS(NS, tag);
    for (const k in attrs || {}) el.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(el);
    return el;
  };
  const txt = (parent, x, y, s, cls, attrs) => {
    const t = mk("text", Object.assign({ x, y }, cls ? { class: cls } : {}, cls === "cap" ? { fill: "#9a948a" } : {}, attrs || {}), parent);
    t.textContent = s;
    return t;
  };
  const clamp = p => (p < 0 ? 0 : p > 1 ? 1 : p);
  const ease = t => ((t = clamp(t)), t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const seg = (p, a, b) => clamp((p - a) / (b - a));
  const lerp = (a, b, p) => a + (b - a) * p;
  const op = (el, o) => el.setAttribute("opacity", String(Math.round(clamp(o) * 1000) / 1000));
  const pos = (el, x, y, s) => el.setAttribute("transform", `translate(${x},${y})` + (s != null && s !== 1 ? ` scale(${s})` : ""));
  const drawIn = (el, p) => {
    el.setAttribute("pathLength", "1");
    el.setAttribute("stroke-dasharray", "1 1");
    el.setAttribute("stroke-dashoffset", String(1 - clamp(p)));
  };
  const still = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const presenting = () => document.body.classList.contains("present");
  const all = [];

  // A person: head and shoulders in the role's colour, the name beneath.
  function person(parent, color, name, role) {
    const g = mk("g", {}, parent);
    mk("circle", { cx: 0, cy: -30, r: 11, fill: color }, g);
    mk("path", { d: "M-19,4 C-19,-11 -10,-15 0,-15 C10,-15 19,-11 19,4 Z", fill: color }, g);
    if (name) txt(g, 0, 22, name, "name", { "text-anchor": "middle", fill: "#161513" });
    if (role) txt(g, 0, 38, role, "lbl", { "text-anchor": "middle" });
    return g;
  }

  // An agent: a rounded terminal with a prompt mark, the name beneath.
  function agent(parent, name, role, tint) {
    const g = mk("g", {}, parent);
    mk("rect", { x: -22, y: -40, width: 44, height: 40, rx: 9, fill: tint || "#1d1b18" }, g);
    mk("path", { d: "M-11,-26 L-4,-20 L-11,-14", fill: "none", stroke: "#ffffff", "stroke-width": 2.2, "stroke-linecap": "round", "stroke-linejoin": "round" }, g);
    mk("line", { x1: 0, y1: -13, x2: 10, y2: -13, stroke: "#ffffff", "stroke-width": 2.2, "stroke-linecap": "round" }, g);
    if (name) txt(g, 0, 22, name, "name", { "text-anchor": "middle", fill: "#161513" });
    if (role) txt(g, 0, 38, role, "lbl", { "text-anchor": "middle" });
    return g;
  }

  function fig(root, spec) {
    const steps = spec.steps;
    const starts = [];
    let total = 0;
    for (const s of steps) { starts.push(total); total += s.dur; }
    const ctl = root.querySelector(".fk-ctl");
    const status = root.querySelector(".fk-status");
    const B = {};
    for (const [k, label, aria] of [["prev", "←", "Previous step"], ["play", "Play", "Play or pause"], ["next", "→", "Next step"], ["reset", "Reset", "Restart"]]) {
      B[k] = document.createElement("button");
      B[k].type = "button";
      B[k].textContent = label;
      B[k].setAttribute("aria-label", aria);
      ctl.appendChild(B[k]);
    }
    let T = 0, playing = false, last = 0, raf = 0, visible = true;
    const index = t => { let i = 0; while (i < steps.length - 1 && t >= starts[i + 1]) i++; return i; };
    const render = () => {
      spec.init();
      for (let i = 0; i < steps.length; i++) {
        if (T > starts[i] || (T === 0 && i === 0 && spec.showFirst)) steps[i].draw(clamp((T - starts[i]) / steps[i].dur));
      }
      const i = index(Math.max(0, T - 1));
      status.innerHTML = `<span>${steps[i].label}</span><b>${i + 1} / ${steps.length}</b>`;
      B.prev.disabled = T <= 0;
      B.next.disabled = T >= total;
      B.play.textContent = playing ? "Pause" : "Play";
    };
    const tick = now => {
      raf = 0;
      if (!playing) return;
      const dt = Math.min(64, now - (last || now));
      last = now;
      T = Math.min(total, T + dt);
      if (T >= total) playing = false;
      render();
      if (playing && visible) raf = requestAnimationFrame(tick);
    };
    const play = () => { if (T >= total) T = 0; playing = true; last = 0; if (!raf) raf = requestAnimationFrame(tick); render(); };
    const pause = () => { playing = false; render(); };
    const bounds = [0, ...starts.slice(1), total];
    B.play.onclick = () => (playing ? pause() : play());
    B.reset.onclick = () => { T = 0; play(); };
    B.prev.onclick = () => { pause(); T = [...bounds].reverse().find(b => b < T - 1) ?? 0; render(); };
    B.next.onclick = () => { pause(); T = bounds.find(b => b > T + 1) ?? total; render(); };
    const api = { root, render, play, pause, seek: t => { playing = false; T = clamp(t / total) * total; render(); }, total, starts };
    all.push(api);
    // Paused at the end of the first step until the presenter presses Play.
    T = still() ? total : (starts[1] ?? total);
    render();
    new IntersectionObserver(entries => {
      for (const e of entries) {
        visible = e.isIntersecting;
        if (visible && playing && !raf) { last = 0; raf = requestAnimationFrame(tick); }
      }
    }, { threshold: 0.1 }).observe(root);
    return api;
  }

  // Builds the figure frame: title, controls, svg, status line and caption.
  function frame(host, { title, legend, viewBox, caption }) {
    const f = document.createElement("figure");
    f.className = "fk";
    f.innerHTML = `<div class="fk-head"><div class="fk-title">${title}</div><div class="fk-ctl"></div></div>` +
      (legend ? `<div class="fk-legend">${legend.map(([c, s, l]) => `<span><i style="background:${c};border-color:${s}"></i>${l}</span>`).join("")}</div>` : "") +
      `<svg viewBox="${viewBox}" role="img" aria-label="${title}"></svg><div class="fk-status"></div>` +
      (caption ? `<figcaption>${caption}</figcaption>` : "");
    host.appendChild(f);
    return { root: f, svg: f.querySelector("svg") };
  }

  return { NS, mk, txt, clamp, ease, seg, lerp, op, pos, drawIn, person, agent, fig, frame, still, all };
})();

// The rail follows the section in view, and presenter mode hides narration.
document.addEventListener("DOMContentLoaded", () => {
  const links = [...document.querySelectorAll(".rail a")];
  const watch = new IntersectionObserver(entries => {
    for (const e of entries) if (e.isIntersecting) links.forEach(a => a.setAttribute("aria-current", String(a.hash === "#" + e.target.id)));
  }, { rootMargin: "-40% 0px -55% 0px" });
  document.querySelectorAll("section.block").forEach(s => watch.observe(s));
  const pres = document.querySelector(".pres");
  if (pres) pres.onclick = () => { const on = document.body.classList.toggle("present"); pres.setAttribute("aria-pressed", String(on)); };
});
