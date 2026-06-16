// /static/modes.js
// Adds robust Reader Mode + Focus Mode + Print button with smooth transitions.
// Safe to include even if ui.js already wires buttons (we guard double-binding).

(function () {
  const $ = (id) => document.getElementById(id);

  const readerBtn = $("readerModeBtn");
  const focusBtn  = $("focusToggleBtn");
  const exitReaderBtn = $("exitReaderBtn");
  const exitFocusBtn  = $("exitFocusBtn");
  const modeReturnBar = $("modeReturnBar");
  const printBtn = $("printBtn");

  // If the page doesn't have these elements, do nothing.
  if (!readerBtn || !focusBtn || !exitReaderBtn || !exitFocusBtn || !modeReturnBar) return;

  const body = document.body;

  const enterMode = (cls) => {
    // Avoid stacking modes
    body.classList.remove("reader-mode", "focus-mode");
    body.classList.add("mode-enter");
    // Next frame, apply mode
    requestAnimationFrame(() => {
      body.classList.add(cls);
      window.setTimeout(() => body.classList.remove("mode-enter"), 220);
    });
  };

  const exitMode = (cls) => {
    if (!body.classList.contains(cls)) return;
    body.classList.add("mode-exit");
    window.setTimeout(() => {
      body.classList.remove(cls);
      body.classList.remove("mode-exit");
    }, 220);
  };

  // Prevent double-binding
  if (!readerBtn.dataset.bound) {
    readerBtn.dataset.bound = "1";
    readerBtn.addEventListener("click", () => {
      if (body.classList.contains("reader-mode")) {
        exitMode("reader-mode");
      } else {
        enterMode("reader-mode");
        // Ensure summary panel stays readable in print view (optional)
        try { document.getElementById("panel-summary")?.scrollIntoView({ block: "start" }); } catch (e) {}
      }
    });
  }

  if (!focusBtn.dataset.bound) {
    focusBtn.dataset.bound = "1";
    focusBtn.addEventListener("click", () => {
      if (body.classList.contains("focus-mode")) {
        exitMode("focus-mode");
      } else {
        enterMode("focus-mode");
      }
    });
  }

  if (!exitReaderBtn.dataset.bound) {
    exitReaderBtn.dataset.bound = "1";
    exitReaderBtn.addEventListener("click", () => exitMode("reader-mode"));
  }

  if (!exitFocusBtn.dataset.bound) {
    exitFocusBtn.dataset.bound = "1";
    exitFocusBtn.addEventListener("click", () => exitMode("focus-mode"));
  }

  // ESC to exit modes
  if (!document.body.dataset.modeEscBound) {
    document.body.dataset.modeEscBound = "1";
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        exitMode("reader-mode");
        exitMode("focus-mode");
      }
    });
  }

  // Print / Export
  if (printBtn && !printBtn.dataset.bound) {
    printBtn.dataset.bound = "1";
    printBtn.addEventListener("click", () => {
      // If user isn't in reader mode, temporarily enter it for a cleaner export.
      const wasReader = body.classList.contains("reader-mode");
      if (!wasReader) enterMode("reader-mode");

      // Wait a bit for layout to settle then print.
      window.setTimeout(() => {
        window.print();
        // Return to previous mode after print
        if (!wasReader) exitMode("reader-mode");
      }, 260);
    });
  }
})();
