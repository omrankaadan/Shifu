const API_BASE = "http://127.0.0.1:8000";
let currentPdfFilename = "";

function setPdfFrame(filename, page = 1){
    const frame = document.getElementById("pdfFrame");
    const pane = document.getElementById("pdfPane");
    if (!frame || !filename) return;
    const url = API_BASE + "/pdf_file?filename=" + encodeURIComponent(filename) + "#page=" + encodeURIComponent(page);
    frame.src = url;
    if (pane) pane.classList.add("has-file");
}

function jumpToFirstSource(pages){
    if (!pages || !pages.length) return;
    const p = Number(pages[0]);
    if (Number.isFinite(p)) setPdfFrame(currentPdfFilename, p);
}


/* ======================================
   GLOBAL PAGE LOADER
====================================== */
function showPageLoader() {
    let loader = document.getElementById("pageLoader");
    if (!loader) {
        loader = document.createElement("div");
        loader.id = "pageLoader";
        loader.innerHTML = `<div class="page-spinner"></div>`;
        document.body.appendChild(loader);
    }
    loader.classList.add("show");
}

function hidePageLoader() {
    const loader = document.getElementById("pageLoader");
    if (loader) loader.classList.remove("show");
}

/* ======================================
   SMOOTH REDIRECT
====================================== */
function smoothRedirect(url, delay = 120) {
    showPageLoader();
    setTimeout(() => (window.location.href = url), delay);
}


/* ======================================
   SESSION CHECK (INDEX / LOGIN / REGISTER)
====================================== */
function checkSession() {
    fetch(API_BASE + "/me", { credentials: "include" })
        .then((res) => {
            if (res.status === 200) {
                // Only show loader when we actually redirect
                smoothRedirect("/static/premium.html", 120);
            }
        })
        .catch(() => {});
}


/* ======================================
   REGISTER (local)
====================================== */
function register() {
    const email = document.getElementById("email")?.value.trim();
    
    const password = document.getElementById("password")?.value;

    if (!email || !password) {
        showNotification("Email and password are required");
        return;
    }

    startButtonLoading();

    fetch(API_BASE + "/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password })
    })
        .then((res) => res.json().then((d) => ({ ok: res.ok, status: res.status, data: d })))
        .then(({ ok, status, data }) => {
            if (ok && data.success) {
                // If needs verification, tell the user clearly
                if (data.needs_verification) {
                    showlongNotification("Account created! Check your email to verify your account.");
                } else {
                    showNotification("Account created! You can now login.");
                }
                setTimeout(() => smoothRedirect("/static/login.html", 250), 900);
            } else {
                stopButtonLoading();
                showNotification(data.detail || "Registration failed");
            }
        })
        .catch(() => {
            stopButtonLoading();
            showNotification("Server error");
        });
}

/* ======================================
   LOGIN (local password)
====================================== */
function login() {
    const email = document.getElementById("email")?.value.trim();
    const password = document.getElementById("password")?.value;

    if (!email || !password) {
        showNotification("Email and password are required");
        return;
    }

    startButtonLoading();

    fetch(API_BASE + "/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email, password })
    })
        .then((res) => res.json().then((d) => ({ ok: res.ok, status: res.status, data: d })))
        .then(({ ok, status, data }) => {
        if (ok && data.success) {
        if (data.is_admin) {
        smoothRedirect("/static/admin.html", 250);
        } else {
        smoothRedirect("/static/premium.html", 250);
            }
        }
        else {
                stopButtonLoading();
                // Special cases
                if (status === 403 && (data.detail || "").toLowerCase().includes("not verified")) {
                    showlongNotification("Please verify your email first. You can resend the verification email below.");
                    // If login page has resend button, show it
                    const resendWrap = document.getElementById("resendWrap");
                    if (resendWrap) resendWrap.style.display = "block";
                } else if ((data.detail || "").toLowerCase().includes("google")) {
                    showNotification(data.detail);
                } else {
                    showNotification(data.detail || "Invalid credentials");
                }
            }
        })
        .catch(() => {
            stopButtonLoading();
            showNotification("Server error");
        });
}

/* ======================================
   RESEND VERIFICATION
====================================== */
const RESEND_WAIT_SECONDS = 120; // MUST match backend (2 minutes)
let resendTimer = null;

function resendVerification() {
    const email = document.getElementById("email").value.trim();
    if (!email) return;

    fetch(API_BASE + "/resend-verification", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email })
    })
    .then(res => {
        if (res.status === 429) {
            startResendCountdown(RESEND_WAIT_SECONDS);
            throw new Error("Too many requests");
        }
        return res.json();
    })
    .then(data => {
        document.getElementById("resendMessage").innerText =
            "Verification email sent. Please check your inbox.";
localStorage.setItem("resend_ts", Date.now());
        startResendCountdown(RESEND_WAIT_SECONDS);
    })
    .catch(err => {
        if (err.message !== "Too many requests") {
            document.getElementById("resendMessage").innerText =
                "Failed to resend verification email.";
        }
    });
}
function startResendCountdown(seconds) {
    const btn = document.getElementById("resendBtn");
    const msg = document.getElementById("resendMessage");

    let remaining = seconds;
    btn.style.display = "inline-block";
    btn.disabled = true;
    msg.innerText = "";

    clearInterval(resendTimer);

    resendTimer = setInterval(() => {
        const mins = Math.floor(remaining / 60);
        const secs = remaining % 60;

        btn.innerText = `Resend in ${mins}:${secs.toString().padStart(2, "0")}`;

        remaining--;

        if (remaining < 0) {
            clearInterval(resendTimer);
            btn.disabled = false;
            btn.innerText = "Resend verification email";
        }
    }, 1000);
}



/* ======================================
   GOOGLE SIGN-IN (index page)
====================================== */
function initGoogle() {
    // Only initialize if container exists on the current page
    const container = document.getElementById("googleSignIn");
    if (!container) return;

    if (!window.google) {
        setTimeout(initGoogle, 100);
        return;
    }

    google.accounts.id.initialize({
        client_id: "1080853162066-cv8kn77f0smvlu78cs51lu056ujslfd5.apps.googleusercontent.com",
        callback: handleGoogleResponse
    });

    google.accounts.id.renderButton(container, {
        theme: "outline",
        size: "large"
    });
}

