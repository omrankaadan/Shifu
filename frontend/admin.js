// /static/admin.js
// Admin page logic: data fetch, tabs, search, CSV export, IP abuse view.
// Depends on /static/script.js for API_BASE, showNotification, smoothRedirect, showPageLoader/hidePageLoader, logout()

let CURRENT_TAB = "overview";
let LAST_TABLE_ID = "recentTable";

// -------------------------
// Helpers
// -------------------------
function fmtDateTime(s){
  if (!s) return "—";
  // Keep as-is if backend returns ISO
  return String(s).replace("T", " ").replace("Z", "");
}

function esc(s){
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setLastRefresh(){
  const el = document.getElementById("lastRefresh");
  if (!el) return;
  const now = new Date();
  el.innerText = now.toLocaleString();
}

function setPanel(title, sub){
  const t = document.getElementById("panelTitle");
  const s = document.getElementById("panelSub");
  if (t) t.innerText = title;
  if (s) s.innerText = sub;
}

function badge(status){
  const st = String(status ?? "").toLowerCase();
  if (st === "active" || st === "paid" || st === "success") return `<span class="badge ok">${esc(status)}</span>`;
  if (st === "canceled" || st === "cancelled" || st === "failed") return `<span class="badge bad">${esc(status)}</span>`;
  if (st) return `<span class="badge warn">${esc(status)}</span>`;
  return "—";
}

// -------------------------
// Auth + boot
// -------------------------
async function requireAdmin(){
  try{
    const res = await fetch(API_BASE + "/me", { credentials: "include" });
    if (!res.ok){
      smoothRedirect("/static/login.html", 120);
      return null;
    }
    const me = await res.json();

    // backend returns is_admin
    if (!me.is_admin){
      showNotification("Admin access required.");
      smoothRedirect("/static/premium.html", 250);
      return null;
    }

    const emailEl = document.getElementById("adminEmail");
    if (emailEl) emailEl.innerText = me.email;

    return me;
  }catch(e){
    console.error(e);
    showNotification("Failed to validate admin session.");
    smoothRedirect("/static/login.html", 120);
    return null;
  }
}

// -------------------------
// Tabs
// -------------------------
function setActiveTab(name){
  CURRENT_TAB = name;
  document.querySelectorAll(".nav-btn").forEach(btn=>{
    const active = btn.getAttribute("data-tab") === name;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true":"false");
  });

  document.querySelectorAll(".tab-panel").forEach(p=>p.classList.remove("active"));
  const panel = document.getElementById("tab-" + name);
  if (panel) panel.classList.add("active");

  // panel copy
  const meta = {
    overview: ["Overview", "Platform usage + operational metrics"],
    users: ["Users", "Accounts overview (search + export)"],
    pdfs: ["PDFs", "Uploads overview (search + export)"],
    abuse: ["IP Abuse", "Suspicious IPs based on auth events (search + export)"],
  };
  const m = meta[name] || ["Admin", "—"];
  setPanel(m[0], m[1]);

  // set export button label
  const eb = document.getElementById("exportBtn");
  if (eb){
    const kindMap = {overview:"overview", users:"users", pdfs:"pdfs", abuse:"ip_abuse"};
    eb.innerText = "Export " + (kindMap[name] || "CSV");
  }

  // track current table for search
  LAST_TABLE_ID = ({
    overview: "recentTable",
    users: "usersTable",
    pdfs: "pdfsTable",
    abuse: "abuseTable"
  })[name] || "recentTable";

  // load section data (lazy)
  refreshTab(name);
}

function initTabs(){
  document.querySelectorAll(".nav-btn").forEach(btn=>{
    btn.addEventListener("click", ()=> setActiveTab(btn.getAttribute("data-tab")));
  });
}

// -------------------------
// Search (filters visible rows in the current table)
// -------------------------
function initSearch(){
  const box = document.getElementById("searchBox");
  if (!box) return;

  const run = ()=>{
    const q = (box.value || "").trim().toLowerCase();
    const tbl = document.getElementById(LAST_TABLE_ID);
    if (!tbl) return;
    const rows = tbl.querySelectorAll("tbody tr");
    rows.forEach(r=>{
      const t = r.innerText.toLowerCase();
      const show = !q || t.includes(q);
      r.style.display = show ? "" : "none";
    });
  };

  box.addEventListener("input", run);
}

// -------------------------
// CSV exports
// -------------------------
function exportCsv(kind){
  const map = {
    overview: "overview",
    users: "users",
    pdfs: "pdfs",
    auth_events: "auth_events",
    ip_abuse: "ip_abuse"
  };
  const k = map[kind] || kind || "overview";
  // triggers direct download
  window.location.href = API_BASE + "/admin/export/" + encodeURIComponent(k) + ".csv";
}

function exportCurrent(){
  const map = {overview:"overview", users:"users", pdfs:"pdfs", abuse:"ip_abuse"};
  exportCsv(map[CURRENT_TAB] || "overview");
}

// -------------------------
// Data loads
// -------------------------
async function loadOverview(){
  const res = await fetch(API_BASE + "/admin/overview", { credentials: "include" });
  const data = await res.json().catch(()=> ({}));
  if (!res.ok || !data.success) throw new Error(data.detail || "Failed to load overview");

  // stats cards
  document.getElementById("statUsers").innerText = String(data.cards.users_total ?? "—");

  document.getElementById("statPdfs").innerText = String(data.cards.pdfs_total ?? "—");
  document.getElementById("statPdfsSub").innerText = `${data.cards.pdfs_14d ?? 0} uploads (14d)`;

  // spark bars (14d): normalized per series
  const spark = document.getElementById("spark");
  if (spark){
    spark.innerHTML = "";
    const series = data.timeseries || [];
    const s1 = series.map(x=> x.signups || 0);
    const s2 = series.map(x=> x.uploads || 0);

    const max = Math.max(1, ...s1, ...s2);
    const addBars = (arr, cls)=>{
      arr.forEach(v=>{
        const h = Math.max(2, Math.round((v / max) * 68));
        const bar = document.createElement("div");
        bar.className = "bar " + cls;
        bar.style.height = h + "px";
        bar.title = String(v);
        spark.appendChild(bar);
      });
    };
    // interleave per day: 2 bars per day
    for (let i=0;i<series.length;i++){
      const day = series[i].day || "";
      const items = [
        {v: s1[i], cls:"b1"},
        {v: s2[i], cls:"b2"},
      ];
      items.forEach(it=>{
        const h = Math.max(2, Math.round((it.v / max) * 68));
        const bar = document.createElement("div");
        bar.className = "bar " + it.cls;
        bar.style.height = h + "px";
        bar.title = `${day}: ${it.v}`;
        spark.appendChild(bar);
      });
    }
  }

  // recent activity
  const body = document.getElementById("recentBody");
  if (body){
    const items = Array.isArray(data.recent) ? data.recent : [];
    if (!items.length){
      body.innerHTML = `<tr><td colspan="5" class="muted">No recent activity</td></tr>`;
    } else {
      body.innerHTML = items.map(it=>{
        return `<tr>
          <td>${esc(fmtDateTime(it.time))}</td>
          <td>${esc(it.type)}</td>
          <td>${esc(it.actor || "—")}</td>
          <td>${esc(it.ip || "—")}</td>
          <td>${esc(it.detail || "—")}</td>
        </tr>`;
      }).join("");
    }
  }
}

async function loadUsers(){
  const res = await fetch(API_BASE + "/admin/users?limit=400", { credentials: "include" });
  const data = await res.json().catch(()=> ({}));
  if (!res.ok || !data.success) throw new Error(data.detail || "Failed to load users");

  const body = document.getElementById("usersBody");
  const rows = data.items || [];
  body.innerHTML = rows.length ? rows.map(u=>{
    const blocked = String(u.status || "active").toLowerCase() === "blocked";
    return `
    <tr data-user-id="${esc(u.id)}" data-email="${esc(u.email)}" class="user-row">
      <td>${esc(u.id)}</td>
      <td>${esc(u.email)}</td>
      <td>${esc(u.auth_provider || "—")}</td>
      <td>${u.email_verified ? `<span class="badge ok">yes</span>` : `<span class="badge warn">no</span>`}</td>
      <td>${esc(fmtDateTime(u.created_at))}</td>
      <td>${blocked ? `<span class="badge bad">blocked</span>` : `<span class="badge ok">active</span>`}</td>
      <td class="actions-cell">
        <div class="row-actions">
          <button type="button" class="btn mini ${blocked ? "ghost" : "danger"} act-btn"
            data-action="status" data-user-id="${esc(u.id)}" data-email="${esc(u.email)}"
            data-next-status="${blocked ? "active" : "blocked"}">${blocked ? "Unblock" : "Block"}</button>
          <button type="button" class="btn mini ghost act-btn"
            data-action="reset" data-user-id="${esc(u.id)}" data-email="${esc(u.email)}">Reset PW</button>
          <button type="button" class="btn mini ghost act-btn"
            data-action="logout" data-user-id="${esc(u.id)}" data-email="${esc(u.email)}">Force logout</button>
          ${u.email_verified ? "" : `<button type="button" class="btn mini ghost act-btn"
            data-action="verify" data-user-id="${esc(u.id)}" data-email="${esc(u.email)}">Verify email</button>`}
        </div>
      </td>
    </tr>
  `;
  }).join("") : `<tr><td colspan="7" class="muted">No users found</td></tr>`;
}

// -------------------------
// User action buttons (block/unblock, reset, logout, verify)
// -------------------------
const ACTION_CONFIG = {
  status: {
    confirm: (email, nextStatus)=> `Are you sure you want to ${nextStatus === "blocked" ? "block" : "unblock"} ${email}?`,
    run: (id, btn)=> {
      const nextStatus = btn.getAttribute("data-next-status");
      return fetch(API_BASE + `/admin/users/${encodeURIComponent(id)}/status`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
      });
    },
    successMsg: (btn)=> btn.getAttribute("data-next-status") === "blocked" ? "User blocked." : "User unblocked.",
    busyMsg: (btn)=> btn.getAttribute("data-next-status") === "blocked" ? "Blocking…" : "Unblocking…",
  },
  reset: {
    confirm: (email)=> `Send a forced password reset to ${email}? Their current password will stop working immediately.`,
    run: (id)=> fetch(API_BASE + `/admin/users/${encodeURIComponent(id)}/force-password-reset`, {
      method: "POST", credentials: "include",
    }),
    successMsg: ()=> "Password reset triggered.",
    busyMsg: ()=> "Resetting…",
  },
  logout: {
    confirm: (email)=> `Force logout ${email} from all active sessions?`,
    run: (id)=> fetch(API_BASE + `/admin/users/${encodeURIComponent(id)}/force-logout`, {
      method: "POST", credentials: "include",
    }),
    successMsg: ()=> "Sessions revoked.",
    busyMsg: ()=> "Logging out…",
  },
  verify: {
    confirm: (email)=> `Manually mark ${email}'s email as verified?`,
    run: (id)=> fetch(API_BASE + `/admin/users/${encodeURIComponent(id)}/verify-email`, {
      method: "POST", credentials: "include",
    }),
    successMsg: ()=> "Email marked as verified.",
    busyMsg: ()=> "Verifying…",
  },
};

