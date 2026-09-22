"use strict";
// VibeTrack dashboard. Task titles, notes and decisions are written by an AI, so they are
// untrusted text: every node here is built with textContent/createTextNode, never innerHTML.

const $ = (id) => document.getElementById(id);
const POLL_MS = 5000;
const SVG_NS = "http://www.w3.org/2000/svg";
const STATUS = [["done", "Done"], ["in_progress", "In progress"], ["blocked", "Blocked"], ["todo", "To do"]];
const STATUS_LABEL = Object.fromEntries(STATUS);

let slug = null;                  // selected project
const last = {};                  // per-section JSON of the last render, to skip no-op redraws
const toggled = new Map();        // task id -> open/closed, set only when the user clicks

// ---------- helpers ----------
/** h("div", {class: "x"}, "text", node, [more]) builds HTML; strings become text nodes. */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  el.append(...kids.flat().filter((k) => k != null && k !== false));
  return el;
}
/** Same for SVG elements. */
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  el.append(...kids);
  return el;
}
const fmtDay = (iso) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });
const hrs = (n) => `${Math.round(n * 10) / 10}h`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const utc = (stamp) => new Date(stamp.replace(" ", "T") + "Z");   // SQLite gives UTC without a zone

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || res.statusText);
  return res.json();
}
/** Run render() only if the data behind a section changed since last time. */
function section(key, data, render) {
  const json = JSON.stringify(data);
  if (last[key] === json) return;
  last[key] = json;
  render(data);
}
const empty = (text) => h("p", { class: "empty" }, text);

// ---------- hero: one plain sentence about where the project stands ----------
function headline(ov, burn) {
  const { timeline: tl, progress: pr } = ov;
  const day = `Day ${tl.days_elapsed + 1}` + (tl.planned_days ? ` of ${tl.planned_days}.` : ".");
  if (pr.leaf_tasks === 0) return `${day} No tasks planned yet.`;
  if (pr.percent_done >= 100) return `${day} Every task is done.`;
  const v = burn.variance_hours;
  if (v == null) return `${day} ${pr.percent_done}% done.`;
  if (Math.abs(v) < 0.5) return `${day} On plan.`;
  return `${day} About ${hrs(Math.abs(v))} ${v > 0 ? "behind" : "ahead of"} plan.`;
}

function summary(ov) {
  const { progress: pr, timeline: tl } = ov;
  const out = [`${pr.by_status.done} of ${pr.leaf_tasks} tasks are done (${pr.percent_done}% of the estimated work) and ${hrs(pr.hours_spent)} is logged.`];
  if (tl.deadline) {
    const d = tl.days_remaining;
    out.push(d > 0 ? `The deadline is ${fmtDay(tl.deadline)}, ${plural(d, "day")} away.`
      : d === 0 ? "The deadline is today."
      : `The deadline was ${fmtDay(tl.deadline)}, ${plural(-d, "day")} ago.`);
  }
  return out.join(" ");
}

function renderStatus(pr) {
  const bar = $("statusbar"), legend = $("status-legend");
  bar.replaceChildren(); legend.replaceChildren();
  for (const [key, label] of STATUS) {
    const n = pr.by_status[key];
    if (pr.leaf_tasks && n) {
      const seg = h("span", { class: `seg ${key}`, title: `${label}: ${n}` });
      seg.style.width = `${(100 * n) / pr.leaf_tasks}%`;
      bar.append(seg);
    }
    legend.append(h("li", {}, h("span", { class: `dot ${key}` }), `${label} `, h("b", {}, String(n))));
  }
}