function handleGoogleResponse(response) {
    showPageLoader();

    fetch(API_BASE + "/auth/google", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ token: response.credential })
    })
        .then((res) => res.json().then((d) => ({ ok: res.ok, data: d })))
        .then(({ ok, data }) => {
            if (ok && data.success) smoothRedirect("premium.html", 250);
            else {
                hidePageLoader();
                showNotification(data.detail || "Google sign-in failed");
            }
        })
        .catch(() => {
            hidePageLoader();
            showNotification("Server error");
        });
}

/* ======================================
   LOGOUT
====================================== */
function logout() {
    showPageLoader();
    fetch(API_BASE + "/logout", { method: "POST", credentials: "include" })
        .then(() => smoothRedirect("login.html", 250));
}

/* ======================================
   BUTTON LOADING (FORMS)
====================================== */
function startButtonLoading() {
    const btn = document.getElementById("submitBtn");
    if (!btn) return;
    btn.classList.add("loading");
    btn.disabled = true;
}
function stopButtonLoading() {
    const btn = document.getElementById("submitBtn");
    if (!btn) return;
    btn.classList.remove("loading");
    btn.disabled = false;
}

/* ======================================
   NOTIFICATIONS
====================================== */
let notificationTimer = null;

function showNotification(message, duration = 2800){
  let n = document.getElementById("notification");

  if (!n) {
    n = document.createElement("div");
    n.id = "notification";
    n.className = "notification";
    document.body.appendChild(n);
  }

  // Reset state
  n.classList.remove("hide");
  n.classList.remove("show");
  void n.offsetWidth; // force reflow (important)

  n.textContent = message;
  n.classList.add("show");

  clearTimeout(notificationTimer);

  notificationTimer = setTimeout(() => {
    n.classList.remove("show");
    n.classList.add("hide");
  }, duration);
}

/* Optional long version (keeps compatibility) */
function showlongNotification(message){
  showNotification(message, 5500);
}

/* ======================================
   ENTER KEY SUPPORT
====================================== */

/* ======================================
   HOME BUTTON
====================================== */
function goHome() {
    smoothRedirect("/static/index.html", 250);
}

/* ======================================
   PAGE LOAD TRANSITION
====================================== */
window.addEventListener("load", () => {
    // Tiny delay so transitions feel intentional
     if (window.location.hash.startsWith("#/settings")) {
        handleSettingsRoute();
    }
    setTimeout(hidePageLoader, 250);
        const last = localStorage.getItem("resend_ts");
    if (!last) return;

    const elapsed = Math.floor((Date.now() - Number(last)) / 1000);
    if (elapsed < RESEND_WAIT_SECONDS) {
        startResendCountdown(RESEND_WAIT_SECONDS - elapsed);
    }
});





function showProcessing(title = "Analyzing your PDF", sub = "Extracting text, finding key points, and detecting formulas…") {
    const overlay = document.getElementById("processingOverlay");
    const t = document.getElementById("processingTitle");
    const s = document.getElementById("processingSub");
    if (t) t.innerText = title;
    if (s) s.innerText = sub;
    if (overlay) overlay.style.display = "flex";
}

function hideProcessing() {
    const overlay = document.getElementById("processingOverlay");
    if (overlay) overlay.style.display = "none";
}



/* ------------------------------
   Tabs (Summary / Highlights / Formulas)
------------------------------ */

function setActiveTab(tabName) {
    const tabs = document.querySelectorAll(".tab");
    const panels = {
        summary: document.getElementById("panel-summary"),
        highlights: document.getElementById("panel-highlights"),
        formulas: document.getElementById("panel-formulas"),
        chat: document.getElementById("panel-chat")
    };

    tabs.forEach(btn => {
        const isActive = btn.getAttribute("data-tab") === tabName;
        btn.classList.toggle("active", isActive);
        btn.setAttribute("aria-selected", isActive ? "true" : "false");
    });

    Object.keys(panels).forEach(k => {
        if (panels[k]) panels[k].classList.toggle("active", k === tabName);
    });

    // Chat full-view: replace the workspace with PDF + chat only
    if (tabName === "chat") {
        window.__lastNonChatTab = window.__lastNonChatTab || "summary";
        enterChatFullView();
    } else {
        window.__lastNonChatTab = tabName;
        if (isChatFullOpen()) exitChatFullView(false);
    }
}

function initTabsAndSwipe() {
    const tabs = document.querySelectorAll(".tab");
    tabs.forEach(btn => {
        btn.addEventListener("click", () => setActiveTab(btn.getAttribute("data-tab")));
    });

    // Simple swipe between tabs (mobile-friendly)
    const panelsWrap = document.getElementById("panels");
    if (!panelsWrap) return;

    let startX = null;
    panelsWrap.addEventListener("touchstart", (e) => {
        startX = e.changedTouches?.[0]?.clientX ?? null;
    }, { passive: true });

    panelsWrap.addEventListener("touchend", (e) => {
        if (startX == null) return;
        const endX = e.changedTouches?.[0]?.clientX ?? startX;
        const dx = endX - startX;
        startX = null;

        if (Math.abs(dx) < 45) return;

        const order = ["summary", "highlights", "formulas", "chat"];
        const active = document.querySelector(".tab.active")?.getAttribute("data-tab") || "summary";
        const idx = order.indexOf(active);
        const next = dx < 0 ? Math.min(order.length - 1, idx + 1) : Math.max(0, idx - 1);
        setActiveTab(order[next]);
    }, { passive: true });
}

/* ------------------------------
   User + Sidebar
------------------------------ */

async function loadUser() {
    try {
        const res = await fetch(API_BASE + "/me", { method: "GET", credentials: "include" });

        if (!res.ok) {
            // Not logged in
            smoothRedirect("login.html", 150);
            return;
        }

        const me = await res.json();

        const welcome = document.getElementById("welcome");
        if (welcome) welcome.innerText = "Shifu tool";




        // Init tabs/swipe (premium page only)
        initTabsAndSwipe();

        // Init upload UX
        initDropzone();

        // Load sidebar PDFs
        await loadMyPdfs();

        // Analyze-page control (results header)
        initAnalyzePageControl();

    } catch (e) {
        console.error(e);
        showNotification("Failed to load your account.");
    }
}

