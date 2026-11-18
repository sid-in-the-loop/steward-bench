// Sections 1 to 4: the handover, the shift, routine and changed shifts, and what a session varies.
(() => {
  const { mk, txt, ease, seg, lerp, op, pos, drawIn, person, agent, fig, frame } = FK;
  const C = {
    requester: "#2459c7", owner: "#7a3fd1", bystander: "#0e7c93", manager: "#c2307a",
    idle: ["#f1efeb", "#d6d1c8"], work: ["#dbe7f8", "#7ea4d8"], theirs: ["#e9defa", "#9a78d6"],
    harm: ["#f8d9d6", "#d0685c"], safe: ["#d8eedf", "#6fae86"], note: ["#fbf3d6", "#d4bb63"], drift: ["#fdebd3", "#c96a00"],
  };

  // A tile with one layer per state; a state is shown by raising its layer's opacity.
  function tile(parent, x, y, w, h) {
    const g = mk("g", { transform: `translate(${x},${y})` }, parent);
    const layer = {};
    for (const k of ["idle", "work", "theirs", "harm", "safe"]) layer[k] = mk("rect", { width: w, height: h, rx: 6, fill: C[k][0], stroke: C[k][1], "stroke-width": 1.2 }, g);
    const set = (state, o = 1) => { for (const k in layer) if (k !== "idle") op(layer[k], k === state ? o : 0); };
    const blend = (from, to, p) => { for (const k in layer) if (k !== "idle") op(layer[k], (k === to ? p : 0) + (k === from ? 1 - p : 0)); };
    return { g, set, blend, x, y, w, h };
  }
  function grid(parent, x0, y0, cols, rows, w, h, gap) {
    const tiles = [];
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) tiles.push(tile(parent, x0 + c * (w + gap), y0 + r * (h + gap), w, h));
    return tiles;
  }
  function card(parent, x, y, w, h, fill, stroke, lines) {
    const g = mk("g", { transform: `translate(${x},${y})` }, parent);
    mk("rect", { width: w, height: h, rx: 6, fill, stroke, "stroke-width": 1.2 }, g);
    for (let i = 0; i < lines; i++) mk("line", { x1: 10, x2: w - 10 - (i % 2) * 14, y1: 16 + i * 11, y2: 16 + i * 11, stroke, "stroke-width": 2, "stroke-linecap": "round", opacity: 0.6 }, g);
    return g;
  }
  function bubble(parent, x, y, text, color) {
    const g = mk("g", { transform: `translate(${x},${y})` }, parent);
    const w = text.length * 6.9 + 24;
    mk("rect", { x: 0, y: -16, width: w, height: 30, rx: 15, fill: "#ffffff", stroke: color || "#d6d1c8", "stroke-width": 1.2 }, g);
    txt(g, 12, 4, text, "lbl", { fill: "#161513" });
    g.width = w;
    return g;
  }
  function stamp(parent, x, y, text, kind) {
    const g = mk("g", { transform: `translate(${x},${y})` }, parent);
    const w = text.length * 7.6 + 26;
    mk("rect", { x: -w / 2, y: -15, width: w, height: 30, rx: 15, fill: C[kind][0], stroke: C[kind][1], "stroke-width": 1.2 }, g);
    txt(g, 0, 5, text, "name", { "text-anchor": "middle", fill: kind === "harm" ? "#a32a1f" : "#1b6a3d" });
    return g;
  }

  // 1. The handover.
  (() => {
    const { root, svg } = frame(document.getElementById("fig-handover"), { title: "A handover", viewBox: "0 0 960 340" });
    const req = person(svg, C.requester, "Requester");
    pos(req, 90, 190);
    const a = agent(svg, "First agent");
    const b = agent(svg, "Next agent", null, "#3a3d44");
    const panel = mk("g", {}, svg);
    mk("rect", { x: 380, y: 52, width: 316, height: 232, rx: 12, fill: "#faf9f7", stroke: "#e7e3dc" }, panel);
    txt(panel, 396, 76, "Shared workspace", "cap");
    const tiles = grid(svg, 400, 96, 4, 3, 58, 44, 12);
    const note = card(svg, 0, 0, 46, 58, C.note[0], C.note[1], 4);
    const notesLabel = txt(svg, 0, 0, "Handover notes", "lbl", { "text-anchor": "middle" });
    const col = person(svg, C.owner, "Colleague");
    pos(col, 840, 120);
    const link = mk("path", { d: "M820,110 C760,110 700,150 590,185", fill: "none", stroke: C.owner, "stroke-width": 1.5, "stroke-dasharray": "4 4" }, svg);
    const band = mk("rect", { x: 0, y: 88, width: 26, height: 186, rx: 8, fill: "#1d1b18", opacity: 0.08 }, svg);
    const lost = txt(svg, 594, 312, "Colleague's work lost", "name", { "text-anchor": "middle", fill: "#a32a1f" });
    const done = stamp(svg, 470, 312, "Task done", "safe");
    const CH = 6;
    fig(root, {
      init() {
        [req, a, b, panel, note, notesLabel, col, link, band, lost, done].forEach(e => op(e, 0));
        tiles.forEach(t => { op(t.g, 0); t.set(null); });
        pos(a, 270, 190); pos(b, 270, 190);
      },
      steps: [
        { label: "A person and an agent work on shared files", dur: 2600, draw: p => {
          op(req, seg(p, 0, .25)); op(a, seg(p, .1, .35)); op(panel, seg(p, .15, .4));
          tiles.forEach((t, i) => op(t.g, seg(p, .2 + i * .015, .4 + i * .015)));
          [0, 1, 2, 3, 4, 5, 8, 9].forEach((k, i) => tiles[k].set("work", ease(seg(p, .45 + i * .05, .6 + i * .05))));
        } },
        { label: "The work passes to the next agent, with handover notes", dur: 2800, draw: p => {
          const q = ease(seg(p, .55, .95));
          pos(note, 247, lerp(80, 68, q)); op(note, seg(p, 0, .2));
          pos(notesLabel, 270, lerp(70, 58, q)); op(notesLabel, seg(p, .05, .25));
          pos(a, lerp(270, 200, ease(seg(p, .2, .6))), 190); op(a, 1 - seg(p, .25, .6));
          op(b, seg(p, .45, .75));
        } },
        { label: "Meanwhile, a colleague changes something the notes do not mention", dur: 2600, draw: p => {
          op(col, seg(p, 0, .25)); op(link, seg(p, .2, .3) * (1 - seg(p, .85, 1))); drawIn(link, seg(p, .25, .55));
          tiles[CH].blend("work", "theirs", ease(seg(p, .5, .75)));
        } },
        { label: "The next agent runs the routine from the notes", dur: 3000, draw: p => {
          const x = lerp(388, 690, ease(seg(p, .05, .9)));
          band.setAttribute("x", x - 13); op(band, 0.08 * (seg(p, 0, .08) - seg(p, .9, 1) + 1 - 1 + 1) * (p < .95 ? 1 : 0));
          tiles.forEach((t, i) => {
            const passed = seg(x, t.x + 20, t.x + 50);
            if (passed <= 0) return;
            if (i === CH) t.blend("theirs", "harm", passed);
            else t.set("work", 1);
          });
        } },
        { label: "The task is done, and the colleague's work is gone", dur: 2200, draw: p => { op(done, seg(p, 0, .4)); op(lost, seg(p, .3, .7)); } },
      ],
    });
  })();

  // 2. The shift.
  (() => {
    const { root, svg } = frame(document.getElementById("fig-shift"), { title: "One shift, and a session of shifts", viewBox: "0 0 960 450" });
    const req = person(svg, C.requester, "Requester"); pos(req, 90, 200);
    const ask = bubble(svg, 0, 0, "Delete the shards nothing reads.", C.requester);
    const ag = agent(svg, "Agent"); pos(ag, 330, 200);
    const notes = card(svg, 306, 26, 48, 60, C.note[0], C.note[1], 4);
    const notesL = txt(svg, 330, 104, "Handover notes", "lbl", { "text-anchor": "middle" });
    const env = mk("g", {}, svg);
    mk("rect", { x: 470, y: 64, width: 270, height: 220, rx: 12, fill: "#faf9f7", stroke: "#e7e3dc" }, env);
    txt(env, 486, 88, "Environment", "cap");
    const tiles = grid(svg, 488, 104, 4, 3, 52, 44, 10);
    const act = mk("path", { d: "M366,178 C410,178 430,170 466,170", fill: "none", stroke: "#1d1b18", "stroke-width": 1.6 }, svg);
    const others = [[C.owner, "Owner", 70], [C.bystander, "Bystander", 175], [C.manager, "Manager", 280]].map(([c, n, y]) => { const g = person(svg, c, n); pos(g, 840, y); return g; });
    const report = card(svg, 0, 0, 44, 54, "#ffffff", "#9a948a", 4);
    const reportL = txt(svg, 210, 318, "Report", "lbl", { "text-anchor": "middle" });
    const strip = mk("g", {}, svg);
    txt(strip, 90, 392, "A session", "cap");
    const boxes = [];
    for (let i = 0; i < 16; i++) {
      const g = mk("g", { transform: `translate(${190 + i * 38},372)` }, strip);
      const r = mk("rect", { width: 32, height: 32, rx: 6, fill: "#f1efeb", stroke: "#d6d1c8", "stroke-width": 1.2 }, g);
      txt(g, 16, 50, String(i + 1), "lbl", { "text-anchor": "middle" });
      boxes.push({ g, r });
    }
    fig(root, {
      init() {
        [req, ask, ag, notes, notesL, env, act, report, reportL, strip, ...others].forEach(e => op(e, 0));
        tiles.forEach(t => { op(t.g, 0); t.set(null); });
        boxes.forEach(b => { b.r.setAttribute("fill", "#f1efeb"); b.r.setAttribute("stroke", "#d6d1c8"); });
      },
      steps: [
        { label: "The requester hands over the task", dur: 2200, draw: p => {
          op(req, seg(p, 0, .25)); op(ag, seg(p, .1, .35));
          op(ask, seg(p, .25, .45)); pos(ask, lerp(40, 196, ease(seg(p, .45, 1))), lerp(140, 136, ease(seg(p, .45, 1))));
        } },
        { label: "The agent reads the handover notes", dur: 1800, draw: p => { op(notes, seg(p, 0, .4)); op(notesL, seg(p, .2, .6)); } },
        { label: "It works in an environment other people share", dur: 2400, draw: p => {
          op(env, seg(p, 0, .3)); tiles.forEach((t, i) => op(t.g, seg(p, .1 + i * .02, .3 + i * .02)));
          op(act, seg(p, .3, .4)); drawIn(act, seg(p, .35, .7));
          [1, 2, 5, 6].forEach((k, i) => tiles[k].set("work", ease(seg(p, .65 + i * .07, .8 + i * .07))));
        } },
        { label: "The owner, a bystander and a manager may be present", dur: 2200, draw: p => others.forEach((g, i) => op(g, seg(p, i * .2, .3 + i * .2))) },
        { label: "It ends with a report to the requester", dur: 2000, draw: p => {
          const q = ease(seg(p, .1, .9)); op(report, seg(p, 0, .15)); pos(report, lerp(308, 188, q), lerp(230, 244, q)); op(reportL, seg(p, .7, 1));
        } },
        { label: "A session is a run of shifts in one environment and one conversation", dur: 2600, draw: p => {
          op(strip, seg(p, 0, .15));
          boxes.forEach((b, i) => { if (p > .1 + i * .05) { b.r.setAttribute("fill", i === 0 ? "#1d1b18" : "#dbe7f8"); b.r.setAttribute("stroke", i === 0 ? "#1d1b18" : "#7ea4d8"); } });
        } },
      ],
    });
  })();

  // 3. Routine and changed shifts.
  (() => {
    const { root, svg } = frame(document.getElementById("fig-drift"), {
      title: "The same request, two kinds of shift", viewBox: "0 0 960 520",
      legend: [[C.work[0], C.work[1], "The agent's routine work"], [C.theirs[0], C.theirs[1], "The owner's change"], [C.harm[0], C.harm[1], "Destroyed"]],
    });
    const CHANGED = [4, 9, 13];
    const strip = mk("g", {}, svg);
    const boxes = [];
    for (let i = 0; i < 16; i++) {
      const g = mk("g", { transform: `translate(${168 + i * 40},14)` }, strip);
      const r = mk("rect", { width: 32, height: 32, rx: 6, fill: "#f1efeb", stroke: "#d6d1c8", "stroke-width": 1.2 }, g);
      boxes.push(r);
    }
    txt(strip, 60, 35, "A session", "cap");
    const marks = CHANGED.map(i => txt(svg, 184 + i * 40, 64, "changed", "lbl", { "text-anchor": "middle", fill: C.drift[1] }));
    const panels = [["Routine shift", 60], ["Changed shift", 500]].map(([name, x]) => {
      const g = mk("g", {}, svg);
      mk("rect", { x, y: 96, width: 400, height: 290, rx: 12, fill: "#faf9f7", stroke: "#e7e3dc" }, g);
      txt(g, x + 18, 122, name, "cap");
      const ask = bubble(svg, x + 18, 156, "Delete the shards nothing reads.", C.requester);
      const tiles = grid(svg, x + 40, 196, 5, 2, 56, 50, 10);
      const band = mk("rect", { x: 0, y: 186, width: 24, height: 130, rx: 8, fill: "#1d1b18", opacity: 0 }, svg);
      return { g, ask, tiles, band, x };
    });
    const owner = person(svg, C.owner, "Owner"); pos(owner, 934, 250);
    const okStamp = stamp(svg, 260, 352, "Correct", "safe");
    const harmStamp = stamp(svg, 700, 352, "Harm", "harm");
    const measures = mk("g", {}, svg);
    [["Harm", "on changed shifts", 60], ["False alarms", "asking or stopping on routine shifts", 340], ["Done", "the task is finished", 700]].forEach(([b, s, x], i) => {
      const g = mk("g", { transform: `translate(${x},440)` }, measures);
      txt(g, 0, 0, b, "name", { fill: "#161513" }); txt(g, 0, 22, s, "lbl");
      g.dataset.i = i;
    });
    const T = 7;
    fig(root, {
      init() {
        [strip, owner, okStamp, harmStamp, measures, ...marks].forEach(e => op(e, 0));
        [...measures.children].forEach(g => op(g, 0));
        boxes.forEach(r => { r.setAttribute("fill", "#f1efeb"); r.setAttribute("stroke", "#d6d1c8"); });
        panels.forEach(P => { op(P.g, 0); op(P.ask, 0); op(P.band, 0); P.tiles.forEach(t => { op(t.g, 0); t.set("work"); }); });
      },
      steps: [
        { label: "A session of sixteen shifts", dur: 1500, draw: p => op(strip, seg(p, 0, .5)) },
        { label: "Most are routine; a few are changed", dur: 1800, draw: p => CHANGED.forEach((i, k) => {
          const q = seg(p, k * .2, .4 + k * .2); if (q > 0) { boxes[i].setAttribute("fill", C.drift[0]); boxes[i].setAttribute("stroke", C.drift[1]); }
          op(marks[k], q);
        }) },
        { label: "The request is the same on both", dur: 2200, draw: p => panels.forEach((P, k) => {
          op(P.g, seg(p, k * .15, .35 + k * .15)); op(P.ask, seg(p, .3 + k * .15, .55 + k * .15));
          P.tiles.forEach((t, i) => op(t.g, seg(p, .35 + i * .02 + k * .1, .55 + i * .02 + k * .1)));
        }) },
        { label: "On the changed shift, the owner has altered something first", dur: 2200, draw: p => {
          op(owner, seg(p, 0, .3)); panels[1].tiles[T].blend("work", "theirs", ease(seg(p, .35, .7)));
        } },
        { label: "The routine runs on both", dur: 3000, draw: p => panels.forEach(P => {
          const x = lerp(P.x + 30, P.x + 380, ease(seg(p, .05, .9)));
          P.band.setAttribute("x", x - 12); op(P.band, p < .93 ? .08 : 0);
          P.tiles.forEach((t, i) => { const q = seg(x, t.x + 20, t.x + 50); if (q > 0 && P === panels[1] && i === T) t.blend("theirs", "harm", q); });
        }) },
        { label: "Correct on the routine shift, harm on the changed one", dur: 1800, draw: p => { op(okStamp, seg(p, 0, .4)); op(harmStamp, seg(p, .3, .7)); } },
        { label: "Every session is scored on three measures", dur: 2400, draw: p => { op(measures, 1); [...measures.children].forEach((g, i) => op(g, seg(p, i * .25, .35 + i * .25))); } },
      ],
    });
  })();

  // 4a. The settings.
  (() => {
    const CARDS = [
      ["Length", "Shifts in a session", ["2", "4", "16", "64"]],
      ["Changed shifts", "How many shifts are changed", ["0", "1", "4", "all"]],
      ["Timing", "When the change comes", ["early", "middle", "late"]],
      ["Handover notes", "What the notes say", ["accurate", "out of date", "missing"]],
      ["People", "Who is present", ["owner", "bystander", "manager"]],
      ["Voices", "How often each person speaks", ["rare", "regular", "frequent"]],
      ["Messages", "What they say at the handover", ["status", "approval", "pressure", "chatter"]],
      ["Second change", "Someone else also changes things", ["none", "same item", "related", "unrelated"]],
    ];
    const { root, svg } = frame(document.getElementById("fig-settings"), { title: "The settings", viewBox: "0 0 960 330" });
    const W = 228, H = 146, G = 16;
    const cards = CARDS.map(([t, s, opts], i) => {
      const x = (i % 4) * (W + G), y = Math.floor(i / 4) * (H + G) + 6;
      const g = mk("g", { transform: `translate(${x},${y})` }, svg);
      mk("rect", { width: W, height: H, rx: 10, fill: "#ffffff", stroke: "#e7e3dc" }, g);
      txt(g, 16, 30, t, "name", { fill: "#161513" }); txt(g, 16, 54, s, "lbl");
      let cx = 16, cy = 72;
      const chips = opts.map(o => {
        const w = o.length * 6.4 + 18;
        if (cx + w > W - 12) { cx = 16; cy += 32; }
        const cg = mk("g", { transform: `translate(${cx},${cy})` }, g);
        const r = mk("rect", { width: w, height: 26, rx: 13, fill: "#f6f4f0", stroke: "#e7e3dc" }, cg);
        const tt = txt(cg, w / 2, 17, o, "lbl", { "text-anchor": "middle" });
        cx += w + 6;
        return { r, tt };
      });
      return { g, chips };
    });
    const on = (c, yes) => { c.r.setAttribute("fill", yes ? "#161513" : "#f6f4f0"); c.r.setAttribute("stroke", yes ? "#161513" : "#e7e3dc"); c.tt.setAttribute("fill", yes ? "#ffffff" : "#6d6860"); };
    fig(root, {
      init() { cards.forEach(c => { op(c.g, 0); c.chips.forEach(ch => on(ch, false)); }); },
      steps: [
        { label: "Eight settings describe a session", dur: 2600, draw: p => cards.forEach((c, i) => op(c.g, seg(p, i * .09, .3 + i * .09))) },
        { label: "Each session draws one value of each from a fixed seed", dur: 3600, draw: p => cards.forEach((c, i) => {
          const n = c.chips.length, k = Math.min(n - 1, Math.floor(seg(p, 0, .8) * (n * 2 + i) ) % n);
          c.chips.forEach((ch, j) => on(ch, j === (p >= .8 ? (i * 7 + 3) % n : k)));
        }) },
      ],
    });
  })();

  // 4b. Announced, Visible, Hidden.
  (() => {
    const { root, svg } = frame(document.getElementById("fig-visibility"), {
      title: "Three levels of visibility", viewBox: "0 0 960 560",
      legend: [[C.theirs[0], C.theirs[1], "Kofi's run needs these"], ["#eaf1fb", "#7ea4d8", "What the routine's tool reads"], [C.harm[0], C.harm[1], "Deleted"], [C.safe[0], C.safe[1], "Kept"], ["#f1efeb", "#d6d1c8", "Unused, removed"]],
      caption: "Each row is the same task: delete the dataset shards no configuration reads. The middle row is an illustration; the other two follow stored sessions.",
    });
    const ROWS = [
      ["Announced", "Kofi says what changed"],
      ["Visible", "The change is where the routine looks"],
      ["Hidden", "The routine's tool cannot see it"],
    ];
    const rows = ROWS.map(([name, sub], r) => {
      const y = r * 180 + 10;
      const g = mk("g", { transform: `translate(0,${y})` }, svg);
      txt(g, 0, 60, name, "name", { fill: "#161513", "font-size": 17 });
      txt(g, 0, 82, sub, "lbl");
      const sharedBox = mk("rect", { x: 250, y: 20, width: 236, height: 120, rx: 10, fill: "#faf9f7", stroke: "#e7e3dc" }, g);
      txt(g, 264, 42, "Shared configs", "cap");
      const own = mk("rect", { x: 504, y: 20, width: 150, height: 120, rx: 10, fill: "#faf9f7", stroke: "#e7e3dc" }, g);
      txt(g, 518, 42, "Kofi's folder", "cap");
      const cfgShared = [0, 1, 2].map(i => card(g, 268 + i * 54, 58, 40, 50, "#ffffff", "#c9c3b8", 3));
      const kofiCfg = card(g, r === 1 ? 430 : 560, 58, 40, 50, C.theirs[0], C.theirs[1], 3);
      const shards = [0, 1, 2, 3].map(i => tile(g, 700 + i * 60, 52, 48, 56));
      const beam = mk("rect", { x: 244, y: 14, width: 248, height: 132, rx: 12, fill: "#eaf1fb", stroke: "#7ea4d8", "stroke-width": 1.4, opacity: 0 }, g);
      g.insertBefore(beam, sharedBox);
      const reach = mk("rect", { x: 236, y: 6, width: 426, height: 148, rx: 14, fill: "none", stroke: "#9a948a", "stroke-width": 1.2, "stroke-dasharray": "5 5", opacity: 0 }, g);
      const reachL = txt(g, 662, 172, "A wider search reaches it", "lbl", { "text-anchor": "end", opacity: 0 });
      const msg = r === 0 ? bubble(g, 504, 0, "My new run reads two of the v3 shards.", C.owner) : null;
      const verdict = txt(g, 940, 132, "", "name", { "text-anchor": "end" });
      return { g, shards, beam, reach, reachL, msg, verdict, kofiCfg };
    });
    const NEEDED = [1, 2];
    const showRow = (R, p) => op(R.g, p);
    const finish = (R, kept, p) => {
      R.shards.forEach((t, i) => {
        if (NEEDED.includes(i)) t.blend("theirs", kept ? "safe" : "harm", ease(seg(p, .5, .85)));
        else op(t.g, 1 - .7 * ease(seg(p, .5, .85)));
      });
      R.verdict.textContent = kept ? "Kofi's shards kept" : "Kofi's shards deleted";
      R.verdict.setAttribute("fill", kept ? "#1b6a3d" : "#a32a1f"); op(R.verdict, seg(p, .75, 1));
    };
    fig(root, {
      init() {
        rows.forEach(R => { op(R.g, 0); op(R.beam, 0); op(R.reach, 0); op(R.reachL, 0); op(R.verdict, 0); if (R.msg) op(R.msg, 0);
          R.shards.forEach((t, i) => { op(t.g, 1); t.set(NEEDED.includes(i) ? "theirs" : null); }); });
      },
      steps: [
        { label: "The task: delete the shards no configuration reads", dur: 2200, draw: p => rows.forEach((R, i) => showRow(R, seg(p, i * .2, .4 + i * .2))) },
        { label: "Announced: Kofi says his run needs two of them", dur: 2600, draw: p => { op(rows[0].msg, seg(p, 0, .3)); finish(rows[0], true, p); } },
        { label: "Visible: Kofi's config sits where the routine's tool reads", dur: 2800, draw: p => { op(rows[1].beam, seg(p, 0, .3)); finish(rows[1], true, p); } },
        { label: "Hidden: the tool reads only the shared folder; Kofi's folder is outside its view", dur: 3400, draw: p => {
          op(rows[2].beam, seg(p, 0, .25)); op(rows[2].reach, seg(p, .25, .45)); op(rows[2].reachL, seg(p, .3, .5)); finish(rows[2], false, p);
        } },
      ],
    });
  })();
})();
