/* HA Fleet Hub — frontend (vanilla JS, relatieve URLs zodat HA-ingress werkt) */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const grid = $("#grid");
const modalRoot = $("#modal-root");

const ICONS = {
  refresh: '<svg viewBox="0 0 24 24" class="ico"><path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  backup: '<svg viewBox="0 0 24 24" class="ico"><path d="M4 7h16M6 7v12h12V7M12 10v6m0 0-2.4-2.4M12 16l2.4-2.4M8 4h8l1 3H7Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  update: '<svg viewBox="0 0 24 24" class="ico"><path d="M12 17V7m0 0-4 4m4-4 4 4M4 21h16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  restart: '<svg viewBox="0 0 24 24" class="ico"><path d="M12 3v7m5.2-5A8 8 0 1 1 6.8 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  close: '<svg viewBox="0 0 24 24" class="ico"><path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  detail: '<svg viewBox="0 0 24 24" class="ico"><path d="M4 6h16M4 12h16M4 18h10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  trash: '<svg viewBox="0 0 24 24" class="ico"><path d="M5 7h14M9 7V5h6v2m-8 0 1 13h8l1-13M10 11v6m4-6v6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  spinner: '<svg viewBox="0 0 24 24" class="ico spin"><path d="M12 3a9 9 0 1 0 9 9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  warn: '<svg viewBox="0 0 24 24" class="ico"><path d="M12 9v4m0 4h.01M10.3 3.9 2.5 18a1.6 1.6 0 0 0 1.4 2.4h16.2a1.6 1.6 0 0 0 1.4-2.4L13.7 3.9a1.6 1.6 0 0 0-2.8 0Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  key: '<svg viewBox="0 0 24 24" class="ico"><path d="M15 7a4 4 0 1 1-4 4M11 11 3 19v2h2l1-1v-1h1l1-1v-1l2-2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: {"Content-Type": "application/json"},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function relAge(iso) {
  if (!iso) return "onbekend";
  const h = (Date.now() - new Date(iso).getTime()) / 36e5;
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} min geleden`;
  if (h < 48) return `${Math.round(h)} uur geleden`;
  return `${Math.round(h / 24)} dagen geleden`;
}

function toast(msg, kind = "", icon = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = icon + esc(msg);
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 6000);
  return el;
}

/* ---------- kaarten ---------- */

let instances = [];

function updBadges(h) {
  const upd = [];
  if (h.core?.update) upd.push(`Core ${h.core.latest}`);
  if (h.os?.update) upd.push(`OS ${h.os.latest}`);
  if (h.supervisor?.update) upd.push(`Supervisor ${h.supervisor.latest}`);
  return upd;
}

/* Gezondheids-score 0-100 + status uit alle signalen van een online instantie */
function healthScore(h) {
  if (!h.online) return { pct: 0, status: "off" };
  let score = 100;
  if (h.core?.update) score -= 12;
  if (h.os?.update) score -= 6;
  if (h.supervisor?.update) score -= 6;
  if (h.addons?.updates?.length) score -= Math.min(12, h.addons.updates.length * 4);
  if (h.entities) score -= Math.min(24, h.entities.pct * 1.2);
  if (h.host && h.host.disk_pct > 85) score -= (h.host.disk_pct - 85);
  if (h.healthy === false) score -= 20;
  if (h.backup?.newest_date) {
    const days = (Date.now() - new Date(h.backup.newest_date).getTime()) / 864e5;
    if (days > 8) score -= Math.min(16, days - 8);
  } else if (h.backup) score -= 16;
  score = Math.max(4, Math.min(100, Math.round(score)));
  const status = score >= 85 ? "ok" : score >= 60 ? "warn" : "bad";
  return { pct: score, status };
}

/* SVG donut-ring (r=22, omtrek ~138) — via var(--stat) gekleurd */
function ringSVG(pct) {
  const r = 22, c = 2 * Math.PI * r, off = c * (1 - pct / 100);
  return `<div class="ring" role="img" aria-label="Gezondheidsscore ${pct} van 100">
    <svg viewBox="0 0 52 52" aria-hidden="true">
      <circle class="track" cx="26" cy="26" r="${r}" fill="none" stroke-width="4"/>
      <circle class="prog" cx="26" cy="26" r="${r}" fill="none" stroke-width="4"
        stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${c.toFixed(1)}"
        data-off="${off.toFixed(1)}"/>
    </svg>
    <span class="lbl" data-count="${pct}">0</span>
  </div>`;
}

/* getal soepel laten oplopen naar target */
function countUp(el, target, dur = 900) {
  const start = performance.now(), from = parseFloat(el.textContent) || 0;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { el.textContent = target; return; }
  const step = t => {
    const k = Math.min(1, Math.max(0, (t - start) / dur)), e = 1 - Math.pow(1 - k, 3);
    el.textContent = Math.round(from + (target - from) * e);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function card(h) {
  const el = document.createElement("div");
  const hs = healthScore(h);
  el.className = `card stat-${hs.status}`;
  el.dataset.name = h.name;

  const dot = h.online ? "on" : (h.error === "geen token" ? "warn" : "off");
  let body = "";

  const tunnelRow = h.kind === "agent"
    ? `<div class="row"><span class="k">Tunnel</span><span class="v">${h.agent_connected
        ? `<strong style="color:var(--green)">verbonden</strong>${h.agent_since ? ` · sinds ${esc(relAge(h.agent_since))}` : ""}`
        : `<strong style="color:var(--red)">niet verbonden</strong>`}</span></div>`
    : "";

  if (!h.online) {
    body = `<div class="offline-msg">${esc(h.error || "onbereikbaar")}</div>
      <div class="rows">
        <div class="row"><span class="k">${h.kind === "agent" ? "Koppeling" : "URL"}</span><span class="v">${esc(h.url)}</span></div>
        ${tunnelRow}
      </div>`;
  } else {
    const upd = updBadges(h);
    const ent = h.entities;
    const pctClass = ent ? (ent.pct < 3 ? "" : ent.pct < 10 ? "warn" : "bad") : "";
    const bkAge = h.backup?.newest_date ? relAge(h.backup.newest_date) : null;
    const bkOld = h.backup?.newest_date ? (Date.now() - new Date(h.backup.newest_date).getTime()) / 36e5 > 24 * 8 : true;
    body = `
      <div class="ver">
        ${ringSVG(hs.pct)}
        <div class="verwrap">
          <span class="big" title="${upd.length ? "" : "actueel"}">${esc(h.version || "?")}</span>
          ${upd.length ? `<button class="upd-pill" data-act="update" title="Klik om te updaten">${ICONS.update}${esc(upd.join(" · "))}</button>` : ""}
        </div>
      </div>
      <div class="rows">
        <div class="row"><span class="k">Locatie</span><span class="v"><strong>${esc(h.location || "?")}</strong></span></div>
        ${tunnelRow}
        ${h.os ? `<div class="row"><span class="k">HAOS</span><span class="v">${esc(h.os.version)} · Supervisor ${esc(h.supervisor?.version || "?")}</span></div>` : ""}
        ${ent ? `<div class="row"><span class="k">Entiteiten</span>
            <span class="bar"><i class="${pctClass}" style="width:${Math.max(3, 100 - ent.pct)}%"></i></span>
            <span class="v">${ent.dead}/${ent.total} dood (${ent.pct}%)</span></div>` : ""}
        ${h.backup ? `<div class="row"><span class="k">Backup</span>
            <span class="v ${bkOld ? "" : ""}">${h.backup.count ? `<strong>${esc(bkAge)}</strong> · ${h.backup.count} totaal` : "<strong>geen backups</strong>"}</span></div>` : ""}
        ${h.addons ? `<div class="row"><span class="k">Add-ons</span><span class="v">${h.addons.running}/${h.addons.total} actief${h.addons.updates?.length
          ? ` · <button class="linklike" data-act="addons" title="${esc(h.addons.updates.map(a => a.name).join(", "))}">${h.addons.updates.length} update${h.addons.updates.length > 1 ? "s" : ""}</button>` : ""}</span></div>` : ""}
      </div>`;
  }

  const diskWarn = h.online && h.host && h.host.disk_pct > 90
    ? `<span class="disk-warn">${ICONS.warn}${Math.round(h.host.disk_pct)}% schijf</span>` : "";
  const issuesBadge = h.online && h.healthy === false
    ? `<span class="badge err issues-badge">issues</span>` : "";

  el.innerHTML = `
    <div class="card-head">
      <span class="dot ${dot}"></span>
      <span class="name">${esc(h.name)}</span>
      ${diskWarn}${issuesBadge}
      <span class="note">${esc(h.note || "")}</span>
    </div>
    ${body}
    <div class="card-foot">
      <button class="btn small" data-act="detail">${ICONS.detail} Details</button>
      <button class="btn small" data-act="refresh">${ICONS.refresh} Ververs</button>
      ${h.online ? `<button class="btn small" data-act="backup">${ICONS.backup} Backup</button>` : ""}
      ${h.online ? `<button class="btn small danger" data-act="restart">${ICONS.restart} Herstart</button>` : ""}
    </div>`;

  el.querySelectorAll("[data-act]").forEach(btn =>
    btn.addEventListener("click", () => actions[btn.dataset.act](h, btn)));
  return el;
}

/* ring vullen + teller starten zodra de kaart in de DOM staat */
function animateCard(el, i, animate) {
  const prog = $(".ring .prog", el), lbl = $(".ring .lbl", el);
  if (!animate) {                       // achtergrondverversing: geen re-entrance
    el.classList.add("instant");
    if (prog && lbl) { prog.style.strokeDashoffset = prog.dataset.off; lbl.textContent = lbl.dataset.count; }
    return;
  }
  el.style.animationDelay = `${Math.min(i * 55, 400)}ms`;
  if (prog && lbl) requestAnimationFrame(() => setTimeout(() => {
    prog.style.strokeDashoffset = prog.dataset.off;
    countUp(lbl, parseInt(lbl.dataset.count, 10));
  }, 120 + Math.min(i * 55, 400)));
}

function render(animate = true) {
  const els = instances.map(card);
  grid.replaceChildren(...els);
  els.forEach((el, i) => animateCard(el, i, animate));
  $("#empty").hidden = instances.length > 0;
}

function renderSkeletons(n = 3) {
  grid.replaceChildren(...Array.from({ length: n }, () => {
    const s = document.createElement("div");
    s.className = "skel-card";
    return s;
  }));
}

async function loadList(animate = true) {
  const data = await api("api/instances");
  instances = data.instances;
  render(animate);
  lastSync = Date.now();
  renderSync();
}

/* versheid-indicator in de header */
let lastSync = 0;
function renderSync() {
  const el = $("#sync");
  if (!el || !lastSync) return;
  const m = Math.round((Date.now() - lastSync) / 60000);
  el.textContent = m < 1 ? "bijgewerkt zojuist" : `bijgewerkt ${m} min geleden`;
}
setInterval(renderSync, 30000);

/* ---------- overzicht-strip + alerts ---------- */

function renderOverview(o) {
  const stats = $("#stats");
  if (!stats) return;
  stats.innerHTML = `
    <span class="stat"><b data-count="${o.online}">0</b><span>van ${esc(o.total)} online</span></span>
    <span class="stat ${o.updates ? "warn" : ""}"><b data-count="${o.updates}">0</b><span>updates</span></span>
    ${o.alerts
      ? `<button class="stat bad" id="stat-alerts" title="Bekijk alerts"><b data-count="${o.alerts}">0</b><span>alerts</span></button>`
      : `<span class="stat"><b data-count="0">0</b><span>alerts</span></span>`}`;
  stats.querySelectorAll("b[data-count]").forEach(b => countUp(b, parseInt(b.dataset.count, 10), 700));
  $("#stat-alerts")?.addEventListener("click", openAlerts);
}

async function loadOverview() {
  const o = await api("api/overview");
  renderOverview(o);
}

let alertsData = [];
function renderAlerts(alerts) { alertsData = alerts; }

function openAlerts() {
  modal(`
    <div class="modal-head"><h2>Alerts</h2><button class="btn small close" aria-label="Sluiten">${ICONS.close}</button></div>
    <div class="tabpane">${alertsData.length ? alertsData.map(a => `
      <div class="arow">${ICONS.warn}<strong>${esc(a.instance)}</strong> — ${esc(a.message)}</div>`).join("")
      : `<p class="skel">Geen actieve alerts.</p>`}</div>`);
}

async function loadAlerts() {
  const d = await api("api/alerts");
  renderAlerts(d.alerts || []);
}

async function refreshOne(name) {
  const h = await api(`api/instances/${name}/health`);
  const i = instances.findIndex(x => x.name === name);
  if (i >= 0) instances[i] = h; else instances.push(h);
  render();
  return h;
}

/* ---------- acties ---------- */

async function runJob(jobId, label) {
  const t = toast(`${label}…`, "", ICONS.spinner);
  let misses = 0;
  while (true) {
    await new Promise(r => setTimeout(r, 3000));
    const job = await api(`api/jobs/${jobId}`).catch(() => null);
    if (!job) {
      if (++misses >= 3) {   // pas na 3 mislukte polls opgeven, niet stil bij 1
        t.remove(); toast(`${label}: status onbekend (verbinding kwijt?)`, "err"); break;
      }
      continue;
    }
    misses = 0;
    if (job.status === "done") { t.remove(); toast(`${label}: klaar`, "ok"); break; }
    if (job.status === "error") { t.remove(); toast(`${label}: ${job.error}`, "err"); break; }
  }
  await loadList();
}

const actions = {
  detail: h => openDetail(h.name),
  addons: h => openDetail(h.name, "Add-ons"),
  refresh: async (h, btn) => {
    btn.disabled = true;
    try { await refreshOne(h.name); } catch (e) { toast(e.message, "err"); }
  },
  backup: async h => {
    if (!confirm(`Volledige backup maken op '${h.name}'? Dit kan minuten duren.`)) return;
    const r = await api(`api/instances/${h.name}/action`, {method: "POST", body: {type: "backup"}});
    runJob(r.job, `Backup ${h.name}`);
  },
  update: async h => {
    const upd = updBadges(h).join(", ");
    if (!confirm(`Core updaten op '${h.name}' (${upd})?\n\nTip: maak eerst een backup.`)) return;
    const r = await api(`api/instances/${h.name}/action`, {method: "POST", body: {type: "core_update"}});
    runJob(r.job, `Update ${h.name}`);
  },
  restart: async h => {
    if (!confirm(`Home Assistant herstarten op '${h.name}'?`)) return;
    const r = await api(`api/instances/${h.name}/action`, {method: "POST", body: {type: "restart"}});
    runJob(r.job, `Herstart ${h.name}`);
  },
};

/* ---------- detail-modal ---------- */

function modal(html) {
  modalRoot._prevFocus = document.activeElement;
  modalRoot.innerHTML = `<div class="overlay"><div class="modal" role="dialog" aria-modal="true" tabindex="-1">${html}</div></div>`;
  const ov = $(".overlay", modalRoot);
  ov.addEventListener("click", e => { if (e.target === ov) closeModal(); });
  $(".close", modalRoot)?.addEventListener("click", closeModal);
  const m = $(".modal", modalRoot);
  m.focus();
  return m;
}
function closeModal() {
  modalRoot.innerHTML = "";
  modalRoot._prevFocus?.focus?.();
  modalRoot._prevFocus = null;
}
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && modalRoot.firstChild) closeModal();
});

async function openDetail(name, initial = "Overzicht") {
  const cur = instances.find(x => x.name === name);
  const hdot = cur ? (cur.online ? "on" : (cur.error === "geen token" ? "warn" : "off")) : "on";
  const tabs = ["Overzicht", "Integraties", "Add-ons", "Backups", "Log", "Entiteiten"];
  const m = modal(`
    <div class="modal-head">
      <span class="dot ${hdot}"></span><h2>${esc(name)}</h2>
      <button class="btn small close" aria-label="Sluiten">${ICONS.close}</button>
    </div>
    <div class="tabs">${tabs.map(t => `<button class="tab ${t === initial ? "active" : ""}" data-tab="${t}">${t}</button>`).join("")}</div>
    <div class="tabpane" id="pane"></div>`);

  const pane = $("#pane", m);
  m.querySelectorAll(".tab").forEach(btn => btn.addEventListener("click", () => {
    m.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b === btn));
    show(btn.dataset.tab);
  }));

  const loaders = {
    async Overzicht() {
      const h = instances.find(x => x.name === name) || await api(`api/instances/${name}/health`);
      const issuesRows = h.issues?.length
        ? h.issues.map(i => `<div class="row"><span class="k"></span><span class="v" style="color:var(--red)">${esc(i)}</span></div>`).join("")
        : "";
      pane.innerHTML = `<div class="rows">
        <div class="row"><span class="k">URL</span><span class="v">${esc(h.url)}</span></div>
        ${h.ssh ? `<div class="row"><span class="k">SSH</span><span class="v">${esc(h.ssh)}</span></div>` : ""}
        <div class="row"><span class="k">Status</span><span class="v"><strong>${h.online ? "online" : esc(h.error || "offline")}</strong> · ${esc(h.state || "")}</span></div>
        <div class="row"><span class="k">HA Core</span><span class="v"><strong>${esc(h.version || "?")}</strong>${h.core?.update ? ` → update ${esc(h.core.latest)}` : " (actueel)"}</span></div>
        ${h.os ? `<div class="row"><span class="k">HAOS</span><span class="v">${esc(h.os.version)}${h.os.update ? ` → ${esc(h.os.latest)}` : ""}</span></div>` : ""}
        ${h.supervisor ? `<div class="row"><span class="k">Supervisor</span><span class="v">${esc(h.supervisor.version)}</span></div>` : ""}
        ${h.agent_version ? `<div class="row"><span class="k">Agent</span><span class="v">v${esc(h.agent_version)}</span></div>` : ""}
        ${h.host ? `<div class="row"><span class="k">Schijf</span><span class="v">${esc(Math.round((h.host.disk_total || 0) - (h.host.disk_free || 0)))} van ${esc(Math.round(h.host.disk_total || 0))} GB (${esc(Math.round(h.host.disk_pct || 0))}%)</span></div>` : ""}
        ${h.healthy === false ? `<div class="row"><span class="k">Gezondheid</span><span class="v" style="color:var(--red)">problemen gevonden</span></div>` : ""}
        ${issuesRows}
        ${h.entities?.worst?.length ? `<div class="row"><span class="k">Dode ent.</span><span class="v">${h.entities.worst.map(w => `${esc(w[0])} (${w[1]})`).join(", ")}</span></div>` : ""}
        <div class="row"><span class="k">Opgehaald</span><span class="v">${esc(relAge(h.fetched_at))}</span></div>
      </div>
      <div class="form-actions">
        ${h.kind === "agent" ? `<button class="btn small" id="rotate">${ICONS.key} Sleutel roteren</button>` : ""}
        <button class="btn small danger" id="del">${ICONS.trash} Instantie verwijderen</button>
      </div>`;
      $("#del", pane).addEventListener("click", async () => {
        if (!confirm(`'${name}' uit het register verwijderen? (de HA zelf blijft ongemoeid)`)) return;
        await api(`api/instances/${name}`, {method: "DELETE"});
        closeModal(); await loadList(); toast(`'${name}' verwijderd`, "ok");
      });
      $("#rotate", pane)?.addEventListener("click", async () => {
        if (!confirm(`Sleutel roteren voor '${name}'? De oude sleutel wordt direct ongeldig — de add-on op de remote HA moet opnieuw gekoppeld worden.`)) return;
        try {
          const r = await api(`api/agents/${h.agent_id || name}/rotate`, {method: "POST"});
          await loadList();   // tunnel is nu verbroken — kaart direct bijwerken
          showRotatedKey(r);
        } catch (e) {
          toast(e.message, "err");
        }
      });
    },
    async Integraties() {
      pane.innerHTML = `<p class="skel">Laden…</p>`;
      const d = await api(`api/instances/${name}/entries`);
      if (!d.entries.length) { pane.innerHTML = `<p class="skel">Geen config entries gevonden.</p>`; return; }
      pane.innerHTML = `<table><tr><th>Titel</th><th>Domein</th><th>Status</th><th></th></tr>` +
        d.entries.sort((a, b) => a.title.localeCompare(b.title)).map(e => `<tr>
          <td>${esc(e.title)}</td>
          <td>${esc(e.domain)}</td>
          <td>${esc(e.state)}</td>
          <td><button class="btn small" data-entry="${esc(e.entry_id)}">Herlaad</button></td>
        </tr>`).join("") + `</table>`;
      pane.querySelectorAll("[data-entry]").forEach(btn => btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          const r = await api(`api/instances/${name}/action`, {method: "POST",
            body: {type: "reload_entry", entry_id: btn.dataset.entry}});
          runJob(r.job, `Herladen op ${name}`);
        } catch (e) {
          toast(e.message, "err");
          btn.disabled = false;
        }
      }));
    },
    async "Add-ons"() {
      pane.innerHTML = `<p class="skel">Laden…</p>`;
      const d = await api(`api/instances/${name}/addons`);
      if (!d.addons.length) { pane.innerHTML = `<p class="skel">Geen add-ons (geen HAOS?)</p>`; return; }
      pane.innerHTML = `<table><tr><th>Add-on</th><th>Versie</th><th>Status</th><th></th></tr>` +
        d.addons.sort((a, b) => a.name.localeCompare(b.name)).map(a => `<tr>
          <td>${esc(a.name)}</td>
          <td>${esc(a.version)}${a.update_available ? ` <span class="badge">→ ${esc(a.version_latest)}</span>` : ""}</td>
          <td>${esc(a.state)}</td>
          <td>${a.update_available ? `<button class="btn small primary" data-slug="${esc(a.slug)}">Update</button>` : ""}</td>
        </tr>`).join("") + `</table>`;
      pane.querySelectorAll("[data-slug]").forEach(btn => btn.addEventListener("click", async () => {
        btn.disabled = true;
        const r = await api(`api/instances/${name}/action`, {method: "POST", body: {type: "addon_update", slug: btn.dataset.slug}});
        runJob(r.job, `Add-on update op ${name}`);
      }));
    },
    async Backups() {
      pane.innerHTML = `<p class="skel">Laden…</p>`;
      const d = await api(`api/instances/${name}/backups`);
      if (!d.backups.length) { pane.innerHTML = `<p class="skel">Geen backups gevonden.</p>`; return; }
      pane.innerHTML = `<table><tr><th>Datum</th><th>Naam</th><th>Type</th><th>Grootte</th><th></th></tr>` +
        d.backups.sort((a, b) => (b.date || "").localeCompare(a.date || "")).map(b => `<tr>
          <td>${esc((b.date || "").slice(0, 16).replace("T", " "))}</td>
          <td>${esc(b.name)}</td><td>${esc(b.type)}</td><td>${Math.round(b.size || 0)} MB</td>
          <td>${b.protected ? `<span class="lock" title="Versleuteld — alleen op de instantie zelf te openen">&#128274;</span>` : ""}</td>
        </tr>`).join("") + `</table>`;
    },
    async Log() {
      pane.innerHTML = `<p class="skel">Laden…</p>`;
      const d = await api(`api/instances/${name}/logs`);
      pane.innerHTML = `<pre class="log">${esc(d.lines.join("\n")) || "(leeg)"}</pre>`;
      const log = $(".log", pane); log.scrollTop = log.scrollHeight;
    },
    async Entiteiten() {
      pane.innerHTML = `<input class="search field-input" id="q" type="text" placeholder="Zoek op entity_id of naam…">
        <div id="res" class="skel">Typ om te zoeken…</div>`;
      const q = $("#q", pane), res = $("#res", pane);
      let timer;
      q.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          if (q.value.trim().length < 2) { res.innerHTML = "Typ om te zoeken…"; return; }
          res.innerHTML = "Zoeken…";
          const d = await api(`api/instances/${name}/states?filter=${encodeURIComponent(q.value.trim())}`);
          res.className = "";
          res.innerHTML = `<table><tr><th>Entity</th><th>State</th><th>Naam</th></tr>` +
            d.states.slice(0, 100).map(s => `<tr><td>${esc(s.entity_id)}</td><td>${esc(s.state)}</td><td>${esc(s.name)}</td></tr>`).join("") +
            `</table><p class="skel">${d.total} resultaten${d.total > 100 ? " (eerste 100 getoond)" : ""}</p>`;
        }, 350);
      });
      q.classList.add("search");
      q.focus();
    },
  };

  function show(tab) { loaders[tab]().catch(e => pane.innerHTML = `<p class="form-err">${esc(e.message)}</p>`); }
  show(initial);
}

/* ---------- sleutel roteren (eenmalig scherm, gelijk aan koppelcode) ---------- */

function pairingHtml(r, intro) {
  const yaml = `hub_url: ${r.hub_url}\nagent_id: ${r.agent_id}\nagent_key: ${r.key}`;
  return {yaml, html: `
    <p style="margin-bottom:12px">${intro}</p>
    <pre class="log" id="pair-yaml">${esc(yaml)}</pre>
    <div class="form-actions">
      <button class="btn" id="pair-copy">Kopieer configuratie</button>
      <button class="btn primary close2">Klaar</button>
    </div>`};
}

function showRotatedKey(r) {
  const {yaml, html} = pairingHtml(r, `Nieuwe sleutel voor <strong>${esc(r.agent_id)}</strong> — dit is de
    <strong>enige keer</strong> dat de sleutel zichtbaar is. Werk de configuratie van de
    <strong>HA Fleet Agent</strong> add-on op de remote HA bij met:`);
  const m = modal(`
    <div class="modal-head"><h2>Sleutel geroteerd</h2><button class="btn small close" aria-label="Sluiten">${ICONS.close}</button></div>
    <div class="tabpane">${html}</div>`);
  $(".close2", m).addEventListener("click", closeModal);
  $("#pair-copy", m).addEventListener("click", async () => {
    await navigator.clipboard.writeText(yaml).catch(() => {});
    toast("Gekopieerd", "ok");
  });
}

/* ---------- toevoegen ---------- */

function openAdd() {
  const m = modal(`
    <div class="modal-head"><h2>Instantie toevoegen</h2><button class="btn small close" aria-label="Sluiten">${ICONS.close}</button></div>
    <div class="tabs">
      <button class="tab active" data-mode="agent">Agent-koppeling (aanbevolen)</button>
      <button class="tab" data-mode="direct">Directe URL</button>
    </div>
    <div id="add-pane"></div>`);
  const pane = $("#add-pane", m);
  m.querySelectorAll(".tab").forEach(btn => btn.addEventListener("click", () => {
    m.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b === btn));
    show(btn.dataset.mode);
  }));

  function show(mode) {
    if (mode === "agent") {
      pane.innerHTML = `
        <p class="skel" style="margin-bottom:12px">De remote HA belt zelf uit naar deze hub — geen VPN,
        port-forward of token nodig bij de ander. Je krijgt een koppelcode voor de
        "HA Fleet Agent" add-on.</p>
        <div class="field"><label>Naam (bijv. "oma", "broer")</label><input type="text" id="f-name" autocomplete="off"></div>
        <div class="field"><label>Notitie (optioneel)</label><input type="text" id="f-note" autocomplete="off"></div>
        <div class="form-err" id="f-err"></div>
        <div class="form-actions">
          <button class="btn close2">Annuleren</button>
          <button class="btn primary" id="f-save">Koppelcode maken</button>
        </div>`;
      $(".close2", pane).addEventListener("click", closeModal);
      $("#f-save", pane).addEventListener("click", async () => {
        $("#f-save", pane).disabled = true;
        $("#f-err", pane).textContent = "";
        try {
          const r = await api("api/agents", {method: "POST", body: {
            name: $("#f-name", pane).value, note: $("#f-note", pane).value,
          }});
          showPairing(r);
          await loadList();
        } catch (e) {
          $("#f-err", pane).textContent = e.message;
          $("#f-save", pane).disabled = false;
        }
      });
    } else {
      pane.innerHTML = `
        <div class="field"><label>Naam</label><input type="text" id="f-name" autocomplete="off"></div>
        <div class="field"><label>URL (bijv. https://100.x.y.z:8123 of Nabu Casa)</label><input type="url" id="f-url" autocomplete="off"></div>
        <div class="field"><label>Long-lived token (HA → Profiel → Beveiliging, admin-account)</label><input type="password" id="f-token" autocomplete="off"></div>
        <div class="field"><label>Notitie (optioneel)</label><input type="text" id="f-note" autocomplete="off"></div>
        <div class="field check"><input type="checkbox" id="f-insecure"><label for="f-insecure" style="all:unset;cursor:pointer">Self-signed certificaat toestaan</label></div>
        <div class="form-err" id="f-err"></div>
        <div class="form-actions">
          <button class="btn close2">Annuleren</button>
          <button class="btn primary" id="f-save">Toevoegen</button>
        </div>`;
      $(".close2", pane).addEventListener("click", closeModal);
      $("#f-save", pane).addEventListener("click", async () => {
        $("#f-save", pane).disabled = true;
        $("#f-err", pane).textContent = "";
        try {
          await api("api/instances", {method: "POST", body: {
            name: $("#f-name", pane).value, url: $("#f-url", pane).value,
            token: $("#f-token", pane).value, note: $("#f-note", pane).value,
            insecure: $("#f-insecure", pane).checked,
          }});
          closeModal();
          toast("Instantie toegevoegd", "ok");
          await loadList();
        } catch (e) {
          $("#f-err", pane).textContent = e.message;
          $("#f-save", pane).disabled = false;
        }
      });
    }
  }

  function showPairing(r) {
    const {yaml, html} = pairingHtml(r, `Koppelcode voor <strong>${esc(r.agent_id)}</strong> — dit is de
      <strong>enige keer</strong> dat de sleutel zichtbaar is. Installeer op de remote HA de
      <strong>HA Fleet Agent</strong> add-on en plak dit in de add-on configuratie:`);
    pane.innerHTML = html;
    $(".close2", pane).addEventListener("click", closeModal);
    $("#pair-copy", pane).addEventListener("click", async () => {
      await navigator.clipboard.writeText(yaml).catch(() => {});
      toast("Gekopieerd", "ok");
    });
  }

  show("agent");
}

/* ---------- instellingen ---------- */

const DAY_NAMES = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"];

async function openSettings() {
  const m = modal(`
    <div class="modal-head"><h2>Instellingen</h2><button class="btn small close" aria-label="Sluiten">${ICONS.close}</button></div>
    <div class="tabpane" id="pane"><p class="skel">Laden…</p></div>`);
  const pane = $("#pane", m);

  let s;
  try {
    s = await api("api/settings");
  } catch (e) {
    pane.innerHTML = `<p class="form-err">${esc(e.message)}</p>`;
    return;
  }

  const dayOptions = [`<option value="-1">Uit</option>`]
    .concat(DAY_NAMES.map((n, i) => `<option value="${i}">${esc(n)}</option>`)).join("");

  pane.innerHTML = `
    <div class="field-group">
      <div class="field-group-title">Alerts</div>
      <div class="field check"><input type="checkbox" id="s-alerts-enabled"><label for="s-alerts-enabled" style="all:unset;cursor:pointer">Alerts inschakelen</label></div>
      <div class="field" style="margin-top:12px"><label>Notify-service</label>
        <input type="text" id="s-notify-service" autocomplete="off" placeholder="bijv. mobile_app_iphone">
      </div>
      <div class="field"><label>Instantie voor notificaties</label>
        <input type="text" id="s-alert-instance" autocomplete="off">
      </div>
    </div>
    <div class="field-group">
      <div class="field-group-title">Geplande backup</div>
      <div class="field-row">
        <div class="field"><label>Dag</label><select id="s-backup-day">${dayOptions}</select></div>
        <div class="field"><label>Uur</label><input type="text" id="s-backup-hour" autocomplete="off"></div>
      </div>
    </div>
    <div class="field-group">
      <div class="field-group-title">Drempelwaarden</div>
      <div class="field-row">
        <div class="field"><label>Dode entiteiten (%)</label><input type="text" id="s-th-dead" autocomplete="off"></div>
        <div class="field"><label>Backup max (dagen)</label><input type="text" id="s-th-backup" autocomplete="off"></div>
        <div class="field"><label>Offline (min)</label><input type="text" id="s-th-offline" autocomplete="off"></div>
      </div>
    </div>
    <div class="form-err" id="s-err"></div>
    <div class="form-actions">
      <button class="btn close2">Annuleren</button>
      <button class="btn primary" id="s-save">Opslaan</button>
    </div>`;

  $("#s-alerts-enabled", pane).checked = !!s.alerts_enabled;
  $("#s-notify-service", pane).value = s.notify_service || "";
  $("#s-alert-instance", pane).value = s.alert_instance || "";
  $("#s-backup-day", pane).value = String(s.backup_day ?? -1);
  $("#s-backup-hour", pane).value = String(s.backup_hour ?? 3);
  $("#s-th-dead", pane).value = String(s.thresholds?.dead_pct ?? 15);
  $("#s-th-backup", pane).value = String(s.thresholds?.backup_max_days ?? 8);
  $("#s-th-offline", pane).value = String(s.thresholds?.offline_min ?? 30);

  $(".close2", pane).addEventListener("click", closeModal);
  $("#s-save", pane).addEventListener("click", async () => {
    const btn = $("#s-save", pane);
    btn.disabled = true;
    $("#s-err", pane).textContent = "";
    const body = {
      alerts_enabled: $("#s-alerts-enabled", pane).checked,
      notify_service: $("#s-notify-service", pane).value.trim(),
      alert_instance: $("#s-alert-instance", pane).value.trim(),
      backup_day: parseInt($("#s-backup-day", pane).value, 10),
      backup_hour: parseInt($("#s-backup-hour", pane).value, 10) || 0,
      thresholds: {
        dead_pct: parseFloat($("#s-th-dead", pane).value) || 0,
        backup_max_days: parseFloat($("#s-th-backup", pane).value) || 0,
        offline_min: parseFloat($("#s-th-offline", pane).value) || 0,
      },
    };
    try {
      await api("api/settings", {method: "POST", body});
      closeModal();
      toast("Instellingen opgeslagen", "ok");
      await loadOverview();
      await loadAlerts();
    } catch (e) {
      $("#s-err", pane).textContent = e.message;
      btn.disabled = false;
    }
  });
}

/* ---------- logboek ---------- */

async function openAudit() {
  const m = modal(`
    <div class="modal-head"><h2>Logboek</h2><button class="btn small close" aria-label="Sluiten">${ICONS.close}</button></div>
    <div class="tabpane" id="pane"><p class="skel">Laden…</p></div>`);
  const pane = $("#pane", m);
  try {
    const d = await api("api/audit");
    if (!d.events.length) { pane.innerHTML = `<p class="skel">Nog geen gebeurtenissen.</p>`; return; }
    pane.innerHTML = `<table><tr><th></th><th>Tijd</th><th>Actie</th><th>Instantie</th><th>Detail</th></tr>` +
      d.events.map(e => `<tr>
        <td><span class="${e.ok ? "dot-ok" : "dot-err"}"></span></td>
        <td>${esc(relAge(e.ts))}</td>
        <td>${esc(e.action)}</td>
        <td>${esc(e.instance || "")}</td>
        <td>${esc(e.detail || "")}</td>
      </tr>`).join("") + `</table>`;
  } catch (e) {
    pane.innerHTML = `<p class="form-err">${esc(e.message)}</p>`;
  }
}

/* ---------- init ---------- */

$("#btn-add").addEventListener("click", openAdd);
$("#btn-settings").addEventListener("click", openSettings);
$("#btn-audit").addEventListener("click", openAudit);
$("#btn-refresh-all").addEventListener("click", async () => {
  const btn = $("#btn-refresh-all");
  btn.disabled = true;
  for (const h of [...instances]) {
    await refreshOne(h.name).catch(() => {});
  }
  btn.disabled = false;
  toast("Alles ververst", "ok");
  await loadOverview().catch(() => {});
  await loadAlerts().catch(() => {});
});

$("#btn-add-empty")?.addEventListener("click", openAdd);

function initLoad() {
  renderSkeletons();
  loadList().catch(e => {
    grid.innerHTML = `<div class="load-err">
      ${ICONS.warn.replace('class="ico"', 'class="ico-big"')}
      <span>Kon de instanties niet laden: ${esc(e.message)}</span>
      <button class="btn" id="btn-retry">${ICONS.refresh} Opnieuw proberen</button>
    </div>`;
    $("#btn-retry").addEventListener("click", initLoad);
  });
}

initLoad();
loadOverview().catch(() => {});
loadAlerts().catch(() => {});
setInterval(() => loadList(false).catch(() => {}), 60000);
setInterval(() => loadOverview().catch(() => {}), 60000);
setInterval(() => loadAlerts().catch(() => {}), 60000);