async function loadMyPdfs() {
    const container = document.getElementById("pdfCards");
    if (!container) return;

    container.innerHTML = "<div class='pdf-card'><div class='name'>Loading...</div></div>";

    try {
        const res = await fetch(API_BASE + "/my_pdfs", { method: "GET", credentials: "include" });
        const data = await res.json().catch(() => ({}));

        if (!res.ok || !data.items) {
            container.innerHTML = "<div class='pdf-card'><div class='name'>No PDFs yet</div><div class='time'>Upload your first PDF</div></div>";
            return;
        }

        if (data.items.length === 0) {
            container.innerHTML = "<div class='pdf-card'><div class='name'>No PDFs yet</div><div class='time'>Upload your first PDF</div></div>";
            return;
        }

        container.innerHTML = "";
        data.items.forEach((it) => {
            const filename = it.filename || "PDF";
            const card = document.createElement("div");
            card.className = "pdf-card";
            card.setAttribute("data-filename", filename);
            card.innerHTML = `
                <div class="name">${escapeHtml(filename)}</div>
                <div class="time"></div>
            `;
            card.addEventListener("click", async () => {
                setActivePdfCard(filename);
                currentPdfFilename = filename;
                setPdfFrame(filename, 1);
                await fetchPdfResult(filename);
            });
            container.appendChild(card);
        });

    } catch (e) {
        console.error(e);
        container.innerHTML = "<div class='pdf-card'><div class='name'>Failed to load PDFs</div></div>";
    }
}

function setActivePdfCard(filename) {
    const cards = document.querySelectorAll(".pdf-card");
    cards.forEach(c => c.classList.toggle("active", c.getAttribute("data-filename") === filename));
}

function escapeHtml(str) {
    return String(str ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

/* ------------------------------
   Upload UX (drag & drop + styled chooser)
------------------------------ */

function initDropzone() {
    const dz = document.getElementById("dropZone");
    const input = document.getElementById("pdf_file");
    const fileName = document.getElementById("fileName");
    const analyzeBtn = document.getElementById("analyzeBtn");
    const dock = document.querySelector(".upload-box");
if (dock) {
  dock.addEventListener("click", (e) => {
  if (!document.body.classList.contains("upload-collapsed")) return;

  // Ignore clicks on buttons / inputs
  if (
    e.target.closest("button") ||
    e.target.closest("input") ||
    e.target.closest("select") ||
    e.target.closest("label")
  ) return;

  document.body.classList.remove("upload-collapsed");
  localStorage.setItem("upload_collapsed", "0");

  const btn = document.getElementById("uploadToggleBtn");
  if (btn) btn.innerText = "⤢";
});

}


    if (!dz || !input) return;

    function setSelectedFileName(f) {
        if (fileName) fileName.innerText = f ? f.name : "No file selected";
        if (analyzeBtn) analyzeBtn.disabled = !f;
    }

    setSelectedFileName(input.files?.[0] || null);

    input.addEventListener("change", () => {
        const f = input.files?.[0] || null;
        setSelectedFileName(f);
    });

    // Drag events
    const prevent = (e) => { e.preventDefault(); e.stopPropagation(); };

    ["dragenter", "dragover"].forEach(evt => {
        dz.addEventListener(evt, (e) => {
            prevent(e);
            dz.classList.add("dragover");
        });
    });

    ["dragleave", "drop"].forEach(evt => {
        dz.addEventListener(evt, (e) => {
            prevent(e);
            dz.classList.remove("dragover");
        });
    });

    dz.addEventListener("drop", async (e) => {
        const dt = e.dataTransfer;
        const f = dt?.files?.[0];
        if (!f) return;
        if (f.type !== "application/pdf" && !String(f.name || "").toLowerCase().endsWith(".pdf")) {
            showNotification("Please drop a PDF file.");
            return;
        }

        // Set the file into the input (best-effort)
        try {
            const transfer = new DataTransfer();
            transfer.items.add(f);
            input.files = transfer.files;
        } catch (_) {}

        setSelectedFileName(f);

        // Immediate analysis after drop (as requested)
        await uploadPDF();
    });

    // Keyboard (Enter = open file picker)
    dz.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            input.click();
        }
    });

    // Clear button
    const clearBtn = document.getElementById("clearBtn");
    if (clearBtn) {
        clearBtn.addEventListener("click", () => {
            input.value = "";
            setSelectedFileName(null);
            clearResultsUI();
        });
    }
}

/* ------------------------------
   Render analysis (3 sections)
------------------------------ */

function clearResultsUI() {
    const meta = document.getElementById("resultMeta");
    const empty = document.getElementById("emptyState");
    const summary = document.getElementById("summaryContent");
    const highlights = document.getElementById("highlightsContent");
    const formulas = document.getElementById("formulasContent");

    if (meta) meta.innerText = "";
    const rf = document.getElementById("resultFileName");
    if (rf) rf.innerText = "No file selected";
    if (empty) empty.style.display = "block";
    if (summary) { summary.style.display = "none"; summary.innerHTML = ""; }
    if (highlights) highlights.innerHTML = "";
    if (formulas) formulas.innerHTML = "";

    setActiveTab("summary");
}


function formatSources(sources){
    if (!Array.isArray(sources) || sources.length === 0) return "";
    const pages = sources.map(x => Number(x)).filter(n => Number.isFinite(n)).sort((a,b)=>a-b);
    if (!pages.length) return "";
    const label = `p.${pages.join(", ")}`;
    return ` <button type="button" class="src" data-pages="${pages.join(",")}">${label}</button>`;
}

function renderItemWithSources(item){
    if (item == null) return "";
    if (typeof item === "string") return escapeHtml(item);
    if (typeof item === "object"){
        const text = item.text ?? item.value ?? "";
        return `${escapeHtml(text)}${formatSources(item.sources)}`;
    }
    return escapeHtml(String(item));
}