// ---------- burndown (SVG) ----------
function renderBurndown(b) {
  const box = $("burndown");
  box.replaceChildren();
  if (!b.total_hours) { box.append(empty("The chart appears once tasks are planned.")); return; }

  const W = Math.max(box.clientWidth, 320), H = 270, m = { l: 46, r: 20, t: 26, b: 30 };
  const n = b.days.length - 1 || 1;
  const x = (i) => m.l + ((W - m.l - m.r) * i) / n;
  const y = (v) => H - m.b - ((H - m.t - m.b) * v) / b.total_hours;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
                         "aria-label": "Hours of work left per day, plan versus actual" });

  for (const f of [0, 0.5, 1]) {
    const v = b.total_hours * f;
    svg.append(s("line", { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: "grid" }),
               s("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, hrs(v)));
  }
  const step = Math.ceil((n + 1) / 6);
  for (let i = 0; i <= n; i += step) {
    svg.append(s("text", { x: x(i), y: H - 8, "text-anchor": i === 0 ? "start" : "middle" }, fmtDay(b.days[i])));
  }

  const t = b.today_index;
  svg.append(s("line", { x1: x(t), x2: x(t), y1: m.t, y2: H - m.b, class: "today" }),
             s("text", { x: x(t), y: m.t - 8, "text-anchor": t > n * 0.9 ? "end" : "middle" }, "today"));

  if (b.ideal) {
    svg.append(s("polyline", { class: "plan", points: b.ideal.map((v, i) => `${x(i)},${y(v)}`).join(" ") }));
    const i = Math.min(Math.floor(n / 3), b.ideal.length - 1);
    svg.append(s("text", { x: x(i) + 8, y: y(b.ideal[i]) - 8, class: "plan-label" }, "Plan"));
  }
  const pts = b.actual.map((v, i) => (v == null ? null : `${x(i)},${y(v)}`)).filter(Boolean);
  svg.append(s("polyline", { class: "actual", points: pts.join(" ") }),
             s("circle", { cx: x(t), cy: y(b.actual[t]), r: 4.5, class: "pt" }));
  const flip = x(t) > W - 80;
  svg.append(s("text", { x: x(t) + (flip ? -8 : 10), y: y(b.actual[t]) + (flip ? -10 : 4),
                         "text-anchor": flip ? "end" : "start", class: "actual-label" }, "Actual"));
  box.append(svg);
}

// ---------- bar rows ----------
function barRow(title, label, ...bars) {
  return h("div", { class: "row" },
    h("div", { class: "row-head" }, h("span", { class: "row-title", title }, title), h("span", { class: "muted num" }, label)),
    h("div", { class: "tracks" }, bars));
}
function bar(cls, pct) { const el = h("span", { class: `bar ${cls}` }); el.style.width = `${pct}%`; return el; }

function renderEpics(list) {
  const box = $("epics"); box.replaceChildren();
  if (!list.length) { box.append(empty("No tasks yet.")); return; }
  const max = Math.max(...list.flatMap((e) => [e.estimate, e.spent]), 1);
  for (const e of list) {
    const over = e.estimate > 0 && e.spent > e.estimate;
    box.append(barRow(e.title, `${hrs(e.spent)} of ${hrs(e.estimate)}${over ? ", over" : ""}`,
      bar("est", (100 * e.estimate) / max), bar(over ? "spent over" : "spent", (100 * e.spent) / max)));
  }
}

function renderTypes(list) {
  const box = $("types"); box.replaceChildren();
  if (!list.length) { box.append(empty("No tasks yet.")); return; }
  for (const t of list) {
    box.append(barRow(t.type, `${t.done} of ${t.total} done`, bar(t.done === t.total ? "share all" : "share", (100 * t.done) / t.total)));
  }
}

// ---------- activity (SVG stacked columns) ----------
function renderActivity(a) {
  const box = $("activity"), legend = $("activity-legend");
  box.replaceChildren(); legend.replaceChildren();
  const totals = a.days.map((_, i) => a.series.reduce((sum, ser) => sum + ser.counts[i], 0));
  const max = Math.max(...totals, 0);
  if (!max) { box.append(empty("No changes in the last 14 days.")); return; }

  const W = Math.max(box.clientWidth, 320), H = 150, m = { l: 30, r: 0, t: 8, b: 24 };
  const slot = (W - m.l - m.r) / a.days.length;
  const y = (v) => H - m.b - ((H - m.t - m.b) * v) / max;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
                         "aria-label": "Tracker changes per day by who made them" });
  for (const v of [0, max]) {
    svg.append(s("line", { x1: m.l, x2: W, y1: y(v), y2: y(v), class: "grid" }),
               s("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, String(v)));
  }
  a.days.forEach((d, i) => {
    let base = 0;
    a.series.forEach((ser, k) => {
      const c = ser.counts[i];
      if (c) svg.append(s("rect", { x: m.l + i * slot + 2, y: y(base + c), width: Math.max(slot - 4, 2), height: y(base) - y(base + c), class: `a${k % 5}` }));
      base += c;
    });
    if (i % 3 === 0) svg.append(s("text", { x: m.l + i * slot + slot / 2, y: H - 6, "text-anchor": "middle" }, fmtDay(d)));
  });
  box.append(svg);
  a.series.forEach((ser, k) => legend.append(h("li", {}, h("span", { class: `dot a${k % 5}` }), ser.actor)));
}

// ---------- task lists ----------
function taskItem(t) {
  return h("li", {}, h("span", {}, t.title),
    t.urgent ? h("span", { class: "tag" }, "Urgent") : null,
    t.path ? h("span", { class: "muted path" }, t.path) : null,
    t.estimate_hours != null ? h("span", { class: "muted num" }, hrs(t.estimate_hours)) : null);
}
function fill(id, items, emptyText) {
  const el = $(id); el.replaceChildren();
  if (items.length) el.append(...items.map(taskItem));
  else el.append(h("li", { class: "muted" }, emptyText));
}

// Leaf counts under a node: [done, total]
function leafCount(t) {
  if (!t.children.length) return [t.status === "done" ? 1 : 0, 1];
  return t.children.map(leafCount).reduce((a, c) => [a[0] + c[0], a[1] + c[1]], [0, 0]);
}
function treeNode(t) {
  const title = h("span", {}, t.title);
  if (!t.children.length) {
    return h("li", {}, title, h("span", { class: "meta" }, h("span", { class: `dot ${t.status}` }), STATUS_LABEL[t.status],
      t.estimate_hours != null ? h("span", { class: "num" }, ` ${hrs(t.estimate_hours)}`) : null));
  }
  const [done, total] = leafCount(t);
  const summ = h("summary", {}, title, h("span", { class: "meta" }, `${done} of ${total} done`));
  const det = h("details", {}, summ, h("ul", { class: "tree" }, t.children.map(treeNode)));
  det.open = toggled.has(t.id) ? toggled.get(t.id) : done < total;      // finished branches start closed
  summ.addEventListener("click", () => toggled.set(t.id, !det.open));   // runs before the browser toggles
  return h("li", {}, det);
}

function decisionNode(d) {
  const kids = d.children.length ? h("ul", { class: "tree" }, d.children.map(decisionNode)) : null;
  return h("li", {}, h("p", { class: "q" }, d.question),
    h("p", { class: "detail" }, `Chose: ${d.chosen}`),
    d.rationale ? h("p", { class: "detail" }, `Why: ${d.rationale}`) : null,
    d.options.length ? h("p", { class: "detail" }, `Options: ${d.options.join(", ")}`) : null,
    d.consequences ? h("p", { class: "detail" }, `Outcome: ${d.consequences}`) : null, kids);
}

// ---------- page ----------
function render(ov, ch, tree, dec) {
  $("headline").textContent = headline(ov, ch.burndown);
  $("summary").textContent = summary(ov);
  const rot = ov.ai_rotation;
  $("rotation").textContent = rot.tools.length > 1
    ? `Working with ${rot.current}. If it hits a limit: ${rot.tools[(rot.cursor + 1) % rot.tools.length]} next.`
    : `Working with ${rot.current}.`;
  section("status", ov.progress, renderStatus);
  section("burn", ch.burndown, renderBurndown);
  section("epics", ch.epics, renderEpics);
  section("types", ch.by_type, renderTypes);
  section("activity", ch.activity, renderActivity);
  section("now", ov.in_progress, (v) => fill("now", v, "Nothing in progress."));
  section("next", ov.next_up, (v) => fill("next", v, "The queue is empty."));
  section("blocked", ov.blocked, (v) => { $("blocked-box").hidden = !v.length; fill("blocked", v, ""); });
  section("tree", tree, (v) => {
    const el = $("tree"); el.replaceChildren();
    el.append(...(v.length ? v.map(treeNode) : [h("li", { class: "muted" }, "No tasks yet. The AI plans them after you give it the timeline.")]));
  });
  section("decisions", dec, (v) => {
    const el = $("decisions"); el.replaceChildren();
    el.append(...(v.length ? v.map(decisionNode) : [h("li", { class: "muted" }, "No decisions recorded yet.")]));
  });
  section("people", ov.contributors, (v) => {
    const el = $("contributors"); el.replaceChildren();
    el.append(...v.map((c) => h("li", {}, h("b", {}, c.actor), h("span", { class: "muted" }, `${plural(c.actions, "change")}, last active ${utc(c.last_active).toLocaleString()}`))));
  });
}

function fillPicker(list) {
  section("projects", list, (v) => {
    const sel = $("project"); sel.replaceChildren();
    v.forEach((p) => sel.append(h("option", { value: p.slug }, `${p.name} (${p.percent_done}%)`)));
    sel.value = slug;
  });
}

async function refresh() {
  try {
    const list = await api("/api/projects");
    $("empty").hidden = list.length > 0;
    $("app").hidden = list.length === 0;
    if (!list.length) return;
    const wanted = decodeURIComponent(location.hash.slice(1));
    if (!list.some((p) => p.slug === slug)) slug = list.some((p) => p.slug === wanted) ? wanted : list[0].slug;
    fillPicker(list);
    const base = `/api/projects/${encodeURIComponent(slug)}`;
    const [ov, ch, tree, dec] = await Promise.all(["overview", "charts", "tasks", "decisions"].map((p) => api(`${base}/${p}`)));
    render(ov, ch, tree, dec);
    $("error").hidden = true;
    $("updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    $("error").textContent = `Can't load data: ${e.message}. Is "python -m vibetrack.dashboard" still running?`;
    $("error").hidden = false;
  }
}

$("project").addEventListener("change", (e) => {
  slug = e.target.value; location.hash = encodeURIComponent(slug);
  Object.keys(last).forEach((k) => delete last[k]); toggled.clear(); refresh();
});
async function getHandoffMarkdown() {
  return (await api(`/api/projects/${encodeURIComponent(slug)}/handoff`)).markdown;
}
function flash(btn, label, text, ms = 3500) {
  btn.textContent = text;
  setTimeout(() => { btn.textContent = label; }, ms);
}
$("download-handoff").addEventListener("click", async (e) => {
  const btn = e.currentTarget, label = "Download HANDOFF.md";
  try {
    const markdown = await getHandoffMarkdown();
    const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown" }));
    const a = h("a", { href: url, download: `${slug}-handoff.md` });
    document.body.append(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    flash(btn, label, "Downloaded. Give the file to the new AI.");
  } catch {
    flash(btn, label, "Download failed. Run python -m vibetrack.handoff in a terminal instead.");
  }
});
$("copy-handoff").addEventListener("click", async (e) => {
  const btn = e.currentTarget, label = "Copy instead";
  try {
    await navigator.clipboard.writeText(await getHandoffMarkdown());
    flash(btn, label, "Copied. Paste it into the new AI.");
  } catch {
    flash(btn, label, "Copy failed. Run python -m vibetrack.handoff in a terminal instead.");
  }
});
let resizeTimer;
window.addEventListener("resize", () => {          // SVG charts are sized in pixels, so redraw them
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { delete last.burn; delete last.activity; refresh(); }, 150);
});

refresh();
setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
