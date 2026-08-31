/* pipeOS web UI: first-run wizard + dashboard. Plain fetch(), no framework. */
"use strict";

const app = document.getElementById("app");

async function api(path, body) {
  const opts = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { /* non-JSON error page */ }
  if (!res.ok) throw new Error(data.error || ("request failed (" + res.status + ")"));
  return data;
}

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function busy(btn, on) { if (btn) btn.disabled = on; }

/* ---------- routing ---------- */

async function boot() {
  let state;
  try {
    state = await api("/api/state");
  } catch (e) {
    app.replaceChildren(el(`<div class="card"><p class="err">The box did not answer: ${esc(e.message)}</p></div>`));
    return;
  }
  if (!state.claimed) return wizard(state);
  if (!state.authed) return loginView(state);
  return dashboard();
}

/* ---------- wizard ---------- */

const SERVICES = [
  { key: "claude", name: "Claude assistant", desc: "The box runs a Claude agent you can put to work.", def: true },
  { key: "stream", name: "Streaming", desc: "Restream video with ffmpeg (configure after setup).", def: false },
  { key: "assistant", name: "Assistant terminal", desc: "A browser terminal into the box's assistant (set a password below).", def: false },
  { key: "agy", name: "Antigravity", desc: "The agy coding agent (if installed on this image).", def: false },
  { key: "pipe", name: "pipe messaging", desc: "Talk to the box by DM from anywhere, via pipe.online.", def: false },
  { key: "support", name: "Vendor support access", desc: "Let your vendor connect to help. Off unless you switch it on.", def: false },
];

function wizard(state) {
  step1(state);
}

function step1(state) {
  const v = el(`<div>
    <p class="steps">Step 1 of 4</p>
    <h1>Welcome — this box is yours to claim</h1>
    <p class="sub">Set an admin password. Whoever holds it manages ${esc(state.hostname)}.</p>
    <div class="card">
      <label for="pw1">Admin password</label>
      <input id="pw1" type="password" autocomplete="new-password" minlength="8">
      <label for="pw2">Same password again</label>
      <input id="pw2" type="password" autocomplete="new-password" minlength="8">
      <button id="go">Claim this box</button>
      <p class="err" id="err" hidden></p>
      <p class="note">Minimum 8 characters. Keep it somewhere safe — it is the only key to this page.</p>
    </div>
  </div>`);
  v.querySelector("#go").onclick = async () => {
    const p1 = v.querySelector("#pw1").value, p2 = v.querySelector("#pw2").value;
    const err = v.querySelector("#err"); err.hidden = true;
    if (p1.length < 8) { err.textContent = "At least 8 characters, please."; err.hidden = false; return; }
    if (p1 !== p2) { err.textContent = "The two passwords do not match."; err.hidden = false; return; }
    busy(v.querySelector("#go"), true);
    try {
      const r = await api("/api/claim", { password: p1 });
      if (!r.saved) alert("Claimed, but saving to the boot media failed:\n" + r.save_detail);
      step2(state);
    } catch (e) {
      err.textContent = e.message; err.hidden = false;
      busy(v.querySelector("#go"), false);
    }
  };
  app.replaceChildren(v);
  v.querySelector("#pw1").focus();
}

function step2(state) {
  const v = el(`<div>
    <p class="steps">Step 2 of 4</p>
    <h1>Name the box</h1>
    <p class="sub">The name becomes its address on your network.</p>
    <div class="card">
      <label for="nick">Box name</label>
      <input id="nick" type="text" value="${esc(state.hostname === "pipeos" ? "" : state.hostname)}" placeholder="e.g. studio-box" pattern="[A-Za-z0-9_.-]+">
      <p class="note">Letters, digits, dots, dashes. You will reach it at http://&lt;name&gt;.local/</p>
      <label for="owner">Your name (optional)</label>
      <input id="owner" type="text" placeholder="e.g. sam">
      <button id="go">Continue</button>
      <button id="skip" class="ghost">Skip</button>
      <p class="err" id="err" hidden></p>
    </div>
  </div>`);
  v.querySelector("#skip").onclick = () => step3();
  v.querySelector("#go").onclick = async () => {
    const nick = v.querySelector("#nick").value.trim();
    const owner = v.querySelector("#owner").value.trim();
    const err = v.querySelector("#err"); err.hidden = true;
    if (!nick && !owner) return step3();
    busy(v.querySelector("#go"), true);
    try {
      await api("/api/name", { nick: nick, owner: owner });
      step3(nick);
    } catch (e) {
      err.textContent = e.message; err.hidden = false;
      busy(v.querySelector("#go"), false);
    }
  };
  app.replaceChildren(v);
}

function step3() {
  const rows = SERVICES.map(s => `
    <div class="row">
      <div><div class="name">${esc(s.name)}</div><div class="desc">${esc(s.desc)}</div></div>
      <label class="switch"><input type="checkbox" data-k="${s.key}" ${s.def ? "checked" : ""}><span></span></label>
    </div>`).join("");
  const v = el(`<div>
    <p class="steps">Step 3 of 4</p>
    <h1>What should this box do?</h1>
    <p class="sub">Everything can be changed later from the dashboard.</p>
    <div class="card">${rows}
      <button id="go">Continue</button>
      <p class="err" id="err" hidden></p>
    </div>
  </div>`);
  v.querySelector("#go").onclick = async () => {
    const picked = {};
    v.querySelectorAll("input[type=checkbox]").forEach(c => { picked[c.dataset.k] = c.checked; });
    const err = v.querySelector("#err"); err.hidden = true;
    busy(v.querySelector("#go"), true);
    try {
      const r = await api("/api/services", picked);
      step4(r.services);
    } catch (e) {
      err.textContent = e.message; err.hidden = false;
      busy(v.querySelector("#go"), false);
    }
  };
  app.replaceChildren(v);
}

function step4(services) {
  const parts = [];
  if (services.claude) parts.push(`
    <div class="card">
      <h2 style="margin-top:0">Connect Claude</h2>
      <p class="note">On your own computer, run <code>claude setup-token</code> and paste the token here.</p>
      <label for="ctok">Claude token</label>
      <input id="ctok" type="password" autocomplete="off">
      <button id="cgo">Connect Claude</button>
      <p id="cmsg" class="note" hidden></p>
    </div>`);
  if (services.pipe) parts.push(`
    <div class="card">
      <h2 style="margin-top:0">Sign in to pipe</h2>
      <p class="note">Get a one-time key at <a href="https://pipe.online" target="_blank" rel="noopener">pipe.online</a> (valid ~15 minutes) and paste it here.</p>
      <label for="pkey">One-time key</label>
      <input id="pkey" type="password" autocomplete="off">
      <button id="pgo">Sign in</button>
      <p id="pmsg" class="note" hidden></p>
    </div>`);
  const v = el(`<div>
    <p class="steps">Step 4 of 4</p>
    <h1>Connect your accounts</h1>
    <p class="sub">${parts.length ? "Each step is optional — you can finish them later from the dashboard." : "Nothing to connect for the services you chose."}</p>
    ${parts.join("")}
    <button id="done">Finish setup</button>
  </div>`);
  const cgo = v.querySelector("#cgo");
  if (cgo) cgo.onclick = async () => {
    const msg = v.querySelector("#cmsg"); msg.hidden = false; msg.textContent = "Checking the token…";
    busy(cgo, true);
    try {
      const r = await api("/api/claude-token", { token: v.querySelector("#ctok").value.trim() });
      msg.textContent = r.probe_ok ? "Claude answered — connected." : "Token stored, but the test call failed: " + r.probe;
    } catch (e) { msg.textContent = e.message; }
    busy(cgo, false);
  };
  const pgo = v.querySelector("#pgo");
  if (pgo) pgo.onclick = async () => {
    const msg = v.querySelector("#pmsg"); msg.hidden = false; msg.textContent = "Signing in…";
    busy(pgo, true);
    try {
      const r = await api("/api/pipe-key", { key: v.querySelector("#pkey").value.trim() });
      msg.textContent = "Signed in as " + r.nick + ".";
    } catch (e) { msg.textContent = e.message; }
    busy(pgo, false);
  };
  v.querySelector("#done").onclick = () => dashboard();
  app.replaceChildren(v);
}

