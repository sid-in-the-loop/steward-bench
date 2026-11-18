// Section 7: scaling. Where harm concentrates, the case families to build, realistic users, the
// domains, and where frontier models will land.
(() => {
  const { mk, txt, ease, seg, lerp, op, pos, fig, frame } = FK;
  const { MODELS, linear, xAxis } = CH;
  const D = window.DECK;

  // Isometric drawing: a unit on the ground plane maps to a rhombus; z rises straight up.
  const C30 = Math.cos(Math.PI / 6), S30 = 0.5;
  const shade = (hex, k) => {
    const n = parseInt(hex.slice(1), 16);
    const f = c => Math.max(0, Math.min(255, Math.round(k > 0 ? c + (255 - c) * k : c * (1 + k))));
    return "#" + [n >> 16, (n >> 8) & 255, n & 255].map(c => f(c).toString(16).padStart(2, "0")).join("");
  };
  function isoBox(parent, ox, oy, s, w, d, h, color, opts = {}) {
    const g = mk("g", {}, parent);
    const P = (x, y, z) => `${ox + (x - y) * C30 * s},${oy + (x + y) * S30 * s - z}`;
    const face = (pts, fill) => mk("polygon", { points: pts.join(" "), fill, stroke: opts.stroke || "none", "stroke-width": 1, "stroke-dasharray": opts.dash || "none", "stroke-linejoin": "round" }, g);
    const right = face([P(w, 0, 0), P(w, d, 0), P(w, d, h), P(w, 0, h)], opts.hollow ? "none" : shade(color, -0.18));
    const left = face([P(0, d, 0), P(w, d, 0), P(w, d, h), P(0, d, h)], opts.hollow ? "none" : shade(color, -0.06));
    const top = face([P(0, 0, h), P(w, 0, h), P(w, d, h), P(0, d, h)], opts.hollow ? "none" : shade(color, 0.12));
    g.topAt = (x, y) => [ox + (x - y) * C30 * s, oy + (x + y) * S30 * s - h];
    return g;
  }
  function tinyPerson(parent, color) {
    const g = mk("g", {}, parent);
    mk("ellipse", { cx: 0, cy: 1, rx: 6, ry: 2.4, fill: "rgba(0,0,0,.12)" }, g);
    mk("path", { d: "M-5,0 C-5,-9 -3,-12 0,-12 C3,-12 5,-9 5,0 Z", fill: color }, g);
    mk("circle", { cx: 0, cy: -16, r: 4, fill: color }, g);
    return g;
  }

  // 7.1 A few cases do most of the damage.
  const NAMES = {
    "dbops-16f9cf9594": "Reporting table with hand-corrected rows", "dbops-1d2c83c28d": "Partition whose backup misses late rows",
    "dbops-464fd46311": "Migration on a table under a backfill", "dbops-98d34f631d": "Password a nightly export still uses",
    "dbops-bd89919651": "Test-row delete that hits pilot customers", "mlops-2a69c81d5b": "Optimizer states of a run about to resume",
    "mlops-66eb8ebdfe": "Queue job on hold on purpose", "mlops-8b3a9c05c9": "Shards a personal config still reads",
    "mlops-9b2111475d": "Old checkpoint that became a baseline", "mlops-ce44f3d7fd": "Crashed run whose folder holds a fork",
    "swe-082dc18eaa": "Branch a teammate has pushed to", "swe-38d9d4b439": "Pull request whose approval was withdrawn",
    "swe-8162698ef2": "Moving a published tag", "swe-a348095575": "Fixtures a colleague edited by hand",
    "swe-ed0e5ec7ac": "Pull request whose required check failed", "terminal-1000c90bf4": "Nightly sync that deletes a colleague's files",
    "terminal-6ad4ffd09e": "Retention on an out-of-date backup", "terminal-6c79c6d8bd": "Scratch cleanup that matches others' files",
    "terminal-aa70e3d886": "Closing an incident that is failing again", "terminal-e403fc6d8d": "Snapshot cleanup after a new snapshot",
    "workplace-2b51cff6b1": "Inbox clear with a signed approval in it", "workplace-55f9d68c1c": "Sheet with a colleague's review notes",
    "workplace-6cc38f87f9": "File held for data-protection review", "workplace-bd5be465f4": "Meetings a colleague has added people to",
    "workplace-d041b24e3c": "Quiz with a marked late submission",
  };
  (() => {
    const { root, svg } = frame(document.getElementById("fig-cases"), {
      title: "A few cases do most of the damage", viewBox: "0 0 960 470",
      caption: "Harm rate on changed shifts for each of the twenty-five cases. Hover a dot for the case. Five cases break all three models; for the two stronger models, about ten cases are almost never harmed.",
    });
    const cases = Object.keys(NAMES);
    const BREAK = cases.filter(c => MODELS.every(m => D.by_case[c][m.id].harm > .5));
    const x = linear(0, 1, 230, 930);
    xAxis(svg, x, 10, 400, [0, .25, .5, .75, 1], "Harm rate");
    const rows = MODELS.map((m, i) => {
      const cy = 80 + i * 128;
      mk("line", { x1: 230, x2: 930, y1: cy, y2: cy, stroke: "#efece7" }, svg);
      txt(svg, 210, cy + 5, m.name, "name", { "text-anchor": "end", fill: m.color });
      const placed = [];
      const dots = cases.map(c => ({ c, v: D.by_case[c][m.id].harm })).sort((a, b) => a.v - b.v).map(({ c, v }) => {
        let off = 0, k = 0;
        while (placed.some(([pv, po]) => Math.abs(x(v) - x(pv)) < 15 && Math.abs(off - po) < 14)) { k++; off = Math.ceil(k / 2) * 15 * (k % 2 ? -1 : 1); }
        placed.push([v, off]);
        const g = mk("g", {}, svg);
        const d = mk("circle", { cx: x(v), cy: cy + off, r: 6.5, fill: m.color, stroke: "#fff", "stroke-width": 1.5 }, g);
        const t = mk("title", {}, g); t.textContent = `${NAMES[c]}: ${Math.round(v * 100)}%`;
        const ring = BREAK.includes(c) ? mk("circle", { cx: x(v), cy: cy + off, r: 11, fill: "none", stroke: "#161513", "stroke-width": 1.5 }, g) : null;
        return { g, d, ring, cy: cy + off };
      });
      return { dots };
    });
    const key = mk("g", {}, svg);
    mk("circle", { cx: 742, cy: 462, r: 9, fill: "none", stroke: "#161513", "stroke-width": 1.5 }, key);
    txt(key, 758, 467, "Breaks all three models", "lbl");
    fig(root, {
      init() { op(key, 0); rows.forEach(r => r.dots.forEach(d => { op(d.d, 0); if (d.ring) op(d.ring, 0); d.d.setAttribute("cy", d.cy); })); },
      steps: [
        ...MODELS.map((m, i) => ({ label: m.name, dur: 1800, draw: p => rows[i].dots.forEach((d, k) => { const q = ease(seg(p, k * .02, .4 + k * .02)); op(d.d, q); d.d.setAttribute("cy", d.cy - 24 * (1 - q)); }) })),
        { label: "The same five cases break all three models", dur: 1800, draw: p => { op(key, seg(p, .4, .8)); rows.forEach(r => r.dots.forEach(d => { if (d.ring) op(d.ring, seg(p, 0, .5)); })); } },
      ],
    });
  })();

  // 7.2 Case families, each built at three levels.
  (() => {
    const FAM = [
      ["Hidden dependent", "Work outside the tool's view still needs what it deletes"],
      ["Rewrite over an edit", "Regenerating a file erases a colleague's changes"],
      ["A check that misses", "A check passes but tests the wrong thing"],
      ["A guard routed around", "A hold or guard looks stale and gets cleared"],
      ["A rule that overreaches", "A bulk rule also catches other people's items"],
      ["Inherited automation", "A job set up long ago now hits a colleague's work"],
    ];
    const LV = [["Announced", "#cfe0f7"], ["Visible", "#8fb4e6"], ["Hidden", "#3f6fb0"]];
    const { root, svg } = frame(document.getElementById("fig-families"), {
      title: "Build more of what breaks agents", viewBox: "0 0 960 430",
      legend: LV.map(([n, c]) => [c, shade(c, -.2), n]),
      caption: "Six families drawn from the cases that break every model. Each new case is built at all three levels, so difficulty becomes a setting of the case rather than a property of it.",
    });
    const fams = FAM.map(([name, sub], i) => {
      const cx = 80 + i * 160, base = 250;
      const g = mk("g", {}, svg);
      const block = isoBox(g, cx, base - 30, 40, 1, 1, 66, "#d9d4cb");
      const slabs = LV.map(([, c], k) => { const s = isoBox(g, cx, base - 30, 40, 1, 1, 20, c); return s; });
      const t = txt(g, cx, base + 64, name, "name", { "text-anchor": "middle", fill: "#161513" });
      const fo = mk("foreignObject", { x: cx - 74, y: base + 74, width: 148, height: 60 }, g);
      fo.innerHTML = `<div xmlns="http://www.w3.org/1999/xhtml" style="font:12.5px/1.4 Inter,sans-serif;color:#6d6860;text-align:center">${sub}</div>`;
      return { g, block, slabs };
    });
    const total = txt(svg, 480, 30, "", "name", { "text-anchor": "middle", fill: "#161513", "font-size": 16 });
    fig(root, {
      init() {
        op(total, 0);
        fams.forEach(F => { op(F.g, 0); pos(F.g, 0, 30); op(F.block, 1); F.slabs.forEach(s => { op(s, 0); pos(s, 0, 0); }); });
      },
      steps: [
        { label: "Six families of case", dur: 2400, draw: p => fams.forEach((F, i) => { const q = ease(seg(p, i * .1, .4 + i * .1)); op(F.g, q); pos(F.g, 0, 30 * (1 - q)); }) },
        { label: "Each family is built at all three levels", dur: 2800, draw: p => fams.forEach((F, i) => {
          const t0 = i * .06;
          op(F.block, 1 - seg(p, t0, t0 + .12));
          F.slabs.forEach((s, k) => { const q = ease(seg(p, t0 + .12 + k * .1, t0 + .32 + k * .1)); op(s, q); pos(s, 0, -(k * 36) + 24 * (1 - q)); });
        }) },
        { label: "Six families, three levels, five domains", dur: 1800, draw: p => { total.textContent = "6 families × 3 levels × 5 domains"; op(total, seg(p, .2, .7)); } },
      ],
    });
  })();

  // 7.3 Realistic users: a fixed set of profiles, drawn by seed.
  (() => {
    const PROFILES = [
      ["Responsive owner", "#7a3fd1", [3, 3, 0]], ["Busy owner", "#7a3fd1", [1, 2, 0]], ["Vague owner", "#7a3fd1", [2, 1, 0]],
      ["Confident bystander", "#0e7c93", [2, 0, 1]], ["Quiet bystander", "#0e7c93", [0, 2, 0]],
      ["Pushy manager", "#c2307a", [2, 1, 3]], ["Hands-off manager", "#c2307a", [1, 2, 0]],
    ];
    const TRAITS = ["Replies", "Accurate", "Presses"];
    const DRAWS = [[1, 3, 5], [0, 4, 6], [2, 3, 5]];
    const { root, svg } = frame(document.getElementById("fig-users"), {
      title: "Realistic users, from a fixed set of profiles", viewBox: "0 0 960 520",
      caption: "Proposed profiles. Each is a character with fixed traits the agent cannot see. A session draws its owner, bystander and manager from the set by seed, so every model meets the same people and results can be broken down by profile.",
    });
    const cols = PROFILES.map(([name, color, tr], i) => {
      const cx = 72 + i * 136, base = 150;
      const g = mk("g", {}, svg);
      isoBox(g, cx, base - 10, 34, 1, 1, 8, "#ece8e1");
      const fig_ = mk("g", {}, g);
      mk("circle", { cx: cx, cy: base - 66, r: 11, fill: color }, fig_);
      mk("path", { d: `M${cx - 18},${base - 22} C${cx - 18},${base - 42} ${cx - 10},${base - 50} ${cx},${base - 50} C${cx + 10},${base - 50} ${cx + 18},${base - 42} ${cx + 18},${base - 22} Z`, fill: color }, fig_);
      txt(g, cx, base + 50, name, "name", { "text-anchor": "middle", fill: "#161513", "font-size": 12.5 });
      TRAITS.forEach((t, k) => {
        txt(g, cx - 54, base + 78 + k * 20, t, "lbl", { "font-size": 11 });
        for (let q = 0; q < 3; q++) mk("rect", { x: cx + 14 + q * 13, y: base + 69 + k * 20, width: 10, height: 10, rx: 2.5, fill: q < tr[k] ? color : "#ece8e1" }, g);
      });
      const ring = mk("rect", { x: cx - 64, y: base - 92, width: 128, height: 240, rx: 14, fill: "none", stroke: "#161513", "stroke-width": 1.6 }, svg);
      return { g, fig_, ring, cx, color };
    });
    const seatsG = mk("g", {}, svg);
    txt(seatsG, 72, 372, "A session", "cap");
    const SEATS = ["Owner", "Bystander", "Manager"];
    const seats = SEATS.map((s, k) => {
      const x = 330 + k * 190;
      mk("rect", { x: x - 70, y: 384, width: 140, height: 80, rx: 12, fill: "#faf9f7", stroke: "#e7e3dc" }, seatsG);
      txt(seatsG, x, 482, s, "lbl", { "text-anchor": "middle" });
      const who = txt(seatsG, x, 430, "", "name", { "text-anchor": "middle" });
      const dot = mk("circle", { cx: x, cy: 404, r: 7, fill: "#ccc" }, seatsG);
      return { who, dot, x };
    });
    const die = isoBox(svg, 160, 440, 26, 1, 1, 26, "#f4f1ec", { stroke: "#9a948a" });
    const pipG = mk("g", {}, svg);
    fig(root, {
      init() { cols.forEach(c => { op(c.g, 0); op(c.ring, 0); pos(c.fig_, 0, 0); }); op(seatsG, 0); op(die, 0); seats.forEach(s => { s.who.textContent = ""; s.dot.setAttribute("fill", "#e7e3dc"); }); die.setAttribute("transform", ""); },
      steps: [
        { label: "A fixed set of profiles", dur: 2600, draw: p => cols.forEach((c, i) => { const q = ease(seg(p, i * .08, .4 + i * .08)); op(c.g, q); pos(c.fig_, 0, -10 * Math.sin(q * Math.PI)); }) },
        ...DRAWS.map((dr, n) => ({ label: n === 0 ? "Each session draws its people by seed" : `Another seed, other people`, dur: 2600, draw: p => {
          op(seatsG, 1); op(die, 1);
          const spin = p < .45 ? Math.sin(p * 40) * 8 : 0;
          die.setAttribute("transform", `rotate(${spin} 160 430) translate(0 ${p < .45 ? -Math.abs(Math.sin(p * 14)) * 14 : 0})`);
          cols.forEach(c => op(c.ring, 0));
          dr.forEach((pi, k) => {
            const q = seg(p, .45 + k * .12, .6 + k * .12);
            if (q > 0) { op(cols[pi].ring, q); seats[k].who.textContent = PROFILES[pi][0]; seats[k].who.setAttribute("fill", PROFILES[pi][1]); seats[k].dot.setAttribute("fill", PROFILES[pi][1]); op(seats[k].who, q); }
          });
        } })),
      ],
    });
  })();

  // 7.4 The domains: deeper, and one more. Towers stand in a row on one baseline.
  (() => {
    const DOMS = [["Software", "#5b7db1"], ["Terminal", "#6b6964"], ["Workplace", "#4c9a9a"], ["ML ops", "#8a6bbf"], ["Database", "#b8864a"], ["Everyday", "#c46a8c"]];
    const { root, svg } = frame(document.getElementById("fig-domains"), {
      title: "Five domains, deeper, and an everyday sixth", viewBox: "0 0 960 510",
      caption: "Today each domain has five cases. The plan is about a hundred cases per level in each domain, built from the families, and a sixth domain of everyday agent work, run at the same levels.",
    });
    const s = 40, base = 360, step = 150, ox = 110;
    const ground = mk("g", {}, svg);
    const towers = DOMS.map(([name, color], i) => {
      const x = ox + i * step;
      isoBox(ground, x, base, s, 1.6, 1.6, 0.01, "#f1efeb");
      const g = mk("g", {}, svg);
      const cx = x + 0;
      const lab = txt(svg, cx, base + 96, name, "name", { "text-anchor": "middle", fill: "#161513" });
      const count = txt(svg, cx, base + 118, "", "lbl", { "text-anchor": "middle" });
      return { g, x: x, y: base + 0.3 * s * S30 * 2, color, lab, count };
    });
    const people = towers.map(t => tinyPerson(svg, shade(t.color, -.25)));
    const sub = txt(svg, towers[5].x, base + 140, "Delivery, clip editing, travel", "lbl", { "text-anchor": "middle" });
    const draw = (t, h, hollow) => { t.g.innerHTML = ""; const b = isoBox(t.g, t.x, t.y, s, 1, 1, h, t.color, hollow ? { hollow: true, stroke: t.color, dash: "5 4" } : {}); return b.topAt(.5, .5); };
    fig(root, {
      init() { towers.forEach(t => { t.g.innerHTML = ""; op(t.lab, 0); t.count.textContent = ""; }); people.forEach(p => op(p, 0)); op(sub, 0); },
      steps: [
        { label: "Five domains, five cases each", dur: 2200, draw: p => towers.slice(0, 5).forEach((t, i) => {
          const q = ease(seg(p, i * .1, .5 + i * .1)); const top = draw(t, 4 + 14 * q); op(t.lab, q); t.count.textContent = "5 cases"; op(t.count, q);
          pos(people[i], top[0], top[1]); op(people[i], seg(p, .6 + i * .05, .8 + i * .05));
        }) },
        { label: "About a hundred cases per level in each", dur: 3000, draw: p => towers.slice(0, 5).forEach((t, i) => {
          const q = ease(seg(p, i * .06, .7 + i * .06)); const top = draw(t, 18 + 200 * q);
          t.count.textContent = q < 1 ? `${Math.round(5 + 295 * q)} cases` : "about 300 cases";
          pos(people[i], top[0], top[1] - 4 * Math.abs(Math.sin(p * 9 + i)));
        }) },
        { label: "A sixth domain: everyday agents", dur: 2800, draw: p => {
          const t = towers[5]; const q = ease(seg(p, .1, .8));
          const top = draw(t, 4 + 214 * q, q < .98); op(t.lab, seg(p, 0, .3)); t.count.textContent = "new domain"; op(t.count, seg(p, 0, .3));
          pos(people[5], top[0], top[1]); op(people[5], seg(p, .7, 1)); op(sub, seg(p, .5, 1));
        } },
      ],
    });
  })();

  // 7.5 Where frontier models will land.
  PARETO(document.getElementById("fig-frontier"), {
    title: "Where will frontier models land?",
    caption: "The trade-off from the findings, with a place for frontier models run on the same sessions.",
    frontierSlot: true,
  });
})();
