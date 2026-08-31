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
  const navItem = (id, label) => `<a data-nav="${id}" href="#/${id}"><span class="ic"></span>${label}</a>`;
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
        <div class="card">
          <p style="margin-top:0">Last boot: <span class="${vcls}">${esc(verdict)}</span></p>
          <div>${running}</div>
          <details><summary class="note">Full boot report</summary>
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

      <section data-view="services" hidden>
        <div class="viewhead"><h1>Services</h1></div>
        <div class="card">${rows}<p class="err" id="serr" hidden></p></div>
        ${st.services.pipe ? `
        <div class="card" id="pipecard">
          <h2>pipe messaging</h2>
          <p class="note" id="pmsg">loading…</p>
          <div id="pipesignin" hidden>
            <label for="pkey2">One-time key from pipe.online</label>
            <input id="pkey2" type="password" autocomplete="off">
            <button id="pgo2">Sign in</button>
          </div>
          <label for="cid">Team board (cohort id, digits; blank to leave)</label>
          <input id="cid" type="text">
          <button id="cgo2" class="ghost">Set cohort</button>
          <p class="note" id="cmsg2" hidden></p>
        </div>` : ""}
      </section>

      <section data-view="network" hidden>
        <div class="viewhead"><h1>Network</h1></div>
        <div class="card">
          <h2>Secure access (HTTPS)</h2>
          <p class="note" id="tlsnote">checking…</p>
          <a class="btn ghost" id="camobile" href="/pipeos-ca.mobileconfig">iPhone / iPad: install profile</a>
          <a class="btn ghost" id="cadl" href="/ca.crt" download>Everyone else: download certificate</a>
          <details><summary class="note">How to install it (once per device)</summary>
            <p class="note" style="line-height:1.5">
            <b>iPhone/iPad:</b> tap the profile above → Settings offers to install it → then Settings › General › About › Certificate Trust Settings → turn it on.<br>
            <b>Mac:</b> open the .crt → Keychain Access → double-click “pipeOS … CA” → Trust → “Always Trust”.<br>
            <b>Windows:</b> open the .crt → Install Certificate → Local Machine → Trusted Root Certification Authorities.<br>
            <b>Android:</b> Settings › Security › Encryption &amp; credentials › Install a certificate › CA certificate → pick the .crt.<br>
            Then reload over <span id="httpslink"></span> and you’ll see the padlock.</p>
          </details>
        </div>
      </section>

      <section data-view="system" hidden>
        <div class="viewhead"><h1>System</h1></div>
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
  const chatgo = v.querySelector("#chatgo");
  if (chatgo) {
    const send = async () => {
      const inp = v.querySelector("#chatmsg"), log = v.querySelector("#chatlog"), note = v.querySelector("#chatnote");
      const msg = inp.value.trim();
      if (!msg) return;
      log.appendChild(el(`<p><strong>you:</strong> ${esc(msg)}</p>`));
      inp.value = ""; busy(chatgo, true);
      note.hidden = false; note.textContent = "thinking…";
      try {
        const r = await api("/api/chat", { message: msg });
        log.appendChild(el(`<p><strong>${esc(st.nick || "box")}:</strong> ${esc(r.reply)}</p>`));
        note.hidden = true;
      } catch (e) { note.textContent = e.message; }
      busy(chatgo, false);
      log.scrollTop = log.scrollHeight;
    };
    chatgo.onclick = send;
    v.querySelector("#chatmsg").addEventListener("keydown", e => { if (e.key === "Enter") send(); });
  }
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
    api("/api/stream").then(s => {
      smode.value = s.mode || "media"; applyMode();
      v.querySelector("#ssrc").value = s.src; v.querySelector("#surl").value = s.url || "";
      v.querySelector("#sres").value = s.res || ""; v.querySelector("#sfps").value = s.fps || "";
      v.querySelector("#sbitrate").value = s.bitrate || ""; v.querySelector("#svaapi").checked = !!s.vaapi;
      v.querySelector("#sargs").value = s.args;
      tbox.textContent = "";
      const rows = (s.targets || []).filter(t => t.url || t.key_set || t.name);
      if (rows.length) rows.forEach(addRow);
      else { addRow({ name: "YouTube", url: PROVIDERS.YouTube, on: true }); addRow({ name: "Twitch", url: PROVIDERS.Twitch, on: true }); }
    }).catch(() => {});
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
  const pcard = v.querySelector("#pipecard");
  if (pcard) {
    api("/api/pipe").then(p => {
      const msg = v.querySelector("#pmsg");
      if (p.authed) msg.textContent = `Signed in as ${p.nick}. Owner: ${p.owner || "(unset)"}. Cohort: ${p.cohort || "(none)"}.`;
      else { msg.textContent = "Not signed in — the box cannot send or receive DMs."; v.querySelector("#pipesignin").hidden = false; }
      v.querySelector("#cid").value = p.cohort || "";
    }).catch(() => {});
    const pgo2 = v.querySelector("#pgo2");
    pgo2.onclick = async () => {
      busy(pgo2, true);
      try { const r = await api("/api/pipe-key", { key: v.querySelector("#pkey2").value.trim() });
        v.querySelector("#pmsg").textContent = "Signed in as " + r.nick + "."; v.querySelector("#pipesignin").hidden = true;
      } catch (e) { v.querySelector("#pmsg").textContent = e.message; }
      busy(pgo2, false);
    };
    v.querySelector("#cgo2").onclick = async () => {
      const m = v.querySelector("#cmsg2"); m.hidden = false; m.textContent = "…";
      try { const r = await api("/api/cohort", { id: v.querySelector("#cid").value.trim() });
        m.textContent = r.cohort ? "Cohort set to " + r.cohort + "." : "Cohort cleared.";
      } catch (e) { m.textContent = e.message; }
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
    if (location.protocol === "https:") {
      tlsnote.textContent = "✓ This connection is secure.";
      v.querySelector("#camobile").parentNode.querySelectorAll(".btn").forEach(b => b.classList.add("done"));
    } else {
      tlsnote.textContent = "Install this box’s certificate once per device to get a secure padlock — no more browser warnings. Pick your device below.";
    }
  }
  api("/api/update").then(u => {
    v.querySelector("#updstate").textContent = "updates: " + u.state + (u.applied ? ` (applied ${u.applied})` : "");
    if (u.state === "update available") v.querySelector("#updnow").hidden = false;
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
  // ---- view router: show one section at a time, driven by the URL hash ----
  const views = v.querySelectorAll("[data-view]");
  const navs = v.querySelectorAll("[data-nav]");
  const names = Array.from(views).map(s => s.dataset.view);
  const show = (name) => {
    if (!names.includes(name)) name = names[0];
    views.forEach(s => { s.hidden = s.dataset.view !== name; });
    navs.forEach(a => a.classList.toggle("active", a.dataset.nav === name));
    const c = v.querySelector(".content"); if (c) c.scrollTop = 0;
  };
  const route = () => show((location.hash.match(/^#\/(\w+)/) || [])[1] || names[0]);
  window.addEventListener("hashchange", route);
  route();
  app.replaceChildren(v);
}

boot();