async function runUserAction(action, btn){
  const cfg = ACTION_CONFIG[action];
  if (!cfg) return;

  const original = btn.innerText;
  btn.disabled = true;
  btn.innerText = cfg.busyMsg(btn);

  try{
    const res = await cfg.run(btn.getAttribute("data-user-id"), btn);
    const data = await res.json().catch(()=> ({}));
    if (!res.ok || !data.success) throw new Error(data.detail || "Action failed");

    showNotification(cfg.successMsg(btn));
    await loadUsers();
  }catch(e){
    console.error(e);
    showNotification(String(e.message || e));
    btn.disabled = false;
    btn.innerText = original;
  }
}

function initUserActions(){
  const body = document.getElementById("usersBody");
  if (!body) return;

  body.addEventListener("click", (ev)=>{
    const btn = ev.target.closest(".act-btn");
    if (btn){
      ev.stopPropagation();
      const action = btn.getAttribute("data-action");
      const email = btn.getAttribute("data-email");
      const cfg = ACTION_CONFIG[action];
      if (!cfg) return;

      const nextStatus = btn.getAttribute("data-next-status");
      if (!confirm(cfg.confirm(email, nextStatus))) return;

      runUserAction(action, btn);
      return;
    }

    // Row click (not on a button) -> open PDF history modal
    const row = ev.target.closest(".user-row");
    if (row){
      openUserPdfModal(row.getAttribute("data-user-id"), row.getAttribute("data-email"));
    }
  });
}

