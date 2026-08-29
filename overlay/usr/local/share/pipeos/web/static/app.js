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
  { key: "agy", name: "Antigravity", desc: "The agy coding agent (if installed on this image).", def: false },
  { key: "pipe", name: "pipe messaging", desc: "Talk to the box by DM from anywhere, via pipe.online.", def: false },
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
  const v = el(`<div>
    <div class="topbar">
      <div><h1>${esc(st.nick || st.hostname)}</h1>
        <span class="note">up ${fmtUptime(st.uptime_s)} · disk ${st.work_pct == null ? "?" : st.work_pct + "% used"}${st.work_free_mb != null ? " (" + Math.round(st.work_free_mb / 1024) + " GB free)" : ""}</span>
      </div>
      <button id="logout" class="ghost">Sign out</button>
    </div>
    <div class="card">
      <p style="margin-top:0">Last boot: <span class="${vcls}">${esc(verdict)}</span></p>
      <details><summary class="note">Full boot report</summary>
        <pre class="report">${esc(st.boot_report || "(none this boot)")}</pre></details>
    </div>
    <h2>Services</h2>
    <div class="card">${rows}<p class="err" id="serr" hidden></p></div>
    <p>${running}</p>
    <h2>Maintenance</h2>
    <div class="card">
      <button id="save" style="margin-top:0">Save state to boot media now</button>
      <p class="note">The box also saves automatically every 15 minutes.</p>
      <details><summary class="note">Change admin password</summary>
        <label for="cur">Current password</label><input id="cur" type="password">
        <label for="new">New password</label><input id="new" type="password" minlength="8">
        <button id="chpw">Change password</button>
        <p class="note" id="pwmsg" hidden></p>
      </details>
    </div>
  </div>`);
  v.querySelector("#logout").onclick = async () => { try { await api("/api/logout", {}); } catch (e) {} boot(); };
  v.querySelectorAll("input[type=checkbox]").forEach(c => {
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
  v.querySelector("#save").onclick = async () => {
    const b = v.querySelector("#save"); busy(b, true);
    try { const r = await api("/api/save", {}); if (!r.ok) alert("Save failed:\n" + r.detail); }
    catch (e) { alert(e.message); }
    busy(b, false);
  };
  v.querySelector("#chpw").onclick = async () => {
    const msg = v.querySelector("#pwmsg"); msg.hidden = false; msg.textContent = "…";
    try {
      await api("/api/password", { current: v.querySelector("#cur").value, new: v.querySelector("#new").value });
      msg.textContent = "Password changed.";
    } catch (e) { msg.textContent = e.message; }
  };
  app.replaceChildren(v);
}

boot();
