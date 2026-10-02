/* ==========================================================================
   eduPro — public website behaviour
   Vanilla JS, no dependencies. Loaded by base_public.html only.
   Internal dashboards keep their own scripts.
   ========================================================================== */
(function () {
  'use strict';

  var THEME_KEY = 'edupro-theme';
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── Theme ───────────────────────────────────────────────────────────── */
  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* private mode */ }
    document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
      btn.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
      btn.setAttribute('aria-label', theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
    });
  }

  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) { /* ignore */ }
    if (!saved) {
      saved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    applyTheme(saved);
    document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
      });
    });
  }

  /* ── Sticky header shadow ────────────────────────────────────────────── */
  function initHeader() {
    var header = document.querySelector('[data-site-header]');
    if (!header) return;
    var ticking = false;
    function update() {
      header.classList.toggle('is-stuck', window.scrollY > 8);
      ticking = false;
    }
    window.addEventListener('scroll', function () {
      if (!ticking) { ticking = true; window.requestAnimationFrame(update); }
    }, { passive: true });
    update();
  }

  /* ── Focus helpers ───────────────────────────────────────────────────── */
  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function trapFocus(container, event) {
    var items = Array.prototype.filter.call(
      container.querySelectorAll(FOCUSABLE),
      function (el) { return el.offsetParent !== null; }
    );
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  /* ── Desktop dropdowns (click / keyboard; hover is pure CSS) ─────────── */
  function initDropdowns() {
    document.querySelectorAll('[data-dropdown]').forEach(function (item) {
      var trigger = item.querySelector('[data-dropdown-trigger]');
      if (!trigger) return;

      function setOpen(open) {
        item.classList.toggle('is-open', open);
        trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
      }

      trigger.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        var isOpen = item.classList.contains('is-open');
        closeAllDropdowns();
        setOpen(!isOpen);
      });

      item.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && item.classList.contains('is-open')) {
          setOpen(false);
          trigger.focus();
        }
      });

      item.addEventListener('focusout', function (e) {
        if (!item.contains(e.relatedTarget)) setOpen(false);
      });
    });

    document.addEventListener('click', function () { closeAllDropdowns(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeAllDropdowns();
    });
  }

  function closeAllDropdowns() {
    document.querySelectorAll('[data-dropdown].is-open').forEach(function (item) {
      item.classList.remove('is-open');
      var trigger = item.querySelector('[data-dropdown-trigger]');
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
    });
  }

  /* ── Mobile drawer ───────────────────────────────────────────────────── */
  function initDrawer() {
    var drawer = document.getElementById('site-drawer');
    var backdrop = document.getElementById('drawer-backdrop');
    var openBtn = document.querySelector('[data-drawer-open]');
    if (!drawer || !backdrop || !openBtn) return;

    var lastFocused = null;

    function setDrawerHidden(hidden) {
      if (hidden) {
        drawer.setAttribute('aria-hidden', 'true');
        drawer.setAttribute('inert', '');
      } else {
        drawer.removeAttribute('aria-hidden');
        drawer.removeAttribute('inert');
      }
    }

    function open() {
      lastFocused = document.activeElement;
      drawer.classList.add('is-open');
      backdrop.classList.add('is-open');
      setDrawerHidden(false);
      openBtn.setAttribute('aria-expanded', 'true');
      document.body.classList.add('is-locked');
      var first = drawer.querySelector(FOCUSABLE);
      if (first) first.focus();
      document.addEventListener('keydown', onKeydown);
    }

    function close() {
      drawer.classList.remove('is-open');
      backdrop.classList.remove('is-open');
      openBtn.setAttribute('aria-expanded', 'false');
      setDrawerHidden(true);
      document.body.classList.remove('is-locked');
      document.removeEventListener('keydown', onKeydown);
      if (lastFocused && lastFocused.focus) lastFocused.focus();
    }

    setDrawerHidden(true);

    function onKeydown(e) {
      if (e.key === 'Escape') { close(); return; }
      if (e.key === 'Tab') trapFocus(drawer, e);
    }

    openBtn.addEventListener('click', open);
    backdrop.addEventListener('click', close);
    drawer.addEventListener('click', function (e) {
      if (e.target.closest('[data-drawer-close]') || e.target.closest('a[href]')) close();
    });

    // Accordion groups inside the drawer
    drawer.querySelectorAll('[data-drawer-toggle]').forEach(function (trigger) {
      trigger.addEventListener('click', function () {
        var sub = document.getElementById(trigger.getAttribute('aria-controls'));
        if (!sub) return;
        var open = trigger.getAttribute('aria-expanded') === 'true';
        trigger.setAttribute('aria-expanded', open ? 'false' : 'true');
        sub.classList.toggle('is-open', !open);
      });
    });

    window.addEventListener('resize', function () {
      if (window.innerWidth >= 992 && drawer.classList.contains('is-open')) close();
    });
  }

  /* ── Scroll reveal ───────────────────────────────────────────────────── */
  function initReveal() {
    var items = document.querySelectorAll('[data-reveal]');
    if (!items.length) return;
    if (reduceMotion || !('IntersectionObserver' in window)) {
      items.forEach(function (el) { el.classList.add('is-in'); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        var delay = parseInt(el.getAttribute('data-reveal-delay') || '0', 10);
        if (delay) el.style.transitionDelay = delay + 'ms';
        el.classList.add('is-in');
        io.unobserve(el);
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    items.forEach(function (el) { io.observe(el); });
  }

  /* ── Programme / news filtering (no library, works offline) ──────────── */
  function initFilters() {
    var form = document.querySelector('[data-filter-form]');
    if (!form) return;

    var scope = form.getAttribute('data-filter-form') || 'programmes';
    var items = Array.prototype.slice.call(document.querySelectorAll('[data-filter-item]'));
    var empty = document.querySelector('[data-filter-empty]');
    var countEl = document.querySelector('[data-filter-count]');
    var resetBtn = document.querySelector('[data-filter-reset]');
    var search = form.querySelector('[data-filter-search]');
    var selects = Array.prototype.slice.call(form.querySelectorAll('[data-filter-select]'));

    function normalise(value) { return (value || '').toString().trim().toLowerCase(); }

    function apply() {
      var q = normalise(search ? search.value : '');
      var active = selects.map(function (sel) { return [sel.name, sel.value]; })
        .filter(function (pair) { return pair[1]; });

      var shown = 0;
      items.forEach(function (item) {
        var haystack = normalise(item.getAttribute('data-search'));
        var matchText = !q || haystack.indexOf(q) !== -1;
        var matchFacet = active.every(function (pair) {
          var field = item.getAttribute('data-' + pair[0]);
          return field && normalise(field).indexOf(normalise(pair[1])) !== -1;
        });
        var visible = matchText && matchFacet;
        item.hidden = !visible;
        if (visible) shown++;
      });

      if (countEl) {
        var total = items.length;
        countEl.innerHTML = shown === total
          ? 'Showing all <strong>' + total + '</strong> ' + scope
          : 'Showing <strong>' + shown + '</strong> of ' + total + ' ' + scope;
      }
      if (empty) empty.hidden = shown !== 0;
    }

    function reset() {
      if (search) search.value = '';
      selects.forEach(function (sel) { sel.value = ''; });
      // Clear any client-side chip state
      document.querySelectorAll('[data-filter-chip]').forEach(function (chip) {
        chip.setAttribute('aria-pressed', 'false');
      });
      apply();
    }

    if (search) search.addEventListener('input', debounce(apply, 140));
    selects.forEach(function (sel) { sel.addEventListener('change', apply); });
    if (resetBtn) resetBtn.addEventListener('click', reset);
    form.addEventListener('submit', function (e) { e.preventDefault(); apply(); });
    form.addEventListener('reset', function () { window.setTimeout(reset, 0); });

    // Optional chip shortcuts that mirror a select
    document.querySelectorAll('[data-filter-chip]').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var name = chip.getAttribute('data-filter-name');
        var value = chip.getAttribute('data-filter-value');
        var target = selects.filter(function (sel) { return sel.name === name; })[0];
        if (!target) return;
        var next = target.value === value ? '' : value;
        target.value = next;
        document.querySelectorAll('[data-filter-chip][data-filter-name="' + name + '"]').forEach(function (other) {
          other.setAttribute('aria-pressed', other.getAttribute('data-filter-value') === next ? 'true' : 'false');
        });
        apply();
      });
    });

    apply();
  }

  function debounce(fn, wait) {
    var timer;
    return function () {
      var args = arguments;
      window.clearTimeout(timer);
      timer = window.setTimeout(function () { fn.apply(null, args); }, wait);
    };
  }

  /* ── Application form: narrow the application types by programme ──────
     Purely a convenience. The server sends the same map in
     data-types-by-program and re-checks the pair on save. */
  function initApplicationTypeFilter() {
    var typeSelect = document.getElementById('id_application_type');
    var programSelect = document.getElementById('id_program_applied');
    if (!typeSelect || !programSelect) return;

    var byProgram;
    var allCodes;
    try {
      byProgram = JSON.parse(typeSelect.getAttribute('data-types-by-program') || '{}');
      allCodes = JSON.parse(typeSelect.getAttribute('data-types-all') || '[]');
    } catch (e) {
      return; // Malformed data must not break the form.
    }
    if (!Object.keys(byProgram).length) return;

    // Keep the full option list so it can be restored when the applicant
    // switches back to a programme that accepts more types.
    var allOptions = Array.prototype.map.call(typeSelect.options, function (opt) {
      return { value: opt.value, text: opt.text, hidden: opt.hidden };
    });
    if (!allCodes.length) {
      allCodes = allOptions.map(function (opt) { return opt.value; });
    }

    function narrow() {
      var allowed = byProgram[programSelect.value];
      var keep = allowed && allowed.length ? allowed : allCodes;
      var current = typeSelect.value;
      for (var i = 0; i < allOptions.length; i++) {
        var opt = typeSelect.options[i];
        opt.hidden = keep.indexOf(allOptions[i].value) === -1;
        opt.disabled = opt.hidden;
      }
      if (keep.indexOf(current) === -1) {
        // The chosen type is not offered by this programme; fall back to the
        // first one it does accept rather than leaving an invalid selection.
        for (var j = 0; j < allOptions.length; j++) {
          if (keep.indexOf(allOptions[j].value) !== -1) {
            typeSelect.value = allOptions[j].value;
            break;
          }
        }
      }
      typeSelect.dispatchEvent(new Event('change', { bubbles: true }));
    }

    programSelect.addEventListener('change', narrow);
    narrow();
  }

  /* ── Back to top ─────────────────────────────────────────────────────── */
  function initBackToTop() {
    var btn = document.querySelector('[data-back-to-top]');
    if (!btn) return;
    var ticking = false;
    function update() {
      btn.classList.toggle('is-visible', window.scrollY > 600);
      ticking = false;
    }
    window.addEventListener('scroll', function () {
      if (!ticking) { ticking = true; window.requestAnimationFrame(update); }
    }, { passive: true });
    btn.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' });
    });
    update();
  }

  /* ── Boot ────────────────────────────────────────────────────────────── */
  function boot() {
    initTheme();
    initHeader();
    initDropdowns();
    initDrawer();
    initReveal();
    initFilters();
    initApplicationTypeFilter();
    initBackToTop();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