// -------------------------
// User PDF history modal
// -------------------------
async function openUserPdfModal(userId, email){
  let modal = document.getElementById("userPdfModal");
  if (!modal){
    modal = document.createElement("div");
    modal.id = "userPdfModal";
    modal.className = "modal-overlay";
    modal.innerHTML = `
      <div class="modal-card glass">
        <div class="modal-head">
          <div>
            <h3 id="userPdfModalTitle">PDF history</h3>
            <p class="muted tiny" id="userPdfModalSub">—</p>
          </div>
          <button class="btn mini ghost" type="button" id="userPdfModalClose">✕</button>
        </div>
        <div class="modal-actions">
          <button class="btn ghost danger" type="button" id="userPdfModalBulkDelete">Delete all PDFs for this user</button>
        </div>
        <div class="modal-body">
          <div class="table-wrap">
            <table class="table" id="userPdfModalTable">
              <thead>
                <tr><th>ID</th><th>Filename</th><th>Uploaded</th><th></th></tr>
              </thead>
              <tbody id="userPdfModalBody">
                <tr><td colspan="4" class="muted">Loading…</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    modal.addEventListener("click", (ev)=>{
      if (ev.target === modal) closeUserPdfModal();
    });
    document.getElementById("userPdfModalClose").addEventListener("click", closeUserPdfModal);
  }

  modal.dataset.userId = userId;
  modal.dataset.email = email;
  document.getElementById("userPdfModalTitle").innerText = "PDF history";
  document.getElementById("userPdfModalSub").innerText = email;
  document.getElementById("userPdfModalBody").innerHTML = `<tr><td colspan="4" class="muted">Loading…</td></tr>`;
  modal.classList.add("open");

  const bulkBtn = document.getElementById("userPdfModalBulkDelete");
  bulkBtn.onclick = ()=> bulkDeleteUserPdfs(userId, email);

  await loadUserPdfModalData(userId);
}

function closeUserPdfModal(){
  const modal = document.getElementById("userPdfModal");
  if (modal) modal.classList.remove("open");
}

async function loadUserPdfModalData(userId){
  try{
    const res = await fetch(API_BASE + `/admin/users/${encodeURIComponent(userId)}/pdfs`, { credentials: "include" });
    const data = await res.json().catch(()=> ({}));
    if (!res.ok || !data.success) throw new Error(data.detail || "Failed to load PDFs");

    const body = document.getElementById("userPdfModalBody");
    const items = data.items || [];
    body.innerHTML = items.length ? items.map(p=>`
      <tr data-pdf-id="${esc(p.id)}">
        <td>${esc(p.id)}</td>
        <td title="${esc(p.filename)}">${esc(p.filename)}</td>
        <td>${esc(fmtDateTime(p.upload_time))}</td>
        <td><button class="btn mini danger" type="button" data-del-pdf-id="${esc(p.id)}">Delete</button></td>
      </tr>
    `).join("") : `<tr><td colspan="4" class="muted">No PDFs uploaded</td></tr>`;

    body.querySelectorAll("[data-del-pdf-id]").forEach(btn=>{
      btn.addEventListener("click", ()=> deleteSinglePdf(btn.getAttribute("data-del-pdf-id"), userId));
    });
  }catch(e){
    console.error(e);
    showNotification(String(e.message || e));
  }
}

async function deleteSinglePdf(pdfId, userId){
  if (!confirm("Delete this PDF? This removes the file and its analysis permanently.")) return;
  try{
    const res = await fetch(API_BASE + `/admin/pdfs/${encodeURIComponent(pdfId)}`, {
      method: "DELETE", credentials: "include",
    });
    const data = await res.json().catch(()=> ({}));
    if (!res.ok || !data.success) throw new Error(data.detail || "Delete failed");
    showNotification("PDF deleted.");
    await loadUserPdfModalData(userId);
    if (CURRENT_TAB === "pdfs") await loadPdfs();
  }catch(e){
    console.error(e);
    showNotification(String(e.message || e));
  }
}

async function bulkDeleteUserPdfs(userId, email){
  if (!confirm(`Delete ALL PDFs for ${email}? This cannot be undone.`)) return;
  try{
    const res = await fetch(API_BASE + `/admin/users/${encodeURIComponent(userId)}/pdfs`, {
      method: "DELETE", credentials: "include",
    });
    const data = await res.json().catch(()=> ({}));
    if (!res.ok || !data.success) throw new Error(data.detail || "Bulk delete failed");
    showNotification(`Deleted ${data.deleted} PDF(s).`);
    await loadUserPdfModalData(userId);
    if (CURRENT_TAB === "pdfs") await loadPdfs();
  }catch(e){
    console.error(e);
    showNotification(String(e.message || e));
  }
}

async function loadPdfs(){
  const res = await fetch(API_BASE + "/admin/pdfs?limit=500", { credentials: "include" });
  const data = await res.json().catch(()=> ({}));
  if (!res.ok || !data.success) throw new Error(data.detail || "Failed to load pdfs");

  const body = document.getElementById("pdfsBody");
  const rows = data.items || [];
  body.innerHTML = rows.length ? rows.map(p=>`
    <tr>
      <td>${esc(p.id)}</td>
      <td>${esc(p.user_email || p.user_id)}</td>
      <td title="${esc(p.filename)}">${esc(p.filename)}</td>
      <td>${esc(fmtDateTime(p.upload_time))}</td>
      <td><button class="btn mini danger" type="button" data-pdf-del-id="${esc(p.id)}">Delete</button></td>
    </tr>
  `).join("") : `<tr><td colspan="5" class="muted">No uploads found</td></tr>`;

  body.querySelectorAll("[data-pdf-del-id]").forEach(btn=>{
    btn.addEventListener("click", ()=> deleteSinglePdfFromTab(btn.getAttribute("data-pdf-del-id")));
  });
}

async function deleteSinglePdfFromTab(pdfId){
  if (!confirm("Delete this PDF? This removes the file and its analysis permanently.")) return;
  try{
    const res = await fetch(API_BASE + `/admin/pdfs/${encodeURIComponent(pdfId)}`, {
      method: "DELETE", credentials: "include",
    });
    const data = await res.json().catch(()=> ({}));
    if (!res.ok || !data.success) throw new Error(data.detail || "Delete failed");
    showNotification("PDF deleted.");
    await loadPdfs();
  }catch(e){
    console.error(e);
    showNotification(String(e.message || e));
  }
}




async function loadAbuse(){
  const res = await fetch(API_BASE + "/admin/ip-abuse?limit=200", { credentials: "include" });
  const data = await res.json().catch(()=> ({}));
  if (!res.ok || !data.success) throw new Error(data.detail || "Failed to load abuse");

  const body = document.getElementById("abuseBody");
  const rows = data.items || [];
  body.innerHTML = rows.length ? rows.map(x=>{
    const risk = String(x.risk || "").toLowerCase();
    const riskBadge = risk === "high" ? "bad" : (risk === "medium" ? "warn" : "ok");
    return `
      <tr>
        <td>${esc(x.ip)}</td>
        <td>${esc(x.failed_1h)}</td>
        <td>${esc(x.failed_24h)}</td>
        <td>${esc(x.signups_24h)}</td>
        <td>${esc(x.unique_emails_24h)}</td>
        <td><span class="badge ${riskBadge}">${esc(x.risk)}</span></td>
      </tr>
    `;
  }).join("") : `<tr><td colspan="6" class="muted">No suspicious IPs detected</td></tr>`;
}

async function refreshTab(name){
  try{
    if (name === "overview") await loadOverview();
    if (name === "users") await loadUsers();
    if (name === "pdfs") await loadPdfs();
    if (name === "abuse") await loadAbuse();
    setLastRefresh();
  }catch(e){
    console.error(e);
    document.getElementById("sysStatus").innerText = "Error";
    showNotification(String(e.message || e));
  }
}

async function refreshAll(){
  document.getElementById("sysStatus").innerText = "OK";
  // Always refresh overview stats first (cards are global)
  await refreshTab("overview");

  // Refresh active tab if different
  if (CURRENT_TAB !== "overview") await refreshTab(CURRENT_TAB);
}

// -------------------------
// Boot
// -------------------------
document.addEventListener("DOMContentLoaded", async ()=>{
  initTabs();
  initSearch();
  initUserActions();

  const me = await requireAdmin();
  if (!me) return;

  // default section
  setActiveTab("overview");
  await refreshAll();
});
fetch(API_BASE + "/admin/overview", { credentials: "include" })
  .then(res => {
    if (res.status === 403 || res.status === 401) {
        window.location.href = "/static/login.html";
    }
  });
