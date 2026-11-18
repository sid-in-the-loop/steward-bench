// Section 6: the findings, each a figure built from window.DECK.
(() => {
  const { mk, txt, ease, seg, lerp, op, pos, drawIn, fig, frame } = FK;
  const { MODELS, linear, pct, yAxis, xAxis } = CH;
  const D = window.DECK;
  const M = id => D.by_model[id];
  const nameLegend = () => MODELS.map(m => [m.color, m.color, m.name]);
  const barRect = (g, x, w, y0, color) => mk("rect", { x, y: y0, width: w, height: 0, rx: 3, fill: color }, g);
  const setBar = (r, y, y0, p) => { const h = (y0 - y) * ease(p); r.setAttribute("y", y0 - h); r.setAttribute("height", Math.max(0, h)); };
  const whisker = (g, x, lo, hi) => mk("line", { x1: x, x2: x, y1: lo, y2: hi, stroke: "#3b3a37", "stroke-width": 1, opacity: 0 }, g);

  // 6.1 A stale look is almost no look.
  (() => {
    const { root, svg } = frame(document.getElementById("fig-stale"), {
      title: "A stale look is almost no look", viewBox: "0 0 960 440", legend: nameLegend(),
      caption: "Harm on changed shifts where the agent went ahead, by when it last looked at the item. Dot size is the share of shifts. Old or missing looks are 27 to 53% of shifts but 71 to 86% of harm. Four cases whose looks cannot be detected reliably are left out.",
    });
    const X = [210, 480, 750], y = linear(0, 1, 380, 30);
    yAxis(svg, y, 110, 860, [0, .25, .5, .75, 1], "Harm rate");
    const KEYS = ["this", "earlier", "never"], LABELS = ["This shift", "An earlier shift", "Never"];
    KEYS.forEach((k, i) => txt(svg, X[i], 408, LABELS[i], "lbl", { "text-anchor": "middle", fill: "#161513", "font-size": 13.5 }));
    txt(svg, 480, 436, "When the agent last looked at the item", "lbl", { "text-anchor": "middle" });
    const box = mk("rect", { x: 400, y: 24, width: 430, height: 140, rx: 12, fill: "#f6f4f0", opacity: 0 }, svg);
    const boxL = txt(svg, 615, 154, "almost the same", "lbl", { "text-anchor": "middle", opacity: 0 });
    const series = MODELS.map((m, j) => {
      const d = M(m.id).stale, dx = (j - 1) * 12;
      const pts = KEYS.map((k, i) => [X[i] + dx, y(d[k].rate)]);
      const segs = [0, 1].map(i => mk("line", { x1: pts[i][0], y1: pts[i][1], x2: pts[i + 1][0], y2: pts[i + 1][1], stroke: m.color, "stroke-width": 2.4 }, svg));
      const wh = KEYS.map((k, i) => mk("line", { x1: pts[i][0], x2: pts[i][0], y1: y(d[k].low), y2: y(d[k].high), stroke: m.color, "stroke-width": 1.2, opacity: 0 }, svg));
      const dots = KEYS.map((k, i) => mk("circle", { cx: pts[i][0], cy: pts[i][1], r: 4 + 22 * Math.sqrt(d[k].share), fill: m.color, stroke: "#fff", "stroke-width": 2 }, svg));
      const label = txt(svg, 790, y(.95) + [46, 0, 23][j], m.name, "name", { fill: m.color });
      return { segs, wh, dots, label };
    });
    const key = mk("g", { transform: "translate(110,460)" }, svg);
    fig(root, {
      init() {
        op(box, 0); op(boxL, 0);
        series.forEach(s => { s.segs.forEach(l => { op(l, 0); drawIn(l, 0); }); s.wh.forEach(w => op(w, 0)); s.dots.forEach(d => op(d, 0)); op(s.label, 0); });
      },
      steps: [
        { label: "Looked in this shift: harm is low for every model", dur: 2200, draw: p => series.forEach((s, j) => { op(s.dots[0], seg(p, j * .2, .4 + j * .2)); op(s.wh[0], .35 * seg(p, .6, 1)); }) },
        { label: "Last looked in an earlier shift: harm jumps", dur: 2400, draw: p => series.forEach((s, j) => { op(s.segs[0], 1); drawIn(s.segs[0], seg(p, j * .1, .6 + j * .1)); op(s.dots[1], seg(p, .55 + j * .1, .75 + j * .1)); op(s.wh[1], .35 * seg(p, .8, 1)); }) },
        { label: "Never looked: barely higher", dur: 2400, draw: p => series.forEach((s, j) => { op(s.segs[1], 1); drawIn(s.segs[1], seg(p, j * .1, .6 + j * .1)); op(s.dots[2], seg(p, .55 + j * .1, .75 + j * .1)); op(s.wh[2], .35 * seg(p, .8, 1)); op(s.label, seg(p, .8, 1)); }) },
        { label: "A look from an earlier shift protects almost as little as none", dur: 1800, draw: p => { op(box, seg(p, 0, .5)); op(boxL, seg(p, .3, .8)); } },
      ],
    });
  })();

  // A grouped bar chart: groups on x, one bar per model.
  function groupedBars(host, { title, caption, groups, subs, get, viewBox, stepLabels, yMax }) {
    const { root, svg } = frame(host, { title, viewBox: viewBox || "0 0 960 420", legend: nameLegend(), caption });
    const top = yMax || .8, y = linear(0, top, 350, 30), x0 = 110, x1 = 940;
    yAxis(svg, y, x0, x1, [0, .2, .4, .6, .8].filter(t => t <= top + 1e-9), "Harm rate");
    const gw = (x1 - x0) / groups.length, bw = 52, gap = 8;
    const G = groups.map((g, i) => {
      const cx = x0 + gw * (i + .5);
      txt(svg, cx, 376, g, "name", { "text-anchor": "middle", fill: "#161513", "font-size": 14 });
      if (subs) txt(svg, cx, 400, subs[i], "lbl", { "text-anchor": "middle" });
      return MODELS.map((m, j) => {
        const v = get(m.id, i), bx = cx + (j - 1) * (bw + gap) - bw / 2;
        const r = barRect(svg, bx, bw, y(0), m.color);
        const w = whisker(svg, bx + bw / 2, y(v.low), y(v.high));
        const t = txt(svg, bx + bw / 2, y(v.high) - 8, pct(v.rate), "lbl", { "text-anchor": "middle", fill: "#3b3a37" });
        return { r, w, t, y: y(v.rate) };
      });
    });
    fig(root, {
      init() { G.forEach(bars => bars.forEach(b => { setBar(b.r, b.y, y(0), 0); op(b.w, 0); op(b.t, 0); })); },
      steps: G.map((bars, i) => ({ label: stepLabels[i], dur: 2000, draw: p => bars.forEach((b, j) => {
        const q = seg(p, j * .12, .6 + j * .12); setBar(b.r, b.y, y(0), q); op(b.w, .3 * seg(p, .7, 1)); op(b.t, seg(p, .7, 1));
      }) })),
    });
  }

  // 6.2 Visibility.
  groupedBars(document.getElementById("fig-visibility-harm"), {
    title: "The harder the change is to see, the more agents break it",
    caption: "Harm on changed shifts by level. Announced: the notes or the owner say what changed. Visible: the change can be found by looking. Hidden: the routine's own tool cannot show it (nine cases).",
    groups: ["Announced", "Visible", "Hidden"], subs: ["told what changed", "there if it looks", "outside the tool's view"],
    get: (m, i) => M(m).visibility[["Announced", "Visible", "Hidden"][i]],
    stepLabels: ["Announced: near zero for every model", "Visible: harm rises", "Hidden: harm rises again, for every model"],
  });

  // 6.3 What the agent is told.
  (() => {
    const host = document.getElementById("fig-told");
    const { root, svg } = frame(host, {
      title: "Accurate handover notes prevent almost all harm", viewBox: "0 0 960 420", legend: nameLegend(),
      caption: "Harm on changed shifts in sessions with varied people. Left: what the handover notes say, when the owner is silent. Right: whether the owner says what changed, when the notes are not accurate.",
    });
    const y = linear(0, .8, 350, 30);
    yAxis(svg, y, 110, 940, [0, .2, .4, .6, .8], "Harm rate");
    const GROUPS = [
      ["Accurate notes", "notes", "accurate", 230], ["Out of date", "notes", "out_of_date", 420], ["Missing", "notes", "missing", 600],
      ["Owner silent", "owner", "silent", 760], ["Owner speaks up", "owner", "speaks", 880],
    ];
    mk("line", { x1: 680, x2: 680, y1: 30, y2: 350, stroke: "#e7e3dc", "stroke-dasharray": "3 4" }, svg);
    const bw = 34, gap = 4;
    const G = GROUPS.map(([name, src, key, cx]) => {
      txt(svg, cx, 376, name, "name", { "text-anchor": "middle", fill: "#161513", "font-size": 13.5 });
      return MODELS.map((m, j) => {
        const v = M(m.id)[src][key], bx = cx + (j - 1) * (bw + gap) - bw / 2;
        const r = barRect(svg, bx, bw, y(0), m.color);
        const t = txt(svg, bx + bw / 2, y(v.rate) - 8, pct(v.rate), "lbl", { "text-anchor": "middle", fill: "#3b3a37", "font-size": 11.5 });
        return { r, t, y: y(v.rate) };
      });
    });
    const grow = (bars, p) => bars.forEach((b, j) => { setBar(b.r, b.y, y(0), seg(p, j * .12, .6 + j * .12)); op(b.t, seg(p, .7, 1)); });
    fig(root, {
      init() { G.forEach(bars => bars.forEach(b => { setBar(b.r, b.y, y(0), 0); op(b.t, 0); })); },
      steps: [
        { label: "With accurate notes, harm is near zero", dur: 1800, draw: p => grow(G[0], p) },
        { label: "Out-of-date or missing notes raise it to between a fifth and a half", dur: 2400, draw: p => { grow(G[1], seg(p, 0, .7)); grow(G[2], seg(p, .3, 1)); } },
        { label: "When the owner says what changed, harm falls to almost nothing", dur: 2400, draw: p => { grow(G[3], seg(p, 0, .7)); grow(G[4], seg(p, .3, 1)); } },
      ],
    });
  })();

  // 6.4 Social talk, one row at a time.
  (() => {
    const ROWS = [
      ["unrelated", "Talk that is not about the task", "Close to zero: a few points either way."],
      ["pile_up", "Many messages arrive at once", "Harm falls for both GPT models; Claude Haiku barely moves."],
      ["disagreement", "Colleagues argue about checking first", "Harm falls for both GPT models."],
      ["approval", "A bystander says go ahead", "Only GPT-6 Luna changes, and harm falls."],
      ["hearsay", "A colleague claims the owner is fine with it", "The largest fall. The claim is false, yet GPT models look first more often after it."],
    ];
    const { root, svg } = frame(document.getElementById("fig-social"), {
      title: "Talk around the agent never raises harm", viewBox: "0 0 960 430", legend: nameLegend(),
      caption: "Change in harm on changed shifts when the agent hears each kind of message at the handover, against the same cases without it. The first row groups chatter, side conversations, shop talk and messages taken back.",
    });
    const x = linear(-.3, .15, 330, 660), rowY = i => 48 + i * 68;
    const band = mk("rect", { x: x(-.05), y: 22, width: x(.05) - x(-.05), height: 5 * 68 - 4, fill: "#f6f4f0" }, svg);
    xAxis(svg, x, 22, 360, [-.3, -.2, -.1, 0, .1], "Change in harm rate (percentage points)", v => (v > 0 ? "+" : "") + Math.round(v * 100));
    mk("line", { x1: x(0), x2: x(0), y1: 22, y2: 360, stroke: "#cfcac1" }, svg);
    const hl = mk("rect", { x: 0, y: 0, width: 960, height: 60, rx: 10, fill: "#f6ecbf", opacity: 0 }, svg);
    svg.insertBefore(hl, band);
    const rows = ROWS.map(([key, label, note], i) => {
      const cy = rowY(i);
      const l = txt(svg, 316, cy + 5, label, "lbl", { "text-anchor": "end", fill: "#161513", "font-size": 13.5 });
      const dots = MODELS.map((m, j) => {
        const v = M(m.id).social[key], yy = cy + (j - 1) * 12;
        const ci = mk("line", { x1: x(v.low), x2: x(v.high), y1: yy, y2: yy, stroke: m.color, "stroke-width": 1.4, opacity: 0 }, svg);
        const d = mk("circle", { cx: x(v.diff), cy: yy, r: 6, fill: m.color, stroke: "#fff", "stroke-width": 1.5 }, svg);
        const t = mk("title", {}, d); t.textContent = `${m.name}: ${(v.diff > 0 ? "+" : "") + Math.round(v.diff * 100)} points`;
        return { ci, d };
      });
      const n = mk("foreignObject", { x: 690, y: cy - 26, width: 270, height: 56 }, svg);
      n.innerHTML = `<div xmlns="http://www.w3.org/1999/xhtml" style="font:13px/1.4 Inter,sans-serif;color:#3b3935">${note}</div>`;
      return { l, dots, n, cy };
    });
    const closing = txt(svg, 690, 400, "", "name", { fill: "#161513" });
    fig(root, {
      init() { op(hl, 0); op(closing, 0); rows.forEach(r => { op(r.l, .35); op(r.n, 0); r.dots.forEach(d => { op(d.d, 0); op(d.ci, 0); }); }); },
      steps: [
        ...rows.map((r, i) => ({ label: ROWS[i][1], dur: 2600, draw: p => {
          op(hl, seg(p, 0, .2) * (i === rows.length - 1 || p < 1 ? 1 : 1) * .55);
          hl.setAttribute("y", r.cy - 30);
          rows.forEach((o, k) => { if (k < i) { op(o.n, 0); } });
          op(r.l, .35 + .65 * seg(p, 0, .3)); r.dots.forEach((d, j) => { op(d.d, seg(p, .15 + j * .12, .35 + j * .12)); op(d.ci, .4 * seg(p, .5, .8)); });
          op(r.n, seg(p, .45, .75));
        } })),
        { label: "Talk never raised harm; some of it made agents more careful", dur: 1800, draw: p => {
          op(hl, .55 * (1 - seg(p, 0, .4))); rows.forEach(o => op(o.n, 0)); closing.textContent = "Talk never raised harm."; op(closing, seg(p, .3, .8));
        } },
      ],
    });
  })();

  // 6.5 Under pressure.
  (() => {
    const SEG = [["harm", "Harmed", "#d0685c", "#ffffff"], ["asked", "Asked the owner, no harm", "#6fae86", "#ffffff"], ["quiet", "Went ahead, no harm", "#e9e6e0", "#6d6860"]];
    const { root, svg } = frame(document.getElementById("fig-pressure"), {
      title: "Under pressure, GPT models give in; Claude Haiku asks the owner", viewBox: "0 0 960 400",
      legend: SEG.map(([, l, c]) => [c, c, l]),
      caption: "What the agent did on changed shifts with and without a manager pressing for speed at the handover, in sessions without accurate notes or a word from the owner. About a hundred pressured shifts per model.",
    });
    const x = linear(0, 1, 230, 930);
    const rows = [];
    MODELS.forEach((m, i) => {
      const y0 = 20 + i * 126;
      txt(svg, 0, y0 + 14, m.name, "name", { fill: m.color, "font-size": 14 });
      ["none", "pressure"].forEach((k, j) => {
        const yy = y0 + 26 + j * 46;
        txt(svg, 216, yy + 25, k === "none" ? "No pressure" : "Under pressure", "lbl", { "text-anchor": "end", fill: k === "none" ? "#9a948a" : "#161513" });
        const v = M(m.id).pressure[k];
        let left = 0;
        const parts = SEG.map(([key, , c, tc]) => {
          const w = v[key];
          const r = mk("rect", { x: x(left), y: yy, width: 0, height: 38, fill: c }, svg);
          const t = txt(svg, x(left + w / 2), yy + 24, w >= .07 ? pct(w) : "", "lbl", { "text-anchor": "middle", fill: tc, "font-weight": 600 });
          const p = { r, t, x0: x(left) + (left > 0 ? 2 : 0), w: x(w) - x(0) - 2 };
          left += w;
          return p;
        });
        rows.push({ parts, model: i });
      });
    });
    fig(root, {
      init() { rows.forEach(r => r.parts.forEach(p => { p.r.setAttribute("x", p.x0); p.r.setAttribute("width", 0); op(p.t, 0); })); },
      steps: MODELS.map((m, i) => ({
        label: ["GPT-6 Luna: harm rises under pressure, asking dips", "GPT-5.6 Luna: harm rises, asking nearly halves", "Claude Haiku: harm holds, asking almost doubles"][i], dur: 2400,
        draw: p => rows.filter(r => r.model === i).forEach((r, k) => r.parts.forEach((part, s) => {
          const q = ease(seg(p, k * .25 + s * .12, k * .25 + s * .12 + .35)); part.r.setAttribute("width", Math.max(0, part.w * q)); op(part.t, seg(p, .75, 1));
        })),
      })),
    });
  })();

  // 6.6 The trade-off, shared with the scaling section.
  window.PARETO = function pareto(host, { title, caption, frontierSlot }) {
    const { root, svg } = frame(host, { title, viewBox: "0 0 960 600", legend: nameLegend(), caption });
    const x = linear(0, 1, 100, 560), y = linear(0, 1, 540, 80);
    yAxis(svg, y, 100, 560, [0, .25, .5, .75, 1], "Harm on changed shifts");
    xAxis(svg, x, 80, 540, [0, .25, .5, .75, 1], "False alarms on routine shifts", null, true);
    const star = mk("path", { d: "M0,-11 L3,-3.5 11,-3.5 4.6,1.4 7,9 0,4.4 -7,9 -4.6,1.4 -11,-3.5 -3,-3.5 Z", fill: "#161513", transform: `translate(${x(0)},${y(0)})` }, svg);
    const starL = txt(svg, x(0) + 16, y(0) - 10, "Ideal", "name", { fill: "#161513" });
    const side = 600;
    const defs = mk("g", {}, svg);
    txt(defs, side, 96, "An ideal agent", "name", { fill: "#161513", "font-size": 16 });
    const lines = ["Does no harm when something changed", "Raises no alarm when nothing changed", "Finishes the task"].map((s, i) => txt(defs, side, 124 + i * 24, s, "lbl", { "font-size": 14 }));
    const fdef = mk("foreignObject", { x: side, y: 200, width: 350, height: 70 }, svg);
    fdef.innerHTML = `<div xmlns="http://www.w3.org/1999/xhtml" style="font:14px/1.45 Inter,sans-serif;color:#3b3935"><b style="font-weight:600;color:#161513">The frontier</b> is the best trade-off reached so far: no agent beats it on both axes at once.</div>`;
    const note = mk("foreignObject", { x: side, y: 300, width: 350, height: 160 }, svg);
    const setNote = (html, o) => { if (note.__h !== html) { note.innerHTML = `<div xmlns="http://www.w3.org/1999/xhtml" style="font:15px/1.5 Inter,sans-serif;color:#3b3935">${html}</div>`; note.__h = html; } op(note, o); };
    const order = [1, 0, 2];
    const P = {};
    const groups = order.map(j => {
      const m = MODELS[j], T = M(m.id).tradeoff;
      const cases = Object.values(D.by_case).map(byModel => byModel[m.id]).filter(Boolean);
      const dots = cases.map(v => mk("circle", { cx: x(v.alarm), cy: y(v.harm), r: 4.5, fill: m.color, opacity: 0 }, svg));
      const big = mk("circle", { cx: x(T.alarm), cy: y(T.harm), r: 11, fill: m.color, stroke: "#fff", "stroke-width": 2.5 }, svg);
      const lab = m.id === "gpt-6-luna" ? txt(svg, x(T.alarm) - 18, y(T.harm) - 12, m.name, "name", { fill: m.color, "text-anchor": "end" }) : m.id === "claude-haiku-5-5" ? txt(svg, x(T.alarm) + 16, y(T.harm) + 26, m.name, "name", { fill: m.color }) : txt(svg, x(T.alarm) + 18, y(T.harm) + 5, m.name, "name", { fill: m.color });
      P[m.id] = [x(T.alarm), y(T.harm)];
      return { m, T, cases, dots, big, lab };
    });
    const [ax, ay] = P["gpt-6-luna"], [bx, by] = P["claude-haiku-5-5"];
    const front = mk("path", { d: `M${ax},${y(1)} L${ax},${ay} L${bx},${ay} L${bx},${by} L${x(1)},${by}`, fill: "none", stroke: "#161513", "stroke-width": 1.5, "stroke-dasharray": "5 4" }, svg);
    const slot = mk("circle", { cx: x(.1), cy: y(.08), r: 26, fill: "none", stroke: "#9a948a", "stroke-width": 1.6, "stroke-dasharray": "4 4" }, svg);
    const slotL = txt(svg, x(.1) + 34, y(.08) + 5, "Frontier models", "name", { fill: "#6d6860" });
    const NOTES = {
      "gpt-5.6-luna": "<b style='color:#eb6834'>GPT-5.6 Luna</b> finishes almost every routine shift, but harms on half of the changed ones.",
      "gpt-6-luna": "<b style='color:#2a78d6'>GPT-6 Luna</b> raises about as many alarms, with about half the harm.",
      "claude-haiku-5-5": "<b style='color:#1baf7a'>Claude Haiku 5.5</b> harms least, but raises the most alarms and finishes the fewest routine shifts.",
    };
    const done = m => Math.round(M(m).tradeoff.done * 100);
    const hd = MODELS.map(m => M(m.id).outcomes.done_given_harm);
    const lo = Math.floor(Math.min(...hd) * 100), hi = Math.floor(Math.max(...hd) * 100);
    const drop = (G, p) => {
      G.dots.forEach((d, i) => { const q = ease(seg(p, i * .02, .35 + i * .02)); op(d, .32 * q); d.setAttribute("cy", lerp(y(1) - 40, y(G.cases[i].harm), q)); });
      const q = ease(seg(p, .55, .85)); op(G.big, q); G.big.setAttribute("cy", lerp(y(1) - 40, y(G.T.harm), q)); op(G.lab, seg(p, .8, 1));
    };
    const steps = [
      { label: "What an ideal agent does", dur: 2400, draw: p => { op(star, seg(p, 0, .3)); op(starL, seg(p, .1, .4)); op(defs, 1); lines.forEach((l, i) => op(l, seg(p, .3 + i * .2, .5 + i * .2))); } },
      { label: "The frontier", dur: 1800, draw: p => op(fdef, seg(p, 0, .5)) },
      ...groups.map(G => ({ label: G.m.name, dur: 2800, draw: p => { drop(G, p); setNote(NOTES[G.m.id] + ` It finishes ${done(G.m.id)}% of routine shifts.`, seg(p, .6, .9)); } })),
      { label: "GPT-6 Luna and Claude Haiku form the frontier; GPT-5.6 Luna sits behind it", dur: 2400, draw: p => { op(front, seg(p, 0, .6)); setNote("No agent is near the ideal. GPT-6 Luna and Claude Haiku trade one failure for the other.", seg(p, .4, .8)); } },
      { label: "Harm comes with the task done", dur: 2200, draw: p => setNote(`When an agent harms, it has finished the task: ${lo} to ${hi}% of harmful shifts end with the task done. The damage is the routine, carried out in full.`, seg(p, .2, .6)) },
    ];
    if (frontierSlot) steps.push({ label: "Where will frontier models land?", dur: 2200, draw: p => { op(slot, seg(p, 0, .4)); op(slotL, seg(p, .3, .7)); setNote("Next: run frontier models on the same sessions and place them on this plot.", seg(p, .3, .7)); } });
    fig(root, {
      init() {
        [star, starL, defs, fdef, front, slot, slotL, note].forEach(e => op(e, 0)); lines.forEach(l => op(l, 0));
        groups.forEach(G => { G.dots.forEach(d => op(d, 0)); op(G.big, 0); op(G.lab, 0); });
      },
      steps,
    });
  };
  PARETO(document.getElementById("fig-pareto"), {
    title: "No agent is both safe and quiet",
    caption: "Each large dot is a model averaged over cases; each small dot is one model on one case. A false alarm is asking someone or leaving the task undone on a shift where nothing changed.",
  });
})();
