// Shared pieces for the data figures: model names and colours, scales, axes and legends.
window.CH = (() => {
  const { mk, txt } = FK;
  const MODELS = [
    { id: "gpt-6-luna", name: "GPT-6 Luna", color: "#2a78d6" },
    { id: "gpt-5.6-luna", name: "GPT-5.6 Luna", color: "#eb6834" },
    { id: "claude-haiku-5-5", name: "Claude Haiku 5.5", color: "#1baf7a" },
  ];
  const linear = (d0, d1, r0, r1) => v => r0 + ((v - d0) / (d1 - d0)) * (r1 - r0);
  const pct = v => `${Math.round(v * 100)}%`;

  // Horizontal gridlines with percentage ticks on the left.
  function yAxis(svg, y, x0, x1, ticks, label) {
    const g = mk("g", {}, svg);
    for (const t of ticks) {
      mk("line", { x1: x0, x2: x1, y1: y(t), y2: y(t), stroke: t === 0 ? "#cfcac1" : "#efece7", "stroke-width": 1 }, g);
      txt(g, x0 - 10, y(t) + 4, pct(t), "lbl", { "text-anchor": "end" });
    }
    if (label) txt(g, x0 - 52, (y(ticks[0]) + y(ticks[ticks.length - 1])) / 2, label, "lbl", { "text-anchor": "middle", transform: `rotate(-90 ${x0 - 52} ${(y(ticks[0]) + y(ticks[ticks.length - 1])) / 2})` });
    return g;
  }

  // Vertical gridlines with ticks below.
  function xAxis(svg, x, y0, y1, ticks, label, fmt, skipFirst) {
    const g = mk("g", {}, svg);
    for (const t of ticks) {
      mk("line", { x1: x(t), x2: x(t), y1: y0, y2: y1, stroke: "#efece7", "stroke-width": 1 }, g);
      if (!(skipFirst && t === ticks[0])) txt(g, x(t), y1 + 22, (fmt || pct)(t), "lbl", { "text-anchor": "middle" });
    }
    if (label) txt(g, (x(ticks[0]) + x(ticks[ticks.length - 1])) / 2, y1 + 46, label, "lbl", { "text-anchor": "middle" });
    return g;
  }

  // A row of model swatches.
  function legend(svg, x, y, gap) {
    const g = mk("g", {}, svg);
    let cx = x;
    for (const m of MODELS) {
      mk("rect", { x: cx, y: y - 9, width: 11, height: 11, rx: 3, fill: m.color }, g);
      const t = txt(g, cx + 17, y, m.name, "lbl");
      cx += 17 + m.name.length * 7.1 + (gap || 22);
    }
    return g;
  }

  return { MODELS, linear, pct, yAxis, xAxis, legend };
})();