/* ---------- login ---------- */

function loginView(state) {
  const v = el(`<div>
    <h1>${esc(state.hostname)}</h1>
    <p class="sub">Enter the admin password.</p>
    <div class="card">
      <label for="pw">Password</label>
      <input id="pw" type="password" autocomplete="current-password">
      <button id="go">Sign in</button>
      <p class="err" id="err" hidden></p>
    </div>
  </div>`);
  const go = async () => {
    const err = v.querySelector("#err"); err.hidden = true;
    busy(v.querySelector("#go"), true);
    try {
      await api("/api/login", { password: v.querySelector("#pw").value });
      dashboard();
    } catch (e) {
      err.textContent = e.message; err.hidden = false;
      busy(v.querySelector("#go"), false);
    }
  };
  v.querySelector("#go").onclick = go;
  v.querySelector("#pw").addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  app.replaceChildren(v);
  v.querySelector("#pw").focus();
}

/* ---------- dashboard ---------- */

function fmtUptime(s) {
  if (!s && s !== 0) return "?";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

function fmtBps(b) {
  if (b == null) return "?";
  if (b >= 1e6) return (b / 1e6).toFixed(1) + " Mb/s";
  if (b >= 1e3) return (b / 1e3).toFixed(0) + " kb/s";
  return Math.round(b) + " b/s";
}

function niceMax(v) {
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

/* Inline-SVG time-series chart: no libraries reach this box's LAN page.
   series: [{data:[num|null], color:cssColor, fill:bool, label}]
   opts: {t0, interval, unit ("%"|"bps"|""), ymax, ref (dotted hline)} */
function chart(elm, series, opts) {
  const W = 600, H = 150, PL = 44, PR = 8, PT = 8, PB = 18;
  let ymax = opts.ymax || 0;
  series.forEach(s => s.data.forEach(v => { if (v != null && v > ymax) ymax = v; }));
  if (opts.ref && opts.ref > ymax) ymax = opts.ref;
  ymax = ymax > 0 ? niceMax(ymax) : 1;
  const n = Math.max(2, ...series.map(s => s.data.length));
  const X = i => PL + i * (W - PL - PR) / (n - 1);
  const Y = v => PT + (H - PT - PB) * (1 - Math.min(v, ymax) / ymax);
  const fmtY = v => opts.unit === "bps" ? fmtBps(v)
    : opts.unit === "%" ? v + "%"
    : (v >= 100 ? Math.round(v) : +v.toFixed(1)) + (opts.unit || "");
  const parts = [];
  const yGrid = [1 / 3, 2 / 3].map(f =>
    `<line x1="${PL}" y1="${Y(ymax * f)}" x2="${W - PR}" y2="${Y(ymax * f)}" class="grid"/>`).join("");
  parts.push(yGrid);
  if (opts.ref) parts.push(`<line x1="${PL}" y1="${Y(opts.ref)}" x2="${W - PR}" y2="${Y(opts.ref)}" class="refline"/>`);
  for (const s of series) {
    let line = "", area = "", open = false, x0 = null, xl = null;
    s.data.forEach((v, i) => {
      if (v == null) {
        if (open && s.fill) area += ` L${xl},${Y(0)} L${x0},${Y(0)} Z`;
        open = false; return;
      }
      const x = X(i), y = Y(v).toFixed(1);
      if (!open) { line += ` M${x.toFixed(1)},${y}`; area += ` M${x.toFixed(1)},${y}`; x0 = x.toFixed(1); open = true; }
      else { line += ` L${x.toFixed(1)},${y}`; area += ` L${x.toFixed(1)},${y}`; }
      xl = x.toFixed(1);
    });
    if (open && s.fill) area += ` L${xl},${Y(0)} L${x0},${Y(0)} Z`;
    if (s.fill) parts.push(`<path d="${area}" fill="${s.color}" opacity=".12" stroke="none"/>`);
    parts.push(`<path d="${line}" fill="none" stroke="${s.color}" stroke-width="1.8" stroke-linejoin="round"/>`);
  }
  const t = s => { const d = new Date(s * 1000); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };
  const axis = opts.t0
    ? `<text x="${PL}" y="${H - 4}" class="tick">${t(opts.t0)}</text>
       <text x="${W - PR}" y="${H - 4}" class="tick" text-anchor="end">${t(opts.t0 + n * opts.interval)}</text>` : "";
  const legend = series.length > 1 ? series.map(s =>
    `<span class="lg"><i style="background:${s.color}"></i>${esc(s.label || "")}</span>`).join("") : "";
  elm.innerHTML = `${legend ? `<div class="legend">${legend}</div>` : ""}
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="tschart">
      <text x="${PL - 5}" y="${Y(ymax) + 4}" class="tick" text-anchor="end">${fmtY(ymax)}</text>
      <text x="${PL - 5}" y="${Y(0) + 4}" class="tick" text-anchor="end">0</text>
      ${parts.join("\n")}${axis}
    </svg>`;
}

function verdictClass(report) {
  const m = /^verdict: (.*)$/m.exec(report || "");
  const v = m ? m[1] : "";
  if (/DEGRADED/.test(v)) return ["status-bad", v];
  if (/warning|remediation|recovered/.test(v)) return ["status-warn", v];
  if (/green/.test(v)) return ["status-ok", v];
  return ["status-warn", v || "no boot report yet"];
}

async function dashboard() {
  let st;
  try {
    st = await api("/api/status");
  } catch (e) {
    return boot();
  }
  const [vcls, verdict] = verdictClass(st.boot_report);
  const rows = SERVICES.map(s => `
    <div class="row">
      <div><div class="name">${esc(s.name)}</div><div class="desc">${esc(s.desc)}</div></div>
      <label class="switch"><input type="checkbox" data-k="${s.key}" ${st.services[s.key] ? "checked" : ""}><span></span></label>
    </div>`).join("");
  const running = Object.entries(st.running).map(([k, ok]) =>
    `<span class="pill">${esc(k)}: ${ok ? "up" : "down"}</span>`).join(" ");
  const showStream = !!st.services.stream;
  const showAssist = !!(st.services.claude || st.services.assistant);
  const ICON = {
    overview: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/></svg>',
    streaming: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19 4.9a10 10 0 0 1 0 14.2M5 19.1A10 10 0 0 1 5 4.9"/></svg>',
    assistant: '<svg viewBox="0 0 24 24"><path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.5 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 17 0z"/></svg>',
    services: '<svg viewBox="0 0 24 24"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>',
    pipe: '<svg viewBox="0 0 24 24"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7z"/></svg>',
    files: '<svg viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
    network: '<svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
    system: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="2" x2="9" y2="4"/><line x1="15" y1="2" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="22"/><line x1="15" y1="20" x2="15" y2="22"/><line x1="20" y1="9" x2="22" y2="9"/><line x1="20" y1="15" x2="22" y2="15"/><line x1="2" y1="9" x2="4" y2="9"/><line x1="2" y1="15" x2="4" y2="15"/></svg>',
  };
  const navItem = (id, label) => `<a data-nav="${id}" href="#/${id}"><span class="ic">${ICON[id] || ""}</span>${label}</a>`;
  const v = el(`<div class="app">
    <aside class="sidenav">
      <div class="brand"><span class="dot"></span>pipeOS</div>
      <div class="boxid">
        <div class="boxname">${esc(st.nick || st.hostname)}</div>
        <span class="${vcls}">${esc(verdict)}</span>
      </div>
      <nav>
        ${navItem("overview", "Overview")}
        ${showStream ? navItem("streaming", "Streaming") : ""}
        ${showAssist ? navItem("assistant", "Assistant") : ""}
        ${navItem("files", "Files")}
        ${st.services.pipe ? navItem("pipe", "pipe") : ""}
        ${navItem("services", "Services")}
        ${navItem("network", "Network")}
        ${navItem("system", "System")}
      </nav>
      <div class="navfoot">
        <span class="note">up ${fmtUptime(st.uptime_s)} · ${st.work_pct == null ? "disk ?" : st.work_pct + "% disk"}${st.work_free_mb != null ? " · " + Math.round(st.work_free_mb / 1024) + " GB free" : ""}</span>
        <button id="logout" class="ghost">Sign out</button>
      </div>
    </aside>
    <main class="content">
      <section data-view="overview">
        <div class="viewhead"><h1>Overview</h1></div>
        <div id="alerts"></div>
        <div class="stats">
          <div class="tile"><div class="k">Status</div><div class="val small"><span class="${vcls}">${esc(verdict)}</span></div></div>
          <div class="tile"><div class="k">Uptime</div><div class="val">${fmtUptime(st.uptime_s)}</div></div>
          <div class="tile"><div class="k">Disk</div><div class="val">${st.work_pct == null ? "?" : st.work_pct + "%"}</div><div class="note">${st.work_free_mb != null ? Math.round(st.work_free_mb / 1024) + " GB free" : "used"}</div></div>
          <div class="tile"><div class="k">CPU load</div><div class="val" id="ovload">…</div><div class="note" id="ovloadn"></div></div>
          <div class="tile"><div class="k">CPU temp</div><div class="val" id="ovtemp">…</div></div>
        </div>
        ${showStream ? `
        <div class="card">
          <div class="cardhead"><h2>Streaming</h2><span class="onair off" id="ovair"><span class="blip"></span>checking…</span></div>
          <div id="ovtargets" class="note">loading…</div>
        </div>` : ""}
        <div class="card">
          <div class="cardhead"><h2>Services</h2></div>
          <div>${running}</div>
          <details style="margin-top:.6rem"><summary class="note">Full boot report</summary>
            <pre class="report">${esc(st.boot_report || "(none this boot)")}</pre></details>
        </div>
      </section>

      ${showStream ? `
      <section data-view="streaming" hidden>
        <div class="viewhead"><h1>Streaming</h1></div>
        <div class="card">
          <label for="smode">Mode</label>
          <select id="smode">
            <option value="media">Restream an existing video feed</option>
            <option value="browser">Render a web page to video (browser)</option>
          </select>
          <div id="smedia">
            <label for="ssrc">Source (input URL or device)</label>
            <input id="ssrc" type="text" placeholder="e.g. https://example.com/live or /dev/video0">
          </div>
          <div id="sbrowser" hidden>
            <label for="surl">Page URL to render</label>
            <input id="surl" type="text" placeholder="e.g. https://basho.dev">
            <label for="sres">Resolution</label>
            <input id="sres" type="text" placeholder="1920x1080">
            <label for="sfps">Frame rate</label>
            <input id="sfps" type="text" placeholder="30">
            <label class="note" style="display:flex;align-items:center;gap:.5rem;margin-top:.4rem">
              <input id="svaapi" type="checkbox" style="width:auto"> Hardware encode on the Intel GPU (VAAPI)
            </label>
          </div>
          <label>Providers</label>
          <div id="targets"></div>
          <button id="addtarget" class="ghost" type="button" style="margin-bottom:.6rem">+ Add a provider</button>
          <label for="sbitrate">Bitrate (per target)</label>
          <input id="sbitrate" type="text" placeholder="3500k">
          <label class="note" style="display:flex;align-items:center;gap:.5rem;margin-top:.4rem">
            <input id="sboot" type="checkbox" style="width:auto" checked> Start streaming automatically at boot
          </label>
          <label for="sargs">Extra ffmpeg args (optional)</label>
          <input id="sargs" type="text" placeholder="advanced — usually blank">
          <button id="ssave">Save & restart stream</button>
          <button id="slog" class="ghost">Show stream log</button>
          <p class="note" id="smsg" hidden></p>
          <pre class="report" id="slogbox" hidden></pre>
        </div>
      </section>` : ""}

      ${showAssist ? `
      <section data-view="assistant" hidden>
        <div class="viewhead"><h1>Assistant</h1></div>
        ${st.services.claude ? `
        <div class="card">
          <h2>Ask the box</h2>
          <div id="chatlog" style="max-height:16rem;overflow-y:auto"></div>
          <label for="chatmsg">Message</label>
          <input id="chatmsg" type="text" autocomplete="off" placeholder="Ask your assistant anything">
          <button id="chatgo">Send</button>
          <p class="note" id="chatnote" hidden></p>
        </div>` : ""}
        ${st.services.assistant ? `
        <div class="card">
          <h2>Terminal</h2>
          <p class="note">A full terminal into this box's assistant, in your browser — the same session as the chat.</p>
          <label for="apass">Terminal password</label>
          <input id="apass" type="password" autocomplete="off" placeholder="leave blank to keep the saved password">
          <label for="aport">Port</label>
          <input id="aport" type="text" placeholder="7681">
          <button id="asave">Save & restart terminal</button>
          <a id="aopen" class="btn ghost" href="#" target="_blank" rel="noopener" style="display:none">Open terminal ↗</a>
          <p class="note" id="amsg" hidden></p>
        </div>` : ""}
      </section>` : ""}

      <section data-view="files" hidden>
        <div class="viewhead"><h1>Files</h1></div>
        <div class="filegrid${st.services.claude ? "" : " nochat"}">
          <div class="card">
            <div class="cardhead">
              <div class="crumbs" id="crumbs"></div>
              <span style="white-space:nowrap">
                <button id="fmkdir" class="ghost small" type="button">new folder</button>
                <button id="fupbtn" class="ghost small" type="button">upload</button>
                <input id="fupin" type="file" multiple hidden>
              </span>
            </div>
            <div id="fmovebar" class="movebar" hidden>
              <span class="note" id="fmovemsg"></span>
              <button id="fmovehere" class="small" type="button">move here</button>
              <button id="fmovecancel" class="ghost small" type="button">cancel</button>
            </div>
            <div id="flist">loading…</div>
            <p class="err" id="ferr" hidden></p>
          </div>
          ${st.services.claude ? `
          <div class="card chatpane">
            <div class="cardhead"><h2>Assistant</h2></div>
            <div id="fchatlog" class="chatlog"></div>
            <div class="chatrow">
              <input id="fchatmsg" type="text" autocomplete="off" placeholder="Ask about these files…">
              <button id="fchatgo" type="button">Send</button>
            </div>
            <p class="note" id="fchatnote" hidden></p>
          </div>` : ""}
        </div>
      </section>

      ${st.services.pipe ? `
      <section data-view="pipe" hidden>
        <div class="viewhead"><h1>pipe</h1></div>
        <div class="card">
          <div class="cardhead"><h2>Identity</h2><span class="pill" id="pauth">checking…</span></div>
          <div class="stats">
            <div class="tile"><div class="k">Nick</div><div class="val small" id="pnick">…</div></div>
            <div class="tile"><div class="k">Owner</div><div class="val small" id="powner">…</div></div>
            <div class="tile"><div class="k">Cohort</div><div class="val small" id="pcoh">…</div></div>
          </div>
          <div id="psignin" hidden>
            <label for="pkey3">One-time key from <a href="https://pipe.online" target="_blank" rel="noopener">pipe.online</a></label>
            <input id="pkey3" type="password" autocomplete="off">
            <button id="pgo3" type="button">Sign in</button>
          </div>
          <button id="plogout" class="ghost" type="button" hidden>Sign out</button>
          <p class="note" id="pidmsg" hidden></p>
          <p class="note">The nick is the signed-in identity — to change it, sign out, then sign in with a key minted for the new nick.</p>
        </div>
        <div class="card">
          <h2>Owner &amp; cohort</h2>
          <label for="pown2">Owner nick (the box DMs this person)</label>
          <input id="pown2" type="text">
          <label for="pcid2">Cohort id (digits; blank for none)</label>
          <input id="pcid2" type="text">
          <button id="pocsave" type="button">Save</button>
          <p class="note" id="pocmsg" hidden></p>
          <details><summary class="note">Cohort board</summary><pre class="report" id="pboard">…</pre></details>
        </div>
        <div class="card">
          <h2>Contacts</h2>
          <div id="pcontacts" class="note">loading…</div>
          <div class="chatrow">
            <input id="pcadd" type="text" autocomplete="off" placeholder="nick">
            <button id="pcaddgo" class="ghost" type="button">Add contact</button>
          </div>
          <p class="note" id="pcmsg3" hidden></p>
        </div>
        <div class="card">
          <h2>Settings</h2>
          <div id="pprefs" class="note">loading…</div>
        </div>
      </section>` : ""}

      <section data-view="services" hidden>
        <div class="viewhead"><h1>Services</h1></div>
        <div class="card">${rows}<p class="err" id="serr" hidden></p></div>
        ${st.services.pipe ? `
        <div class="card">
          <h2>pipe messaging</h2>
          <p class="note">Sign-in, contacts and cohort moved to the <a href="#/pipe">pipe panel</a>.</p>
        </div>` : ""}
      </section>

      <section data-view="network" hidden>
        <div class="viewhead"><h1>Network</h1></div>
        <div class="stats">
          <div class="tile"><div class="k">Address</div><div class="val small" id="nip">…</div><div class="note" id="nif"></div></div>
          <div class="tile"><div class="k">Down</div><div class="val small" id="nrx">…</div></div>
          <div class="tile"><div class="k">Up</div><div class="val small" id="ntx">…</div></div>
        </div>
        <div class="card">
          <div class="cardhead"><h2>Traffic</h2><span class="note" id="ntot"></span></div>
          <div class="ch" id="ch-net"></div>
        </div>
        <div class="card">
          <h2>Secure access (HTTPS)</h2>
          <p class="note" id="tlsnote">checking…</p>
          <a class="btn ghost" id="camobile" href="/pipeos-ca.mobileconfig">iPhone / iPad: install profile</a>
          <a class="btn ghost" id="cadl" href="/ca.crt" download>Mac / Windows / Android: download certificate</a>
          <p class="note" style="margin-bottom:.2rem"><b>Linux:</b> one command — installs into the system store, Chrome, and Firefox:</p>
          <pre class="report" id="lnxcmd" style="user-select:all;margin-top:0"></pre>
          <details><summary class="note">How to install it (once per device)</summary>
            <p class="note" style="line-height:1.5">
            <b>iPhone/iPad:</b> tap the profile above → Settings offers to install it → then Settings › General › About › Certificate Trust Settings → turn it on.<br>
            <b>Mac:</b> open the .crt → Keychain Access → double-click “pipeOS … CA” → Trust → “Always Trust”.<br>
            <b>Windows:</b> open the .crt → Install Certificate → Local Machine → Trusted Root Certification Authorities.<br>
            <b>Android:</b> Settings › Security › Encryption &amp; credentials › Install a certificate › CA certificate → pick the .crt.<br>
            <b>Linux (manual):</b> paste the command above into a terminal; it needs sudo and, for the browsers, the certutil tool (package <code>libnss3-tools</code> or <code>nss-tools</code>).<br>
            Then reload over <span id="httpslink"></span> and you’ll see the padlock.</p>
          </details>
        </div>
      </section>

      <section data-view="system" hidden>
        <div class="viewhead"><h1>System</h1></div>
        <div class="stats" id="metrics">
          <div class="tile"><div class="k">Metrics</div><div class="val small">loading…</div></div>
        </div>
        <div class="cardhead" style="margin:1.1rem 0 .7rem"><h2>History</h2>
          <span id="spanbtns">
            <button class="ghost small" type="button" data-mspan="1h">1h</button>
            <button class="ghost small" type="button" data-mspan="6h">6h</button>
            <button class="ghost small" type="button" data-mspan="24h">24h</button>
          </span>
        </div>
        <div class="charts">
          <div class="card"><div class="k">CPU load</div><div class="ch" id="ch-cpu"></div></div>
          <div class="card"><div class="k">Memory</div><div class="ch" id="ch-mem"></div></div>
          <div class="card"><div class="k">CPU temp</div><div class="ch" id="ch-temp"></div></div>
          <div class="card"><div class="k">Disks</div><div class="ch" id="ch-disk"></div></div>
        </div>
        <p class="note">History lives in memory and starts over when the dashboard restarts.</p>
        <div class="card">
          <h2>Logs</h2>
          <select id="logsel">
            ${["selfcheck", "pipeos-web", "pipeos-mdns", "pipe-daemon", "pipebox-listener", "pipeos-stream", "pipeos-assistant", "selfupdate", "worksweep"].map(l => `<option>${l}</option>`).join("")}
          </select>
          <button id="logview" class="ghost" style="margin-top:.4rem">View</button>
          <pre class="report" id="logbox" hidden></pre>
        </div>
        <div class="card">
          <h2>Maintenance</h2>
          <button id="save" style="margin-top:0">Save state to boot media now</button>
          <p class="note">The box also saves automatically every 15 minutes.</p>
          <button id="repair" class="ghost">Repair remote access</button>
          <button id="reboot" class="ghost">Reboot the box</button>
          <button id="rebootfw" class="ghost">Reboot into BIOS</button>
          <p class="note" id="mmsg" hidden></p>
          <div id="updrow" style="margin-top:1rem">
            <span class="pill" id="updstate">updates: checking…</span>
            <button id="updnow" class="ghost" hidden>Update now</button>
          </div>
          <details><summary class="note">Change admin password</summary>
            <label for="cur">Current password</label><input id="cur" type="password">
            <label for="new">New password</label><input id="new" type="password" minlength="8">
            <button id="chpw">Change password</button>
            <p class="note" id="pwmsg" hidden></p>
          </details>
        </div>
      </section>
    </main>
  </div>`);
  v.querySelector("#logout").onclick = async () => { try { await api("/api/logout", {}); } catch (e) {} boot(); };
  v.querySelectorAll("input[type=checkbox]").forEach(c => {
    if (!c.dataset.k) return;  // service toggles only — not e.g. the VAAPI box
    c.onchange = async () => {
      const serr = v.querySelector("#serr"); serr.hidden = true;
      const picked = {}; picked[c.dataset.k] = c.checked;
      try {
        const r = await api("/api/services", picked);
        if (r.problems && r.problems.length) { serr.textContent = r.problems.join("; "); serr.hidden = false; }
      } catch (e) {
        serr.textContent = e.message; serr.hidden = false;
        c.checked = !c.checked;
      }
    };
  });
  // one wiring for every chat surface (assistant view, files pane)
  const wireChat = (goId, msgId, logId, noteId) => {
    const go = v.querySelector(goId);
    if (!go) return;
    const send = async () => {
      const inp = v.querySelector(msgId), log = v.querySelector(logId), note = v.querySelector(noteId);
      const msg = inp.value.trim();
      if (!msg) return;
      log.appendChild(el(`<p><strong>you:</strong> ${esc(msg)}</p>`));
      inp.value = ""; busy(go, true);
      note.hidden = false; note.textContent = "thinking…";
      try {
        const r = await api("/api/chat", { message: msg });
        log.appendChild(el(`<p><strong>${esc(st.nick || "box")}:</strong> ${esc(r.reply)}</p>`));
        note.hidden = true;
      } catch (e) { note.textContent = e.message; }
      busy(go, false);
      log.scrollTop = log.scrollHeight;
    };
    go.onclick = send;
    v.querySelector(msgId).addEventListener("keydown", e => { if (e.key === "Enter") send(); });
  };
  wireChat("#chatgo", "#chatmsg", "#chatlog", "#chatnote");
  wireChat("#fchatgo", "#fchatmsg", "#fchatlog", "#fchatnote");
  // ---- overview: attention strip + live tiles ----
  const alertItems = [];
  const renderAlerts = () => {
    const box = v.querySelector("#alerts");
    box.innerHTML = alertItems.length
      ? alertItems.map(([c, t]) => `<div class="alert ${c}">${esc(t)}</div>`).join("")
      : `<div class="alert ok">All clear — nothing needs attention.</div>`;
  };
  const addAlert = (cls, text) => { alertItems.push([cls, text]); renderAlerts(); };
  (st.boot_report || "").split("\n").forEach(l => {
    if (/^CRITICAL:/.test(l)) addAlert("bad", l);
    else if (/^warn:/.test(l)) addAlert("warn", l);
  });
  Object.entries(st.running).forEach(([k, ok]) => {
    if (!ok) addAlert("bad", k + " is enabled but not running");
  });
  renderAlerts();
  api("/api/metrics").then(m => {
    v.querySelector("#ovload").textContent = m.load1 == null ? "?" : m.load1.toFixed(2);
    v.querySelector("#ovloadn").textContent = m.ncpu ? m.ncpu + " cores" : "";
    v.querySelector("#ovtemp").textContent = m.temp_c == null ? "—" : m.temp_c + "°C";
  }).catch(() => {});
  v.querySelector("#save").onclick = async () => {
    const b = v.querySelector("#save"); busy(b, true);
    try { const r = await api("/api/save", {}); if (!r.ok) alert("Save failed:\n" + r.detail); }
    catch (e) { alert(e.message); }
    busy(b, false);
  };
  const ssave = v.querySelector("#ssave");
  if (ssave) {
    const smode = v.querySelector("#smode");
    const applyMode = () => {
      const browser = smode.value === "browser";
      v.querySelector("#sbrowser").hidden = !browser;
      v.querySelector("#smedia").hidden = browser;
    };
    smode.onchange = applyMode;
    const PROVIDERS = {
      YouTube: "rtmp://a.rtmp.youtube.com/live2",
      Twitch: "rtmp://live.twitch.tv/app",
      Kick: "rtmps://fa723fc1b171.global-contribute.live-video.net/app",
      Facebook: "rtmps://live-api-s.facebook.com:443/rtmp/",
      Custom: "",
    };
    const tbox = v.querySelector("#targets");
    // Render one provider row. `t` is {name,url,on,key_set} from the server.
    const addRow = (t) => {
      if (tbox.children.length >= 4) return;
      t = t || { name: "Custom", url: "", on: true, key_set: false };
      const row = el(`<div class="surface" style="padding:.6rem;margin-bottom:.5rem;display:grid;gap:.35rem"></div>`);
      const provsel = el(`<select></select>`);
      for (const name of Object.keys(PROVIDERS)) {
        const o = el(`<option>${name}</option>`); if (t.name === name) o.selected = true; provsel.appendChild(o);
      }
      const url = el(`<input type="text" placeholder="rtmp://…" class="well">`); url.value = t.url || (PROVIDERS[t.name] || "");
      const key = el(`<input type="password" autocomplete="off" class="well" placeholder="${t.key_set ? "(saved — blank keeps it)" : "stream key"}">`);
      const onwrap = el(`<label class="note" style="display:flex;align-items:center;gap:.4rem"></label>`);
      const on = el(`<input type="checkbox" style="width:auto">`); on.checked = t.on !== false;
      onwrap.appendChild(on); onwrap.appendChild(document.createTextNode(" stream to this"));
      const rm = el(`<button class="ghost" type="button" style="justify-self:start">remove</button>`);
      provsel.onchange = () => { const u = PROVIDERS[provsel.value]; if (u || provsel.value !== "Custom") url.value = u; };
      rm.onclick = () => row.remove();
      row.appendChild(provsel); row.appendChild(url); row.appendChild(key); row.appendChild(onwrap); row.appendChild(rm);
      row._get = () => ({ name: provsel.value, url: url.value, key: key.value, on: on.checked, keep_key: !key.value });
      tbox.appendChild(row);
    };
    v.querySelector("#addtarget").onclick = () => addRow();
    // Overview card: the same /api/stream answer drives the on-air badge.
    const ovair = v.querySelector("#ovair"), ovtargets = v.querySelector("#ovtargets");
    const renderOvStream = (s) => {
      if (!ovair) return;
      if (!s) {
        ovair.className = "onair off";
        ovair.innerHTML = '<span class="blip"></span>unknown';
        ovtargets.textContent = "Could not read the stream status.";
        return;
      }
      const live = !!s.running;
      ovair.className = "onair " + (live ? "live" : "off");
      ovair.innerHTML = '<span class="blip"></span>' + (live ? "ON AIR" : "off air");
      const ts = (s.targets || []).filter(t => t.url || t.key_set || t.name);
      if (!ts.length) {
        ovtargets.className = "note";
        ovtargets.innerHTML = 'No providers configured yet — set them up under <a href="#/streaming">Streaming</a>.';
        return;
      }
      ovtargets.className = "";
      ovtargets.replaceChildren(...ts.map(t => el(`<div class="targetline">
        <span class="tdot ${live && t.on !== false ? "on" : "off"}"></span>
        <span class="tname">${esc(t.name || "target")}</span>
        ${t.on === false ? '<span class="note">off</span>' : ""}
        <span class="turl">${esc(t.url || "")}</span></div>`)));
    };
    api("/api/stream").then(s => {
      renderOvStream(s);
      smode.value = s.mode || "media"; applyMode();
      v.querySelector("#ssrc").value = s.src; v.querySelector("#surl").value = s.url || "";
      v.querySelector("#sres").value = s.res || ""; v.querySelector("#sfps").value = s.fps || "";
      v.querySelector("#sbitrate").value = s.bitrate || ""; v.querySelector("#svaapi").checked = !!s.vaapi;
      v.querySelector("#sboot").checked = s.boot !== false;
      v.querySelector("#sargs").value = s.args;
      tbox.textContent = "";
      const rows = (s.targets || []).filter(t => t.url || t.key_set || t.name);
      if (rows.length) rows.forEach(addRow);
      else { addRow({ name: "YouTube", url: PROVIDERS.YouTube, on: true }); addRow({ name: "Twitch", url: PROVIDERS.Twitch, on: true }); }
    }).catch(() => { renderOvStream(null); });
    ssave.onclick = async () => {
      const msg = v.querySelector("#smsg"); msg.hidden = false; msg.textContent = "saving…";
      busy(ssave, true);
      try {
        const targets = Array.from(tbox.children).map(r => r._get());
        const r = await api("/api/stream-config", {
          mode: smode.value,
          src: v.querySelector("#ssrc").value, url: v.querySelector("#surl").value,
          res: v.querySelector("#sres").value, fps: v.querySelector("#sfps").value,
          bitrate: v.querySelector("#sbitrate").value,
          vaapi: v.querySelector("#svaapi").checked,
          boot: v.querySelector("#sboot").checked,
          args: v.querySelector("#sargs").value,
          targets,
        });
        msg.textContent = r.problems.length ? r.problems.join("; ") : "Saved — stream restarted.";
      } catch (e) { msg.textContent = e.message; }
      busy(ssave, false);
    };
    v.querySelector("#slog").onclick = async () => {
      const box = v.querySelector("#slogbox"); box.hidden = false; box.textContent = "…";
      try { box.textContent = (await api("/api/stream-log")).text; } catch (e) { box.textContent = e.message; }
    };
  }
  const asave = v.querySelector("#asave");
  if (asave) {
    const aopen = v.querySelector("#aopen");
    const showOpen = (port) => {
      aopen.href = location.protocol + "//" + location.hostname + ":" + (port || "7681") + "/";
      aopen.style.display = "";
    };
    api("/api/assistant").then(a => {
      v.querySelector("#aport").value = a.port || "";
      if (a.pass_set) {
        v.querySelector("#apass").placeholder = "(a password is saved — blank keeps it)";
        showOpen(a.port);
      }
    }).catch(() => {});
    asave.onclick = async () => {
      const msg = v.querySelector("#amsg"); msg.hidden = false; msg.textContent = "saving…";
      busy(asave, true);
      try {
        const r = await api("/api/assistant-config", {
          password: v.querySelector("#apass").value,
          port: v.querySelector("#aport").value,
          keep_pass: !v.querySelector("#apass").value,
        });
        msg.textContent = r.problems.length ? r.problems.join("; ") : "Saved — terminal restarted.";
        showOpen(v.querySelector("#aport").value);
      } catch (e) { msg.textContent = e.message; }
      busy(asave, false);
    };
  }
  // ---- pipe panel ----
  let loadPipe = null;
  if (st.services.pipe) {
    const setT = (id, t) => { const e = v.querySelector(id); if (e) e.textContent = t; };
    const PREF_DESC = {
      dm_relay: "Relay DMs through the server when a peer is offline",
      remember_login: "Stay signed in across reboots",
      agent_events: "Announce agent activity as events",
    };
    loadPipe = () => {
      api("/api/pipe").then(p => {
        setT("#pnick", p.nick || "—");
        setT("#powner", p.owner || "—");
        setT("#pcoh", p.cohort || "—");
        const au = v.querySelector("#pauth");
        au.textContent = p.authed ? "signed in" : "not signed in";
        au.className = "pill " + (p.authed ? "status-ok" : "status-warn");
        v.querySelector("#psignin").hidden = !!p.authed;
        v.querySelector("#plogout").hidden = !p.authed;
        if (document.activeElement !== v.querySelector("#pown2")) v.querySelector("#pown2").value = p.owner || "";
        if (document.activeElement !== v.querySelector("#pcid2")) v.querySelector("#pcid2").value = p.cohort || "";
        const prefs = p.prefs || {};
        const pp = v.querySelector("#pprefs");
        pp.className = "";
        pp.innerHTML = Object.keys(PREF_DESC).map(k => `
          <div class="row"><div><div class="name">${k}</div><div class="desc">${PREF_DESC[k]}</div></div>
          <label class="switch"><input type="checkbox" data-pref="${k}" ${prefs[k] ? "checked" : ""}><span></span></label></div>`).join("");
        pp.querySelectorAll("input[data-pref]").forEach(c => {
          c.onchange = async () => {
            try { await api("/api/pipe-set", { pref: c.dataset.pref, value: c.checked }); }
            catch (e) { c.checked = !c.checked; alert(e.message); }
          };
        });
      }).catch(() => {});
      api("/api/pipe-contacts").then(r => {
        const box = v.querySelector("#pcontacts");
        const list = Array.isArray(r.contacts) ? r.contacts
          : (r.contacts && Array.isArray(r.contacts.contacts) ? r.contacts.contacts : null);
        if (list && list.length) {
          box.className = "";
          box.replaceChildren(...list.map(c => {
            const nick = typeof c === "string" ? c : (c.nick || c.name || "?");
            const trust = typeof c === "object" ? (c.trust || c.state || "") : "";
            const row = el(`<div class="targetline"><span class="tname">${esc(nick)}</span><span class="note">${esc(trust)}</span><button class="ghost small" type="button" style="margin-left:auto">remove</button></div>`);
            row.querySelector("button").onclick = async () => {
              if (!confirm("Remove " + nick + " from contacts?")) return;
              try { await api("/api/pipe-contact", { nick: nick, remove: true }); loadPipe(); }
              catch (e) { alert(e.message); }
            };
            return row;
          }));
        } else {
          box.className = "note";
          box.textContent = (list && !list.length) ? "No contacts yet." : (r.text || "No contacts yet.");
        }
      }).catch(() => {});
      api("/api/pipe-board").then(b => {
        v.querySelector("#pboard").textContent =
          b.cohort ? (b.text || "(empty board)") : "(no cohort set)";
      }).catch(() => {});
    };
    const pgo3 = v.querySelector("#pgo3"), pidmsg = v.querySelector("#pidmsg");
    pgo3.onclick = async () => {
      pidmsg.hidden = false; pidmsg.textContent = "Signing in…"; busy(pgo3, true);
      try {
        const r = await api("/api/pipe-key", { key: v.querySelector("#pkey3").value.trim() });
        pidmsg.textContent = "Signed in as " + r.nick + ".";
        v.querySelector("#pkey3").value = "";
        loadPipe();
      } catch (e) { pidmsg.textContent = e.message; }
      busy(pgo3, false);
    };
    v.querySelector("#plogout").onclick = async () => {
      if (!confirm("Sign this box out of pipe? It stops sending and receiving DMs until signed in again.")) return;
      pidmsg.hidden = false; pidmsg.textContent = "Signing out…";
      try { await api("/api/pipe-logout", {}); pidmsg.textContent = "Signed out."; loadPipe(); }
      catch (e) { pidmsg.textContent = e.message; }
    };
    v.querySelector("#pocsave").onclick = async () => {
      const m = v.querySelector("#pocmsg"); m.hidden = false; m.textContent = "saving…";
      try {
        const owner = v.querySelector("#pown2").value.trim();
        if (owner) await api("/api/name", { owner: owner });
        await api("/api/cohort", { id: v.querySelector("#pcid2").value.trim() });
        m.textContent = "Saved.";
        loadPipe();
      } catch (e) { m.textContent = e.message; }
    };
    v.querySelector("#pcaddgo").onclick = async () => {
      const m = v.querySelector("#pcmsg3"); m.hidden = false; m.textContent = "…";
      try {
        await api("/api/pipe-contact", { nick: v.querySelector("#pcadd").value.trim() });
        m.hidden = true; v.querySelector("#pcadd").value = "";
        loadPipe();
      } catch (e) { m.textContent = e.message; }
    };
  }

  // ---- files: /work explorer + mover ----
  let loadFiles = null;
  {
    const flist = v.querySelector("#flist"), crumbs = v.querySelector("#crumbs"),
      ferr = v.querySelector("#ferr"), movebar = v.querySelector("#fmovebar");
    let cwd = "", moving = null;
    const fileErr = m => { ferr.textContent = m || ""; ferr.hidden = !m; };
    const fmtSize = n => n == null ? "" :
      n >= (1 << 30) ? (n / (1 << 30)).toFixed(1) + " GB" :
      n >= (1 << 20) ? (n / (1 << 20)).toFixed(1) + " MB" :
      n >= 1024 ? Math.round(n / 1024) + " KB" : n + " B";
    const fmtDate = t => {
      const d = new Date(t * 1000);
      return d.toLocaleDateString() + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    };
    const syncMove = () => {
      movebar.hidden = !moving;
      if (moving) v.querySelector("#fmovemsg").textContent =
        `Moving “${moving.name}” — open the destination folder, then`;
    };
    const frow = (f, isDir) => {
      const p = (cwd ? cwd + "/" : "") + f.name;
      const r = el(`<div class="frow">
        <span class="fic">${isDir ? ICON.files : '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>'}</span>
        <a class="fname" href="${isDir ? "#/files" : "/api/file-dl?path=" + encodeURIComponent(p)}">${esc(f.name)}</a>
        <span class="fmeta">${fmtSize(f.size)}</span>
        <span class="fmeta fdate">${fmtDate(f.mtime)}</span>
        <span class="facts">
          <button class="ghost small" type="button" data-a="move">move</button>
          <button class="ghost small" type="button" data-a="ren">rename</button>
          <button class="ghost small" type="button" data-a="del">delete</button>
        </span></div>`);
      if (isDir) r.querySelector(".fname").onclick = e => { e.preventDefault(); loadFiles(p); };
      r.querySelector("[data-a=move]").onclick = () => { moving = { path: p, name: f.name }; syncMove(); };
      r.querySelector("[data-a=ren]").onclick = async () => {
        const nn = (prompt("Rename “" + f.name + "” to:", f.name) || "").trim();
        if (!nn || nn === f.name) return;
        if (nn.includes("/")) return fileErr("no slashes in names");
        try { await api("/api/file-op", { op: "rename", path: p, dest: (cwd ? cwd + "/" : "") + nn }); loadFiles(cwd); }
        catch (e) { fileErr(e.message); }
      };
      r.querySelector("[data-a=del]").onclick = async () => {
        if (!confirm("Delete “" + f.name + "”" + (isDir ? " and everything inside it?" : "?"))) return;
        try { await api("/api/file-op", { op: "delete", path: p, recursive: true }); loadFiles(cwd); }
        catch (e) { fileErr(e.message); }
      };
      return r;
    };
    loadFiles = async (path) => {
      fileErr("");
      try {
        const r = await api("/api/files?path=" + encodeURIComponent(path == null ? cwd : path));
        cwd = r.path;
        crumbs.replaceChildren();
        let acc = "";
        const mk = (label, target) => {
          const a = el(`<a href="#/files">${esc(label)}</a>`);
          a.onclick = e => { e.preventDefault(); loadFiles(target); };
          crumbs.appendChild(a);
        };
        mk("work", "");
        (cwd ? cwd.split("/") : []).forEach(seg => {
          crumbs.appendChild(el(`<span class="sep">/</span>`));
          acc = acc ? acc + "/" + seg : seg;
          mk(seg, acc);
        });
        flist.replaceChildren();
        r.dirs.forEach(d => flist.appendChild(frow(d, true)));
        r.files.forEach(f => flist.appendChild(frow(f, false)));
        if (!r.dirs.length && !r.files.length)
          flist.appendChild(el(`<p class="note">Empty folder.</p>`));
        if (r.truncated)
          flist.appendChild(el(`<p class="note">…listing truncated at 2000 entries.</p>`));
        syncMove();
      } catch (e) { fileErr(e.message); }
    };
    v.querySelector("#fmovehere").onclick = async () => {
      if (!moving) return;
      try {
        await api("/api/file-op", { op: "move", path: moving.path, dest: cwd });
        moving = null; syncMove(); loadFiles(cwd);
      } catch (e) { fileErr(e.message); }
    };
    v.querySelector("#fmovecancel").onclick = () => { moving = null; syncMove(); };
    v.querySelector("#fmkdir").onclick = async () => {
      const name = (prompt("New folder name:") || "").trim();
      if (!name) return;
      try { await api("/api/file-op", { op: "mkdir", path: cwd, name: name }); loadFiles(cwd); }
      catch (e) { fileErr(e.message); }
    };
    const fupin = v.querySelector("#fupin");
    v.querySelector("#fupbtn").onclick = () => fupin.click();
    fupin.onchange = async () => {
      for (const file of fupin.files) {
        fileErr("uploading " + file.name + "…"); // reuse the line as progress
        try {
          const res = await fetch("/api/file-up?path=" + encodeURIComponent(cwd) +
            "&name=" + encodeURIComponent(file.name), { method: "POST", body: file });
          if (!res.ok) throw new Error((await res.json()).error || "upload failed");
        } catch (e) { fileErr(e.message); fupin.value = ""; return; }
      }
      fupin.value = "";
      fileErr("");
      loadFiles(cwd);
    };
  }
  v.querySelector("#logview").onclick = async () => {
    const box = v.querySelector("#logbox"); box.hidden = false; box.textContent = "…";
    try {
      const r = await fetch("/api/logs?name=" + encodeURIComponent(v.querySelector("#logsel").value) + "&lines=150");
      box.textContent = (await r.json()).text;
    } catch (e) { box.textContent = e.message; }
  };
  {
    const tlsnote = v.querySelector("#tlsnote");
    const host = location.hostname;
    const link = v.querySelector("#httpslink");
    const a = el(`<a href="https://${host}/">https://${host}/</a>`);
    link.appendChild(a);
    v.querySelector("#lnxcmd").textContent = `curl -s http://${host}/install-ca.sh | sudo sh`;
    if (location.protocol === "https:") {
      tlsnote.textContent = "✓ This connection is secure.";
      v.querySelector("#camobile").parentNode.querySelectorAll(".btn").forEach(b => b.classList.add("done"));
    } else {
      tlsnote.textContent = "Install this box’s certificate once per device to get a secure padlock — no more browser warnings. Pick your device below.";
    }
  }
  api("/api/update").then(u => {
    v.querySelector("#updstate").textContent = "updates: " + u.state + (u.applied ? ` (applied ${u.applied})` : "");
    if (u.state === "update available") {
      v.querySelector("#updnow").hidden = false;
      addAlert("warn", "A system update is available — install it from the System page.");
    }
  }).catch(() => {});
  v.querySelector("#updnow").onclick = async () => {
    const b = v.querySelector("#updnow"); busy(b, true);
    v.querySelector("#updstate").textContent = "updates: applying (takes a few minutes)…";
    try { const r = await api("/api/update-now", {});
      v.querySelector("#updstate").textContent = "updates: " + (r.ok ? "applied — verified" : "FAILED: " + r.detail.slice(-120));
    } catch (e) { v.querySelector("#updstate").textContent = "updates: " + e.message; }
    busy(b, false);
  };
  v.querySelector("#repair").onclick = async () => {
    const msg = v.querySelector("#mmsg"); msg.hidden = false; msg.textContent = "repairing…";
    try {
      const r = await api("/api/repair-access", {});
      msg.textContent = r.actions.join("; ") + ".";
    } catch (e) { msg.textContent = e.message; }
  };
  v.querySelector("#reboot").onclick = async () => {
    if (!confirm("Reboot the box? It restores its last saved state and is back in about a minute.")) return;
    const msg = v.querySelector("#mmsg"); msg.hidden = false;
    try {
      await api("/api/reboot", {});
      msg.textContent = "Rebooting — this page will reconnect when the box is back.";
      setTimeout(() => { const t = setInterval(async () => {
        try { await api("/api/state"); clearInterval(t); location.reload(); } catch (e) {}
      }, 5000); }, 20000);
    } catch (e) { msg.textContent = e.message; }
  };
  v.querySelector("#rebootfw").onclick = async () => {
    if (!confirm("Reboot into the BIOS/UEFI setup? Connect a monitor + keyboard to the box first — it will stop at the firmware screen, not boot pipeOS.")) return;
    const msg = v.querySelector("#mmsg"); msg.hidden = false; msg.textContent = "arming firmware setup…";
    try {
      const r = await api("/api/reboot-firmware", {});
      msg.textContent = r.note || "Rebooting into firmware setup.";
    } catch (e) { msg.textContent = e.message; }
  };
  v.querySelector("#chpw").onclick = async () => {
    const msg = v.querySelector("#pwmsg"); msg.hidden = false; msg.textContent = "…";
    try {
      await api("/api/password", { current: v.querySelector("#cur").value, new: v.querySelector("#new").value });
      msg.textContent = "Password changed.";
    } catch (e) { msg.textContent = e.message; }
  };
  // ---- system metrics tiles + history charts ----
  const mbox = v.querySelector("#metrics");
  let lastNcpu = null, mspan = "1h";
  const renderMetrics = (m) => {
    lastNcpu = m.ncpu || lastNcpu;
    const tile = (k, val, note) => `<div class="tile"><div class="k">${k}</div><div class="val">${val}</div>${note ? `<div class="note">${note}</div>` : ""}</div>`;
    const memUsed = (m.mem_total_mb != null && m.mem_avail_mb != null) ? m.mem_total_mb - m.mem_avail_mb : null;
    const memPct = (memUsed != null && m.mem_total_mb) ? Math.round(memUsed * 100 / m.mem_total_mb) : null;
    mbox.innerHTML =
      tile("CPU load", m.load1 == null ? "?" : m.load1.toFixed(2),
        (m.ncpu ? m.ncpu + " cores" : "") + (m.load5 != null ? " · 5m " + m.load5.toFixed(2) : "")) +
      tile("Memory", memPct == null ? "?" : memPct + "%",
        memUsed != null ? (memUsed / 1024).toFixed(1) + " / " + (m.mem_total_mb / 1024).toFixed(1) + " GB" : "") +
      tile("CPU temp", m.temp_c == null ? "—" : m.temp_c + "°C", m.temp_c == null ? "no sensor" : "") +
      tile("RAM disk /", m.root_pct == null ? "?" : m.root_pct + "%", "root fs lives in RAM") +
      tile("Work disk", m.work_pct == null ? "?" : m.work_pct + "%",
        m.work_free_mb != null ? Math.round(m.work_free_mb / 1024) + " GB free" : "");
  };
  const drawSysCharts = (h) => {
    const o = { t0: h.t0, interval: h.interval_s };
    chart(v.querySelector("#ch-cpu"), [{ data: h.cpu, color: "var(--accent)" }],
      Object.assign({ ref: lastNcpu }, o));
    chart(v.querySelector("#ch-mem"), [{ data: h.mem_pct, color: "var(--accent)", fill: true }],
      Object.assign({ unit: "%", ymax: 100 }, o));
    chart(v.querySelector("#ch-temp"), [{ data: h.temp, color: "var(--warn)" }],
      Object.assign({ unit: "°" }, o));
    chart(v.querySelector("#ch-disk"), [
      { data: h.work_pct, color: "var(--accent)", label: "/work" },
      { data: h.root_pct, color: "var(--warn)", label: "root (RAM)" },
    ], Object.assign({ unit: "%", ymax: 100 }, o));
  };
  const pollSystem = async () => {
    try { renderMetrics(await api("/api/metrics")); }
    catch (e) { mbox.innerHTML = `<div class="tile"><div class="k">Metrics</div><div class="val small">${esc(e.message)}</div></div>`; }
    try { drawSysCharts(await api("/api/metrics-history?span=" + mspan)); } catch (e) {}
  };
  v.querySelectorAll("[data-mspan]").forEach(b => {
    if (b.dataset.mspan === mspan) b.classList.add("active");
    b.onclick = () => {
      mspan = b.dataset.mspan;
      v.querySelectorAll("[data-mspan]").forEach(x => x.classList.toggle("active", x === b));
      pollSystem();
    };
  });
  // ---- network tiles + traffic chart ----
  const pollNetwork = async () => {
    try {
      const m = await api("/api/metrics");
      v.querySelector("#nip").textContent = m.ip || "?";
      v.querySelector("#nif").textContent = m.iface || "";
      v.querySelector("#nrx").textContent = fmtBps(m.rx_bps);
      v.querySelector("#ntx").textContent = fmtBps(m.tx_bps);
      const gb = b => b == null ? "?" : (b / 1073741824).toFixed(2) + " GB";
      v.querySelector("#ntot").textContent =
        "since boot: ↓ " + gb(m.rx_total) + " · ↑ " + gb(m.tx_total);
    } catch (e) {}
    try {
      const h = await api("/api/metrics-history?span=1h");
      chart(v.querySelector("#ch-net"), [
        { data: h.rx_bps, color: "var(--accent)", label: "down", fill: true },
        { data: h.tx_bps, color: "var(--ok)", label: "up" },
      ], { t0: h.t0, interval: h.interval_s, unit: "bps" });
    } catch (e) {}
  };
  // ---- view router: show one section at a time, driven by the URL hash ----
  // Views with live data register a poller (runs only while on screen) or a
  // lazy loader (runs on first visit).
  const POLLERS = { system: pollSystem, network: pollNetwork };
  const LAZY = { files: () => loadFiles(""), pipe: loadPipe };
  const seen = {};
  let viewTimer = null;
  const views = v.querySelectorAll("[data-view]");
  const navs = v.querySelectorAll("[data-nav]");
  const names = Array.from(views).map(s => s.dataset.view);
  const show = (name) => {
    if (!names.includes(name)) name = names[0];
    views.forEach(s => { s.hidden = s.dataset.view !== name; });
    navs.forEach(a => a.classList.toggle("active", a.dataset.nav === name));
    const c = v.querySelector(".content"); if (c) c.scrollTop = 0;
    if (viewTimer) { clearInterval(viewTimer); viewTimer = null; }
    const poll = POLLERS[name];
    if (poll) {
      poll();
      viewTimer = setInterval(() => { v.isConnected ? poll() : clearInterval(viewTimer); }, 5000);
    }
    if (!seen[name] && LAZY[name]) { seen[name] = true; LAZY[name](); }
  };
  const route = () => show((location.hash.match(/^#\/(\w+)/) || [])[1] || names[0]);
  window.addEventListener("hashchange", route);
  route();
  app.replaceChildren(v);
}

boot();