function renderAnalysis(result, filenameForMeta = "") {
    if (filenameForMeta) currentPdfFilename = filenameForMeta;
    const rf = document.getElementById("resultFileName");
    if (rf && filenameForMeta) rf.innerText = filenameForMeta;
    const meta = document.getElementById("resultMeta");
    const empty = document.getElementById("emptyState");

    const summaryEl = document.getElementById("summaryContent");
    const highlightsEl = document.getElementById("highlightsContent");
    const formulasEl = document.getElementById("formulasContent");

    if (meta) {
        const lvl = result?.meta?.level ? ` • ${result.meta.level}` : "";
        const pgs = (result?.meta?.pages_analyzed != null) ? ` • pages: ${result.meta.pages_analyzed}` : "";
        meta.innerText = filenameForMeta ? `File: ${filenameForMeta}${lvl}${pgs}` : "";
    }
    if (empty) empty.style.display = "none";

    // Summary panel
    const summary = (result && result.summary) ? String(result.summary) : "";
    const keyPoints = Array.isArray(result?.key_points) ? result.key_points : [];
    const defs = Array.isArray(result?.definitions) ? result.definitions : [];

    if (summaryEl) {
        summaryEl.style.display = "block";
        summaryEl.innerHTML = `
            ${summary ? `<div class="kv"><div class="k">Summary</div><div class="v">${escapeHtml(summary)}</div></div>` : ""}
            ${keyPoints.length ? `
              <div class="kv"><div class="k">Key points</div>
                <div class="v">
                  <ul class="list">
                    ${keyPoints.slice(0, 14).map(p => `<li>${renderItemWithSources(p)}</li>`).join("")}
                  </ul>
                </div>
              </div>
            ` : ""}
            ${defs.length ? `
              <div class="kv"><div class="k">Definitions</div>
                <div class="v">
                  <ul class="list">
                    ${defs.slice(0, 12).map(p => `<li>${renderItemWithSources(p)}</li>`).join("")}
                  </ul>
                </div>
              </div>
            ` : ""}
        `;
    }

    // Highlights panel
    const highlights = Array.isArray(result?.highlights) ? result.highlights : [];
    const important = Array.isArray(result?.important_notes) ? result.important_notes : [];

    const highlightBlocks = [
        ...important.map(n => ({ type: "note", item: n })),
        ...highlights.map(h => ({ type: "hl", item: h }))
    ].filter(x => x.item);

    if (highlightsEl) {
        highlightsEl.innerHTML = highlightBlocks.length
            ? highlightBlocks.slice(0, 22).map(x => `<div class="note">${renderItemWithSources(x.item)}</div>`).join("")
            : `<div class="empty-state"><div class="empty-title">No highlights found</div><div class="empty-sub">Try a different PDF or a clearer scanned document.</div></div>`;
    }

    // Formulas panel
    const formulas = Array.isArray(result?.formulas) ? result.formulas : [];
    if (formulasEl) {
        formulasEl.innerHTML = formulas.length
            ? formulas.slice(0, 22).map(f => {
                const latex = String(f?.latex || f?.formula || "");
                const expr = escapeHtml(latex);
                const rendered = expr ? `\\(${expr}\\)` : "—";
                const exp = escapeHtml(f?.meaning || f?.explanation || "");
                return `
                  <div class="formula">
                    <div class="expr">${rendered}${formatSources(f?.sources)}</div>
                    ${exp ? `<div class="exp">${exp}</div>` : ""}
                  </div>
                `;
              }).join("")
            : `<div class="empty-state"><div class="empty-title">No formulas detected</div><div class="empty-sub">If your PDF is scanned, OCR may be needed for formula extraction.</div></div>`;
    
        // Render LaTeX with MathJax (if loaded)
        if (window.MathJax && typeof window.MathJax.typesetPromise === 'function') {
            window.MathJax.typesetPromise([formulasEl]).catch(() => {});
        }
}

    setActiveTab("summary");
}

/* ------------------------------
   Fetch previous results by clicking sidebar PDF
------------------------------ */

async function fetchPdfResult(filename) {
    try {
        showProcessing("Loading saved analysis", "Fetching the latest results for this PDF…");

        const url = new URL(API_BASE + "/pdf_result");
        url.searchParams.set("filename", filename);

        const res = await fetch(url.toString(), { method: "GET", credentials: "include" });
        const data = await res.json().catch(() => ({}));

        hideProcessing();

        if (!res.ok || !data.success) {
            const msg = data.message || data.detail || "Could not load PDF result.";
            showNotification(msg);
            return;
        }

        renderAnalysis(data.result, filename);
    } catch (e) {
        console.error(e);
        hideProcessing();
        showNotification("Failed to load PDF result.");
    }
}

/* ------------------------------
   Upload + analyze
------------------------------ */


/* ------------------------------
   Analyze a specific page of the selected PDF (without re-upload)
------------------------------ */
function initAnalyzePageControl() {
    const btn = document.getElementById("pageAnalyzeBtn");
    if (!btn) return;

    btn.addEventListener("click", async () => {
        if (!currentPdfFilename) {
            showNotification("Select a PDF from the sidebar first.");
            return;
        }

        const pageRaw = document.getElementById("pageAnalyzeInput")?.value;
        const page = Number(pageRaw);
        if (!Number.isFinite(page) || page <= 0) {
            showNotification("Enter a valid page number.");
            return;
        }

        const lvl = document.getElementById("pageAnalyzeLevel")?.value || "normal";

        try {
            showProcessing("Analyzing selected page", "Extracting summary, highlights, and formulas for this page…");

            const res = await fetch(API_BASE + "/analyze_page", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "include",
                body: JSON.stringify({ filename: currentPdfFilename, page: page, level: lvl })
            });

           

            const data = await res.json().catch(() => ({}));
            hideProcessing();

            if (!res.ok || !data.success) {
                showNotification(data.message || data.detail || "Page analysis failed");
                return;
            }

            renderAnalysis(data.result, currentPdfFilename);
            setActiveTab("summary");
            showNotification("Page analysis ready ✅");
        } catch (e) {
            console.error(e);
            hideProcessing();
            showNotification("Page analysis failed");
        }
    });
}


async function uploadPDF() {
    const fileInput = document.getElementById("pdf_file");
    if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
        showNotification("Please choose a PDF file first.");
        return;
    }

    const file = fileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);
    const lvl = document.getElementById("analysisLevel")?.value || "normal";
    formData.append("level", lvl);
    const pageVal = document.getElementById("uploadPageInput")?.value;
    if (pageVal && String(pageVal).trim() !== "") {
        formData.append("page", String(pageVal).trim());
    }

    try {
        showProcessing("Analyzing your PDF", "Extracting summary, highlights, and formulas…");

        const res = await fetch(API_BASE + "/upload_pdf", {
            method: "POST",
            credentials: "include",
            body: formData
        });

        

        const data = await res.json().catch(() => ({}));
        hideProcessing();

        if (!res.ok || !data.success) {
            const msg = data.message || "Upload failed.";
            showNotification(msg);
            return;
        }

        

        // Render analysis result
        renderAnalysis(data.result, file.name);

        // Auto-enter full screen (focus mode) after analysis completes
        if (typeof toggleFocusMode === 'function') toggleFocusMode(true);

        // Refresh sidebar list + highlight selected
        await loadMyPdfs();
        setActivePdfCard(file.name);

    } catch (e) {
        console.error(e);
        hideProcessing();
        showNotification("Upload failed. Please try again.");
    }
}

/* ------------------------------
   Modal hooks (premium page only)
------------------------------ */

