/* Tiny SVG charts, hand-rolled.
 *
 * No charting library: this is a no-build front end (see ADR-008), and pulling
 * one in would mean either a bundler or a CDN dependency. The three shapes here
 * are all the console needs, and inline SVG keeps them crisp at any zoom.
 */

const NS = "http://www.w3.org/2000/svg";

function svgNode(name, attrs) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs || {})) {
    node.setAttribute(key, String(value));
  }
  return node;
}

/**
 * Vertical bars over time. `series` is [{date, <key>}, ...]; `keys` is a list of
 * {key, label, color} so two metrics can share one chart.
 */
export function barChart(series, keys, { height = 180, labelCount = 7 } = {}) {
  const width = 720;
  const padLeft = 34;
  const padBottom = 22;
  const plotW = width - padLeft - 8;
  const plotH = height - padBottom - 8;

  const max = Math.max(1, ...series.flatMap((row) => keys.map((k) => Number(row[k.key] || 0))));
  const slot = plotW / Math.max(1, series.length);
  const barW = Math.max(2, (slot - 4) / keys.length);

  const svg = svgNode("svg", {
    viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img",
    "aria-label": keys.map((k) => k.label).join("、") + " 趋势",
  });

  // Horizontal grid. The number of lines adapts to the data: a flat series of 1
  // would otherwise print "1 1 0 0" and look broken.
  const gridLines = Math.min(3, Math.max(1, Math.round(max)));
  for (let i = 0; i <= gridLines; i += 1) {
    const y = 8 + (plotH * i) / gridLines;
    svg.appendChild(svgNode("line", {
      x1: padLeft, y1: y, x2: width - 8, y2: y, class: "chart-grid",
    }));
    const value = Math.round(max - (max * i) / gridLines);
    const label = svgNode("text", { x: padLeft - 6, y: y + 4, class: "chart-axis", "text-anchor": "end" });
    label.textContent = value;
    svg.appendChild(label);
  }

  series.forEach((row, index) => {
    keys.forEach((key, kIndex) => {
      const value = Number(row[key.key] || 0);
      const h = max === 0 ? 0 : (value / max) * plotH;
      const x = padLeft + index * slot + 2 + kIndex * barW;
      const y = 8 + plotH - h;
      const bar = svgNode("rect", {
        x, y, width: barW, height: Math.max(value > 0 ? 2 : 0, h),
        rx: 2, fill: key.color, class: "chart-bar",
      });
      const title = svgNode("title", {});
      title.textContent = `${row.date} ${key.label}: ${value}`;
      bar.appendChild(title);
      svg.appendChild(bar);
    });
  });

  // x labels every N slots so they never collide
  const step = Math.max(1, Math.ceil(series.length / labelCount));
  series.forEach((row, index) => {
    if (index % step !== 0 && index !== series.length - 1) return;
    const label = svgNode("text", {
      x: padLeft + index * slot + slot / 2, y: height - 6,
      class: "chart-axis", "text-anchor": "middle",
    });
    label.textContent = row.date.slice(5);
    svg.appendChild(label);
  });

  return svg;
}

/** Legend that matches {@link barChart}. */
export function legend(keys) {
  const wrap = document.createElement("div");
  wrap.className = "legend";
  for (const key of keys) {
    const item = document.createElement("span");
    item.className = "legend-item";
    const dot = document.createElement("i");
    dot.style.background = key.color;
    item.appendChild(dot);
    item.appendChild(document.createTextNode(key.label));
    wrap.appendChild(item);
  }
  return wrap;
}

/** Donut for a small set of parts: [{label, value, color}]. */
export function donut(parts, { size = 150, thickness = 18 } = {}) {
  const total = parts.reduce((sum, p) => sum + Number(p.value || 0), 0);
  const radius = size / 2 - thickness / 2;
  const circumference = 2 * Math.PI * radius;
  const svg = svgNode("svg", {
    viewBox: `0 0 ${size} ${size}`, class: "donut", role: "img",
    "aria-label": "任务状态分布",
  });
  svg.appendChild(svgNode("circle", {
    cx: size / 2, cy: size / 2, r: radius, fill: "none",
    stroke: "#22262f", "stroke-width": thickness,
  }));

  let offset = 0;
  for (const part of parts) {
    const value = Number(part.value || 0);
    if (value <= 0 || total === 0) continue;
    const length = (value / total) * circumference;
    const arc = svgNode("circle", {
      cx: size / 2, cy: size / 2, r: radius, fill: "none",
      stroke: part.color, "stroke-width": thickness,
      "stroke-dasharray": `${length} ${circumference - length}`,
      "stroke-dashoffset": -offset,
      transform: `rotate(-90 ${size / 2} ${size / 2})`,
    });
    const title = svgNode("title", {});
    title.textContent = `${part.label}: ${value}`;
    arc.appendChild(title);
    svg.appendChild(arc);
    offset += length;
  }

  const caption = svgNode("text", {
    x: size / 2, y: size / 2 - 2, "text-anchor": "middle", class: "donut-total",
  });
  caption.textContent = total;
  svg.appendChild(caption);
  const sub = svgNode("text", {
    x: size / 2, y: size / 2 + 16, "text-anchor": "middle", class: "donut-sub",
  });
  sub.textContent = "总任务";
  svg.appendChild(sub);
  return svg;
}
