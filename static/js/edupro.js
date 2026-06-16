/**
 * eduPro global JavaScript — theme toggle, mobile nav, charts helper
 */
(function () {
  'use strict';

  const THEME_KEY = 'edupro-theme';

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(THEME_KEY, theme);
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = theme === 'dark' ? '☀️ Light' : '🌙 Dark';
  }

  function initTheme() {
    const saved = localStorage.getItem(THEME_KEY);
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    applyTheme(saved || (prefersDark ? 'dark' : 'light'));
  }

  function initThemeToggle() {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    btn.addEventListener('click', function () {
      const current = document.documentElement.getAttribute('data-theme') || 'light';
      applyTheme(current === 'dark' ? 'light' : 'dark');
    });
  }

  function initMobileNav() {
    const toggle = document.getElementById('nav-toggle');
    const sidebar = document.getElementById('sidebar');
    if (toggle && sidebar) {
      toggle.addEventListener('click', function () {
        sidebar.classList.toggle('open');
      });
    }
  }

  function renderBarChart(canvasId, labels, data, label) {
    if (typeof Chart === 'undefined') return;
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;
    new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          label: label,
          data: data,
          backgroundColor: 'rgba(79, 70, 229, 0.7)',
          borderRadius: 6,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          y: { beginAtZero: true, grid: { color: 'rgba(128,128,128,0.1)' } },
          x: { grid: { display: false } },
        },
      },
    });
  }

  window.eduPro = { renderBarChart: renderBarChart };

  document.addEventListener('DOMContentLoaded', function () {
    initTheme();
    initThemeToggle();
    initMobileNav();
  });
})();