document.addEventListener("DOMContentLoaded", () => {

    // Default tab state if premium page is loaded directly
    if (document.querySelector(".tabs")) {
        initTabsAndSwipe();
        initDropzone();
    }
});

/* ============================
   Reader Mode
============================ */
function toggleReaderMode(force = null){
  const enable = force === null ? !document.body.classList.contains("reader-mode") : !!force;
if (enable){
  document.body.classList.remove("focus-mode");
  document.body.classList.remove("chat-full-open");
  document.body.classList.remove("chat-split");
}
document.body.classList.toggle("reader-mode", enable);
  localStorage.setItem("reader_mode", enable ? "1" : "0");

  const btn = document.getElementById("readerModeBtn");
  if (btn) btn.innerText = enable ? "🧾" : "📖";
}

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("readerModeBtn");
  if (btn) btn.addEventListener("click", () => toggleReaderMode());
  const saved = localStorage.getItem("reader_mode");
  if (saved === "1") toggleReaderMode(true);
});

/* ============================
   Copy helpers (Summary/Highlights)
============================ */
async function copyTextToClipboard(text){
  const t = String(text || "").trim();
  if (!t) return false;

  try{
    if (navigator.clipboard && window.isSecureContext){
      await navigator.clipboard.writeText(t);
      return true;
    }
  }catch(_){}

  try{
    const ta = document.createElement("textarea");
    ta.value = t;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return !!ok;
  }catch(_){
    return false;
  }
}

function initCopyButtons(){
  const doCopySummary = async () => {
    const el = document.getElementById("summaryContent");
    const ok = await copyTextToClipboard(el ? el.innerText : "");
    showNotification(ok ? "Summary copied ✅" : "Nothing to copy");
  };

  const doCopyHighlights = async () => {
    const el = document.getElementById("highlightsContent");
    const ok = await copyTextToClipboard(el ? el.innerText : "");
    showNotification(ok ? "Highlights copied ✅" : "Nothing to copy");
  };

  // Support OLD buttons (if they exist)
  document.getElementById("copySummaryBtn")?.addEventListener("click", doCopySummary);
  document.getElementById("copyHighlightsBtn")?.addEventListener("click", doCopyHighlights);

  // Support NEW 3-dots menu buttons
  document.getElementById("menuCopySummary")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    doCopySummary();
    document.getElementById("resultsMenu").style.display = "none";
  });

  document.getElementById("menuCopyHighlights")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    doCopyHighlights();
    document.getElementById("resultsMenu").style.display = "none";
  });
}

document.addEventListener("DOMContentLoaded", initCopyButtons);
/* ============================
   Sidebar Toggle
============================ */
function toggleSidebar(force = null) {
    const enable =
        force === null
            ? !document.body.classList.contains("sidebar-collapsed")
            : !!force;

    document.body.classList.toggle("sidebar-collapsed", enable);
    localStorage.setItem("sidebar_collapsed", enable ? "1" : "0");
}

document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("sidebarToggleBtn");
    if (btn) {
        btn.addEventListener("click", () => toggleSidebar());
    }

    // Restore state
    const saved = localStorage.getItem("sidebar_collapsed");
    if (saved === "1") toggleSidebar(true);
});
/* ============================
   Fullscreen / Focus Mode
============================ */
function toggleFocusMode(force = null) {
    const enable =
        force === null
            ? !document.body.classList.contains("focus-mode")
            : !!force;

    document.body.classList.toggle("focus-mode", enable);
    localStorage.setItem("focus_mode", enable ? "1" : "0");

    const btn = document.getElementById("focusToggleBtn");
    if (btn) btn.innerText = enable ? "⤢" : "⛶";
}

document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("focusToggleBtn");
    if (btn) {
        btn.addEventListener("click", () => toggleFocusMode());
    }

    // Restore state
    const saved = localStorage.getItem("focus_mode");
    if (saved === "1") toggleFocusMode(true);
});
/* ============================
   ESC key priority handling
   1) Upgrade modal
   2) Reader mode
   3) Fullscreen (focus mode)
============================ */
document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;

    

    /* 2️⃣ Exit reader mode */
    if (document.body.classList.contains("reader-mode")) {
        toggleReaderMode(false);
        return;
    }

    /* 3️⃣ Exit fullscreen / focus mode */
    if (document.body.classList.contains("focus-mode")) {
        toggleFocusMode(false);
        return;
    }
});
function togglePassword(inputId, el) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const isHidden = input.type === "password";
    input.type = isHidden ? "text" : "password";
    el.textContent = isHidden ? "🙈" : "👁";
}


/* ============================
   Chat tab (zero-cost PDF chat)
============================ */
function appendChatBubble(text, kind = "bot", sources = null, matches = null) {
    const wrap = document.getElementById("chatMessages");
    if (!wrap) return;

    // Remove empty-state if present
    const empty = wrap.querySelector(".empty-state");
    if (empty) empty.remove();

    const div = document.createElement("div");
    div.className = "chat-bubble " + (kind === "user" ? "user" : "bot");

    // Main message (preserve line breaks)
    let html = `<div>${escapeHtml(text).replaceAll("\n", "<br>")}</div>`;

    // Optional sources (kept out of the main answer)
    if (kind !== "user" && Array.isArray(sources) && sources.length) {
        const items = sources.slice(0, 5).map(s => {
            const p = escapeHtml(String(s.page ?? ""));
            const sn = escapeHtml(String(s.snippet ?? ""))
                .replaceAll("\n", " ");

            return `
                <li>
                    <button type="button" class="src-page" data-page="${p}"><b>Page ${p}</b></button><br>
                    <span class="src-snippet">${sn}</span>
                </li>
            `;
        }).join("");

        html += `
            <details class="chat-sources">
                <summary>Sources</summary>
                <ul class="chat-src-list">
                    ${items}
                </ul>
            </details>
        `;
    }


// Optional matched ideas from analysis
if (kind !== "user" && Array.isArray(matches) && matches.length) {
    const chips = matches.slice(0, 6).map(m => {
        const type = escapeHtml(String(m.type || "match"));
        const text = escapeHtml(String(m.text || "")).slice(0, 160);
        const pages = Array.isArray(m.sources) ? m.sources.map(x=>Number(x)).filter(n=>Number.isFinite(n)) : [];
        const dp = pages.length ? ` data-pages="${pages.join(",")}"` : "";
        return `<button type="button" class="match-chip"${dp} title="${type}">${type}: ${text}${text.length>=160?"…":""}</button>`;
    }).join("");
    html += `<div class="match-wrap"><div class="match-title">Matched ideas</div><div class="match-chips">${chips}</div></div>`;
}
    div.innerHTML = html;
    wrap.appendChild(div);
    wrap.scrollTop = wrap.scrollHeight;
}

