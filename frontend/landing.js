// /static/landing.js

// ===== Theme Toggle (persisted) =====
(function initTheme() {
  const saved = localStorage.getItem("theme");
  if (saved === "light" || saved === "dark") {
    document.documentElement.setAttribute("data-theme", saved);
  }
})();

function updateThemeButton() {
  const btn = document.getElementById("themeToggle");
  if (!btn) return;

  const theme = document.documentElement.getAttribute("data-theme") || "dark";
  const icon = btn.querySelector(".theme-icon");
  const text = btn.querySelector(".theme-text");

  if (theme === "light") {
    icon.textContent = "☀️";
    text.textContent = "Light";
  } else {
    icon.textContent = "🌙";
    text.textContent = "Dark";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  // Theme button click
  const btn = document.getElementById("themeToggle");
  if (btn) {
    btn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "dark";
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("theme", next);
      updateThemeButton();
    });
  }
  updateThemeButton();

  // Footer year
  const year = document.getElementById("year");
  if (year) year.textContent = new Date().getFullYear();

  // Smooth reveal on scroll
  const els = document.querySelectorAll(".reveal");
  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) e.target.classList.add("in");
      });
    },
    { threshold: 0.14 }
  );

  els.forEach((el) => io.observe(el));
});
