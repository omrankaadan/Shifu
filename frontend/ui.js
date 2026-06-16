// /static/ui.js - theme toggle + reveal + footer year

(function initTheme() {
  const saved = localStorage.getItem("theme");
  if (saved === "light" || saved === "dark") {
    document.documentElement.setAttribute("data-theme", saved);
  } else {
    document.documentElement.setAttribute("data-theme", "dark");
  }
})();

function updateThemeButton(btn) {
  if (!btn) return;
  const theme = document.documentElement.getAttribute("data-theme") || "dark";
  const icon = btn.querySelector(".theme-icon");
  const text = btn.querySelector(".theme-text");
  if (!icon || !text) return;

  if (theme === "light") {
    icon.textContent = "☀️";
    text.textContent = "Light";
  } else {
    icon.textContent = "🌙";
    text.textContent = "Dark";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("themeToggle");
  if (btn) {
    btn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "dark";
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("theme", next);
      updateThemeButton(btn);
    });
    updateThemeButton(btn);
  }

  const year = document.getElementById("year");
  if (year) year.textContent = new Date().getFullYear();

  const els = document.querySelectorAll(".reveal");
  if (els.length) {
    const io = new IntersectionObserver(
      (entries) => entries.forEach(e => e.isIntersecting && e.target.classList.add("in")),
      { threshold: 0.14 }
    );
    els.forEach(el => io.observe(el));
  }
});