async function sendChatMessage(){
    const input = document.getElementById("chatInput");
    const btn = document.getElementById("chatSendBtn");
    const msg = (input?.value || "").trim();
    if (!msg) return;

    if (!currentPdfFilename){
        showNotification("Select or analyze a PDF first.");
        return;
    }

    appendChatBubble(msg, "user");
    if (input) input.value = "";

    try{
        if (btn) btn.disabled = true;

        const res = await fetch(API_BASE + "/chat_pdf", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: JSON.stringify({ filename: currentPdfFilename, message: msg })
        });

        const data = await res.json().catch(()=> ({}));
        if (!res.ok || !data.success){
            appendChatBubble(data.message || data.detail || "Chat failed.", "bot");
            return;
        }

        const answer = String(data.answer || "").trim() || "No answer.";
        const sources = Array.isArray(data.sources) ? data.sources : [];
        const matches = Array.isArray(data.matches) ? data.matches : [];

        appendChatBubble(answer, "bot", sources, matches);

        // Real-time matching: jump to most relevant page (best-effort)
        const p = Number(sources?.[0]?.page);
        if (Number.isFinite(p) && p > 0) {
            jumpToPdfPage(p);
            flashPdfHint();
        }

    }catch(e){
        console.error(e);
        appendChatBubble("Chat failed. Please try again.", "bot");
    }finally{
        if (btn) btn.disabled = false;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("chatSendBtn");
    const input = document.getElementById("chatInput");
    if (btn) btn.addEventListener("click", sendChatMessage);
    if (input){
        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter"){
                e.preventDefault();
                sendChatMessage();
            }
        });
    }
});


/* ============================
   Chat Full View (PDF + Chat)
============================ */
let __chatFullPlaceholder = null;

function isChatFullOpen(){
    const overlay = document.getElementById("chatFullOverlay");
    return !!overlay && overlay.style.display === "flex";
}

function setChatPdfFrame(filename, page){
    const frame = document.getElementById("chatPdfFrame");
    if (!frame || !filename) return;
    const safe = encodeURIComponent(filename);
    const p = Math.max(1, parseInt(page || 1, 10));
    frame.src = API_BASE + "/pdf_file?filename=" + safe + "#page=" + p;
}

function jumpToPdfPage(page){
    const p = Math.max(1, parseInt(page || 1, 10));
    if (isChatFullOpen()){
        if (currentPdfFilename) setChatPdfFrame(currentPdfFilename, p);
        return;
    }
    // normal preview
    if (currentPdfFilename) setPdfFrame(currentPdfFilename, p);
}

function flashPdfHint(){
    const head = document.querySelector(".pdf-pane-head");
    if (!head) return;
    head.classList.add("flash");
    setTimeout(()=> head.classList.remove("flash"), 650);
}

function enterChatFullView(){
    const overlay = document.getElementById("chatFullOverlay");
    const host = document.getElementById("chatFullChatHost");
    const panel = document.getElementById("panel-chat");
    if (!overlay || !host || !panel) return;

    // Move the chat UI into the full-view container (preserves state)
    const chatWrap = panel.querySelector(".chat-wrap");
    if (chatWrap && !__chatFullPlaceholder){
        __chatFullPlaceholder = document.createElement("div");
        __chatFullPlaceholder.className = "chat-placeholder";
        panel.appendChild(__chatFullPlaceholder);
        host.appendChild(chatWrap);
    }

    overlay.style.display = "flex";
    document.body.classList.add("chat-full-open");
    document.body.classList.remove("focus-mode");


    // Initialize PDF side
    if (currentPdfFilename) setChatPdfFrame(currentPdfFilename, 1);
}

function exitChatFullView(setTabBack = true){
    const overlay = document.getElementById("chatFullOverlay");
    const host = document.getElementById("chatFullChatHost");
    const panel = document.getElementById("panel-chat");
    if (!overlay || !host || !panel) return;

    // Move chat back
    const chatWrap = host.querySelector(".chat-wrap");
    if (chatWrap && __chatFullPlaceholder){
        panel.insertBefore(chatWrap, __chatFullPlaceholder);
        __chatFullPlaceholder.remove();
        __chatFullPlaceholder = null;
    }

    overlay.style.display = "none";
    document.body.classList.remove("chat-full-open");
    document.body.classList.add("focus-mode");
    if (setTabBack){
        const back = window.__lastNonChatTab || "summary";
        const tabs = document.querySelectorAll(".tab");
        tabs.forEach(btn => {
            const t = btn.getAttribute("data-tab");
            btn.classList.toggle("active", t === back);
            btn.setAttribute("aria-selected", t === back ? "true" : "false");
        });
        ["summary","highlights","formulas","chat"].forEach(k=>{
            const el = document.getElementById("panel-"+k);
            if (el) el.classList.toggle("active", k === back);
        });
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const ret = document.getElementById("chatFullReturnBtn");
    if (ret) ret.addEventListener("click", () => exitChatFullView(true));
});

// Clicking a source page or match chip jumps the PDF
document.addEventListener("click", (e) => {
    const btn = e.target?.closest?.(".src-page, .match-chip");
    if (!btn) return;

    if (btn.classList.contains("src-page")){
        const p = Number(btn.getAttribute("data-page"));
        if (Number.isFinite(p)) jumpToPdfPage(p);
        return;
    }
    if (btn.classList.contains("match-chip")){
        const pages = (btn.getAttribute("data-pages") || "").split(",").map(x=>parseInt(x,10)).filter(n=>Number.isFinite(n));
        if (pages.length) jumpToPdfPage(pages[0]);
    }
});

/* ============================
   Return buttons (Reader / Focus)
============================ */
document.addEventListener("DOMContentLoaded", () => {
    const r = document.getElementById("exitReaderBtn");
    const f = document.getElementById("exitFocusBtn");
    if (r) r.addEventListener("click", () => toggleReaderMode(false));
    if (f) f.addEventListener("click", () => toggleFocusMode(false));
});

/* ============================
   Settings menu + modals
============================ */
function openOverlay(id){
    const el = document.getElementById(id);
    if (el) el.style.display = "flex";
}
function closeOverlay(id){
    const el = document.getElementById(id);
    if (el) el.style.display = "none";
}

document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("settingsBtn");
    const menu = document.getElementById("settingsMenu");

    function closeMenu(){
        if (menu) menu.style.display = "none";
        if (btn) btn.setAttribute("aria-expanded","false");
    }

    if (btn && menu){
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const open = menu.style.display === "block";
            menu.style.display = open ? "none" : "block";
            btn.setAttribute("aria-expanded", open ? "false" : "true");
        });

        document.addEventListener("click", (e) => {
            if (!menu.contains(e.target) && e.target !== btn) closeMenu();
        });
    }

    // Close buttons for overlays + click outside modal content
    document.addEventListener("click", (e) => {
        const c = e.target?.getAttribute?.("data-close");
        if (c) closeOverlay(c);
        const ov = e.target?.classList?.contains?.("modal-overlay") ? e.target : null;
        if (ov && ov.id && ov.style.display === "flex") closeOverlay(ov.id);
    });

    const menuProfile = document.getElementById("menuProfile");
    const menuUsage = document.getElementById("menuUsage");
    const menuFeedback = document.getElementById("menuFeedback");
    const menuPolicy = document.getElementById("menuPolicy");
    const menuLogout = document.getElementById("menuLogout");

    if (menuLogout) menuLogout.addEventListener("click", () => { closeMenu(); logout(); });

    async function fillProfile(){
        const body = document.getElementById("profileBody");
        if (!body) return;
        body.innerText = "Loading…";
        try{
            const res = await fetch(API_BASE + "/me", { method:"GET", credentials:"include" });
            const me = await res.json().catch(()=> ({}));
            if (!res.ok) { body.innerText = "Failed to load profile."; return; }

            const lines = [
    "Email: " + (me.email || "-"),
    "Provider: " + (me.auth_provider || "local")
];
            body.innerHTML = "<div class=\"policy-text\">" + lines.map(x=>"<p>"+escapeHtml(x)+"</p>").join("") + "</div>";
        }catch(_){
            body.innerText = "Failed to load profile.";
        }
    }

    async function fillUsage(){
        const body = document.getElementById("usageBody");
        if (!body) return;
        body.innerText = "Loading…";
        try{
            const res = await fetch(API_BASE + "/me", { method:"GET", credentials:"include" });
            const me = await res.json().catch(()=> ({}));
            if (!res.ok) { body.innerText = "Failed to load usage."; return; }

            body.innerHTML = `
<div class="policy-text">
    <p><b>Email:</b> ${escapeHtml(me.email || "-")}</p>
    <p><b>Provider:</b> ${escapeHtml(me.auth_provider || "local")}</p>
</div>
`;
        }catch(_){
            body.innerText = "Failed to load usage.";
        }
    }

    if (menuProfile) menuProfile.addEventListener("click", async () => { closeMenu(); openOverlay("profileOverlay"); await fillProfile(); });
    if (menuUsage) menuUsage.addEventListener("click", async () => { closeMenu(); openOverlay("usageOverlay"); await fillUsage(); });
    if (menuFeedback) menuFeedback.addEventListener("click", () => { closeMenu(); openOverlay("feedbackOverlay"); });
    if (menuPolicy) menuPolicy.addEventListener("click", () => { closeMenu(); openOverlay("policyOverlay"); });
});

// Feedback submit
document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("feedbackSendBtn");
    if (!btn) return;

    btn.addEventListener("click", async () => {
        const hint = document.getElementById("feedbackHint");
        const rating = document.getElementById("feedbackRating")?.value;
        const message = (document.getElementById("feedbackMessage")?.value || "").trim();
        if (!message){
            if (hint) hint.innerText = "Please write a message.";
            return;
        }
        if (hint) hint.innerText = "Sending…";
        try{
            const res = await fetch(API_BASE + "/feedback", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "include",
                body: JSON.stringify({ rating: rating, message: message, page: window.location.pathname })
            });
            const data = await res.json().catch(()=> ({}));
            if (!res.ok || !data.success){
                if (hint) hint.innerText = data.message || data.detail || "Failed to send.";
                return;
            }
            if (hint) hint.innerText = "Thanks! ✅";
            const msgEl = document.getElementById("feedbackMessage");
            if (msgEl) msgEl.value = "";
        }catch(_){
            if (hint) hint.innerText = "Failed to send.";
        }
    });
});

// ESC: exit chat full view first
document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (isChatFullOpen()){
        exitChatFullView(true);
        return;
    }
});
document.getElementById("settingsReturnBtn")?.addEventListener("click", () => {
  // Hide settings
  const settings = document.getElementById("settingsPage");
  if (settings) settings.style.display = "none";

  // Reset route
  history.replaceState(null, "", window.location.pathname);

  // 🔑 Re-initialize dashboard safely
  restoreDashboard();
});
function restoreDashboard() {
  // Ensure workspace is visible
  const workspace = document.querySelector(".workspace");
  if (workspace) workspace.style.display = "";

  // Remove any mode leftovers
  document.body.classList.remove(
    "reader-mode",
    "focus-mode",
    "chat-full-active",
    "chat-split"
  );

  // Re-run dashboard init (safe to call multiple times)
  loadUser();
}
function loadSettingsUsage() {
    const usage = document.getElementById("settingsTries");
    if (usage) {
        usage.innerText = "Unlimited";
    }
}
(function initUploadToggle(){
  const btn = document.getElementById("uploadToggleBtn");
  if (!btn) return;

  const KEY = "upload_collapsed";

  // Restore state on load
  const saved = localStorage.getItem(KEY);
  if (saved === "1") {
    document.body.classList.add("upload-collapsed");
    btn.innerText = "⤡";
  }

  btn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation(); // IMPORTANT

    const collapsed = document.body.classList.toggle("upload-collapsed");

    btn.innerText = collapsed ? "⤡" : "⤢";
    localStorage.setItem(KEY, collapsed ? "1" : "0");
  });
})();
(function initUploadMagnet(){
  const dock = document.querySelector(".upload-dock");
  if (!dock) return;

  let snapping = false;

  function snapPulse(){
    if (snapping) return;
    snapping = true;

    dock.classList.add("snap-pulse");
    setTimeout(() => {
      dock.classList.remove("snap-pulse");
      snapping = false;
    }, 180);
  }

  // Add snap feedback on toggle
  document.addEventListener("click", (e) => {
    if (e.target.closest("#uploadToggleBtn")) {
      snapPulse();
    }
  });
})();
(function initResultsMenu(){
  const btn = document.getElementById("resultsMenuBtn");
  const menu = document.getElementById("resultsMenu");
  if (!btn || !menu) return;

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    menu.style.display = menu.style.display === "none" ? "block" : "none";
  });

  // Close on outside click
  document.addEventListener("click", () => {
    menu.style.display = "none";
  });

})();
function exitChatToHome() {
  // Close chat full overlay
  const overlay = document.getElementById("chatFullOverlay");
  if (overlay) overlay.style.display = "none";

  // Remove all special modes
  document.body.classList.remove(
    "chat-split",
    "focus-mode",
    "reader-mode"
  );

  // Restore main layout
  document.querySelector(".workspace")?.style.removeProperty("display");
  document.querySelector(".sidebar")?.style.removeProperty("display");
  document.querySelector(".upload-dock")?.style.removeProperty("display");

  // Return to last non-chat tab (fallback: summary)
  const tab = window.__lastNonChatTab || "summary";
  setActiveTab(tab);
}



async function loadSettingsProfile(){
  const res = await fetch(API_BASE + "/me", { credentials: "include" });
  const me = await res.json();

  document.getElementById("settingsEmail").innerText = me.email;
  document.getElementById("settingsPlan").innerText ="Unlimited";
}
function exitSettings() {
    document.getElementById("settingsPage").style.display = "none";
    const workspace = document.querySelector(".workspace");
    if (workspace) workspace.style.display = "grid";
    history.replaceState(null, "", "/static/premium.html");
}

/* ======================================
   SETTINGS ROUTER (hash-based)
====================================== */
function handleSettingsRoute() {
    const hash = window.location.hash || "#/settings/profile";

    const page = document.getElementById("settingsPage");
    if (!page) return;

    // Show settings page
    page.style.display = "block";

    // Hide main workspace
    const workspace = document.querySelector(".workspace");
    if (workspace) workspace.style.display = "none";

    // Activate correct section
    const route = hash.replace("#/settings/", "");
    document.querySelectorAll(".settings-section").forEach(sec => {
        sec.style.display = sec.dataset.route === route ? "block" : "none";
    });

    // Fetch data per section
    if (route === "profile") loadSettingsProfile();
    if (route === "usage") loadSettingsUsage();
}
window.addEventListener("hashchange", handleSettingsRoute);

// ============================
// Settings Control Center
// ============================
const settingsBtn = document.getElementById("settingsBtn");
const settingsCenter = document.getElementById("settingsCenter");
const closeSettingsBtn = document.getElementById("closeSettingsBtn");

if (settingsBtn && settingsCenter) {
  settingsBtn.onclick = () => {
    settingsCenter.style.display = "flex";
  };
}
if (closeSettingsBtn) {
  closeSettingsBtn.onclick = () => {
    settingsCenter.style.display = "none";
  };
}

// Section switching
document.querySelectorAll(".settings-link[data-section]").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll(".settings-link").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");

    const section = btn.dataset.section;
    document.getElementById("settingsTitle").innerText =
      btn.innerText.replace(/^[^ ]+ /, "");

    document.querySelectorAll(".settings-section").forEach(s => s.classList.remove("active"));
    document.getElementById("settings-" + section).classList.add("active");
  };
});

// Logout
const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) logoutBtn.onclick = logout;
/* ============================
   SETTINGS ROUTER
============================ */

if (settingsBtn){
  settingsBtn.onclick = () => {
    location.hash = "#/settings/profile";
  };
}

const settingsLogoutBtn = document.getElementById("settingsLogoutBtn");
if (settingsLogoutBtn) settingsLogoutBtn.onclick = logout;

function showSettingsRoute(route){
  const page = document.getElementById("settingsPage");
  if (!page) return;

  page.style.display = "block";
  document.querySelector(".workspace").style.display = "none";

  document.querySelectorAll(".settings-section").forEach(sec=>{
    sec.classList.toggle("active", sec.dataset.route === route);
  });

  document.querySelectorAll(".settings-link[href]").forEach(a=>{
    a.classList.toggle(
      "active",
      a.getAttribute("href") === "#/settings/" + route
    );
  });
}

function hideSettingsRoute(){
  const page = document.getElementById("settingsPage");
  if (!page) return;

  page.style.display = "none";
  document.querySelector(".workspace").style.display = "";
}

function handleRoute(){
  const hash = location.hash || "";
  if (hash.startsWith("#/settings/")){
    const route = hash.split("/")[2] || "profile";
    showSettingsRoute(route);
  } else {
    hideSettingsRoute();
  }
}

window.addEventListener("hashchange", handleRoute);
window.addEventListener("load", handleRoute);

/* ======================================
   AUTH PAGES DOM BOOTSTRAP
   (register / login / index)
====================================== */
document.addEventListener("DOMContentLoaded", () => {
    const chatReturnBtn = document.getElementById("chatFullReturnBtn");
if (chatReturnBtn) {
  chatReturnBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    exitChatToHome();
  });
}


    /* -----------------------------
       Auto session check
       (index, login, register)
    ----------------------------- */
    const authPages = ["index.html", "login.html", "register.html"];
    const path = window.location.pathname.toLowerCase();

    if (authPages.some(p => path.endsWith(p))) {
        checkSession();
    }

    /* -----------------------------
       REGISTER PAGE
    ----------------------------- */
    const registerForm = document.getElementById("registerForm");
    const registerBtn = document.getElementById("submitBtn");

    if (registerForm && registerBtn) {
        registerBtn.addEventListener("click", (e) => {
            e.preventDefault();
            register();
        });

        registerForm.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                register();
            }
        });
    }

    /* -----------------------------
       LOGIN PAGE
    ----------------------------- */
    const loginForm = document.getElementById("loginForm");
    const loginBtn = document.getElementById("submitBtn");

    if (loginForm && loginBtn) {
        loginBtn.addEventListener("click", (e) => {
            e.preventDefault();
            login();
        });

        loginForm.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                login();
            }
        });
    }

    /* -----------------------------
       GOOGLE SIGN-IN (index only)
    ----------------------------- */
    if (document.getElementById("googleSignIn")) {
        initGoogle();
    }
});



document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest ? e.target.closest(".src") : null;
    if (!btn) return;
    const pagesAttr = btn.getAttribute("data-pages") || "";
    const pages = pagesAttr.split(",").map(x => Number(x)).filter(n => Number.isFinite(n));
    if (pages.length){
        setPdfFrame(currentPdfFilename, pages[0]);
    }
});

