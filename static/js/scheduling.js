/* eduPro scheduling — calendar interactions (vanilla JS, no frameworks)
   v2: day/week time-grids rendered from server JSON, live school-time clock,
   unified event/class modal, collapsible filters, debounced live search. */
(function () {
  'use strict';

  if (window.__schedulingInit) return;
  window.__schedulingInit = true;

  var doc = document;

  /* ── tiny helpers ──────────────────────────────────────────────────────── */

  function el(tag, cls, text) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function readJson(id, fallback) {
    var node = doc.getElementById(id);
    if (!node) return fallback;
    try { return JSON.parse(node.textContent); }
    catch (e) { return fallback; }
  }

  function safeCls(v) {
    return String(v || '').replace(/[^a-z0-9_-]/gi, '');
  }

  function tzOf() {
    var shell = doc.querySelector('.cal-shell');
    return shell ? (shell.getAttribute('data-tz') || '') : '';
  }

  /* Current date/time in the school's configured timezone. Falls back to the
     browser's local clock when no IANA name is available. */
  function zoneNow(tzName) {
    if (!tzName) return new Date();
    try {
      var parts = new Intl.DateTimeFormat('en-GB', {
        timeZone: tzName,
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        hour12: false
      }).formatToParts(new Date());
      function get(t) {
        var f = parts.filter(function (p) { return p.type === t; })[0];
        return f ? f.value : '0';
      }
      var h = parseInt(get('hour'), 10) % 24;
      return new Date(+get('year'), +get('month') - 1, +get('day'),
                     h, +get('minute'), +get('second'));
    } catch (e) {
      return new Date();
    }
  }

  function fmtHour(h) {
    var a = h % 12 || 12;
    return a + ':00 ' + (h < 12 ? 'AM' : 'PM');
  }

  /* ── Live clock (school timezone) ──────────────────────────────────────── */

  function initLiveClock() {
    var dateEl = doc.getElementById('calLiveDate');
    var timeEl = doc.getElementById('calLiveTime');
    if (!dateEl) return;
    var tzName = tzOf() || undefined;
    var dFmt = new Intl.DateTimeFormat('en-GB', {
      weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
      timeZone: tzName
    });
    var tFmt = new Intl.DateTimeFormat('en-GB', {
      hour: '2-digit', minute: '2-digit', hour12: true, timeZone: tzName
    });
    function tick() {
      var now = new Date();
      dateEl.textContent = dFmt.format(now);
      timeEl.textContent = tFmt.format(now);
    }
    tick();
    setInterval(tick, 20000);
  }

  /* ── Day / Week time-grid ──────────────────────────────────────────────── */

  var HOUR_PX = 52;

  function tgHourPx(host) {
    try {
      var v = getComputedStyle(host).getPropertyValue('--tg-hour').trim();
      var m = /^([\d.]+)px$/.exec(v);
      if (m) return parseFloat(m[1]);
    } catch (e) { /* fall through */ }
    return HOUR_PX;
  }

  function pad(n) { return String(n).padStart(2, '0'); }

  function initTimeGrid() {
    var host = doc.getElementById('cal-timegrid');
    if (!host) return;

    var mode = host.getAttribute('data-mode') || 'day';
    var hmin = parseInt(host.getAttribute('data-hmin'), 10);
    var hmax = parseInt(host.getAttribute('data-hmax'), 10);
    if (isNaN(hmin)) hmin = 7;
    if (isNaN(hmax)) hmax = 20;
    if (hmax <= hmin) hmax = hmin + 12;
    var spanH = hmax - hmin;
    var today = host.getAttribute('data-today') || '';
    window.__calHourPx = tgHourPx(host);

    var days = readJson('cal-days', []);
    var items = readJson('cal-items', []);

    host.innerHTML = '';

    /* Header row */
    var header = el('div', 'tg-header');
    header.appendChild(el('div', 'tg-gutter'));
    days.forEach(function (d) {
      var cell = el('div', 'tg-headcell');
      if (d.date === today) cell.classList.add('is-today');
      cell.appendChild(el('div', 'tg-head-main', String(d.num)));
      cell.appendChild(el('div', 'tg-head-sub', (d.label || '') + ' ' + (d.month || '')));
      header.appendChild(cell);
    });
    host.appendChild(header);

    /* All-day strip */
    var alldayItems = items.filter(function (it) { return it.allday; });
    if (alldayItems.length) {
      var strip = el('div', 'tg-allday');
      days.forEach(function (d) {
        var cell = el('div', 'tg-allday-cell');
        alldayItems.filter(function (it) { return it.date === d.date; })
          .forEach(function (it) {
            var b = el('button', 'tg-ad type-' + safeCls(it.type));
            b.type = 'button';
            b.title = it.title;
            b.appendChild(el('span', 'tg-ad-label', it.title));
            b.appendChild(el('span', 'tg-ad-time', it.time || 'All day'));
            b.setAttribute('data-open-url',
              it.url + (it.kind === 'class' ? '?date=' + it.date : ''));
            cell.appendChild(b);
          });
        strip.appendChild(cell);
      });
      host.appendChild(strip);
    }

    /* Body: gutter + day columns + positioned events */
    var body = el('div', 'tg-body');
    var gutter = el('div', 'tg-gutter');
    for (var h = hmin; h <= hmax; h++) {
      var hr = el('div', 'tg-hour');
      hr.appendChild(el('span', 'tg-hour-label', fmtHour(h)));
      gutter.appendChild(hr);
    }
    body.appendChild(gutter);

    var dayCols = [];
    days.forEach(function (d) {
      var col = el('div', 'tg-day');
      col.setAttribute('data-date', d.date);
      if (d.date === today) col.classList.add('is-today');
      for (var hh = hmin; hh <= hmax; hh++) col.appendChild(el('div', 'tg-slot'));
      dayCols.push(col);
      body.appendChild(col);
    });

    days.forEach(function (d, i) {
      var dayItems = items.filter(function (it) {
        return it.date === d.date && !it.allday;
      });
      layoutOverlaps(dayItems);
      dayItems.forEach(function (it) {
        dayCols[i].appendChild(buildTimeEvent(it, hmin, spanH));
      });
    });

    host.appendChild(body);

    initNowLine(host, body, today, hmin, spanH);
  }

  /* Greedy column packing so overlapping sessions never paint over each other. */
  function layoutOverlaps(list) {
    list.forEach(function (it) { it._col = 0; it._cols = 1; });
    if (!list.length) return;
    list.sort(function (a, b) { return (a.start - b.start) || (b.end - a.end); });
    var cols = [];
    list.forEach(function (it) {
      var placed = -1;
      for (var c = 0; c < cols.length; c++) {
        if (cols[c].last <= it.start) { placed = c; break; }
      }
      if (placed === -1) {
        cols.push({ last: it.end });
        placed = cols.length - 1;
      } else {
        cols[placed].last = Math.max(cols[placed].last, it.end);
      }
      it._col = placed;
    });
    list.forEach(function (it) { it._cols = cols.length; });
  }

  function buildTimeEvent(it, hmin, spanH) {
    var e = el('button', 'tg-ev type-' + safeCls(it.type));
    e.type = 'button';
    e.title = it.title + (it.time ? ' · ' + it.time : '');
    e.setAttribute('aria-label', e.title);

    var inner = el('span', 'tg-ev-inner');
    inner.appendChild(el('span', 'tg-ev-time', it.time || '—'));
    inner.appendChild(el('span', 'tg-ev-title', it.title));
    var bits = [];
    if (it.room) bits.push(it.room);
    if (it.lecturer) bits.push(it.lecturer);
    if (bits.length) inner.appendChild(el('span', 'tg-ev-sub', bits.join(' · ')));
    e.appendChild(inner);

    var total = spanH * 60;
    var top = (it.start - hmin * 60) / total * 100;
    var height = Math.max((it.end - it.start) / total * 100, 1.4);
    e.style.top = top + '%';
    e.style.height = height + '%';
    e.style.left = ((it._col || 0) * (100 / (it._cols || 1))) + '%';
    e.style.width = (100 / (it._cols || 1)) + '%';
    e.setAttribute('data-open-url',
      it.url + (it.kind === 'class' ? '?date=' + it.date : ''));

    if ((it.end - it.start) / 60 * (window.__calHourPx || HOUR_PX) < 26) {
      e.classList.add('is-short');
    }
    return e;
  }

  /* Current-time line + one-time scroll-to-now when viewing today. */
  function initNowLine(host, body, today, hmin, spanH) {
    if (!today) return;
    var col = null;
    for (var i = 0; i < body.children.length; i++) {
      var c = body.children[i];
      if (c.classList && c.classList.contains('tg-day') &&
          c.getAttribute('data-date') === today) { col = c; break; }
    }
    if (!col) return;

    function place() {
      var prev = col.querySelector('.tg-now');
      if (prev) prev.remove();
      var now = zoneNow(tzOf());
      var mins = now.getHours() * 60 + now.getMinutes();
      if (mins < hmin * 60 || mins > (hmin + spanH) * 60) return;
      var line = el('div', 'tg-now');
      line.style.top = (mins - hmin * 60) / (spanH * 60) * 100 + '%';
      col.appendChild(line);
      if (!body.getAttribute('data-autoscrolled')) {
        body.setAttribute('data-autoscrolled', '1');
        var px = (mins - hmin * 60) / 60 * (window.__calHourPx || HOUR_PX);
        var max = body.scrollHeight - body.clientHeight;
        body.scrollTop = Math.max(0, Math.min(px - body.clientHeight * 0.35, max));
      }
    }
    place();
    setInterval(place, 60000);
  }

  /* ── Highlight today in month/agenda views (server already marks it) ───── */

  function initToday() {
    var today = zoneNow(tzOf());
    var iso = today.getFullYear() + '-' + pad(today.getMonth() + 1) + '-' +
              pad(today.getDate());
    var cells = doc.querySelectorAll('.cal-grid .cal-day');
    for (var i = 0; i < cells.length; i++) {
      if (cells[i].getAttribute('data-date') === iso) {
        cells[i].classList.add('cal-today');
      }
    }
  }

  /* ── Unified event / class modal (fragment fetch into <dialog>) ────────── */

  function openEventModal(url) {
    var modal = doc.getElementById('cal-modal');
    var body = doc.getElementById('cal-modal-body');
    if (!modal || !body) { window.location.href = url; return; }

    body.innerHTML =
      '<div class="cal-skeleton">' +
      '<span class="sk-line sk-title-long"></span>' +
      '<span class="sk-line"></span><span class="sk-line"></span>' +
      '<span class="sk-line"></span></div>';
    if (typeof modal.showModal === 'function' && !modal.open) modal.showModal();

    fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.text();
      })
      .then(function (html) { body.innerHTML = html; })
      .catch(function () {
        body.innerHTML =
          '<div class="gen-modal"><div class="gen-rows">' +
          '<div class="gen-row"><span class="gen-key">Status</span>' +
          '<span class="gen-val">Could not load this item.</span></div>' +
          '</div></div>';
      });
  }

  function initModal() {
    var modal = doc.getElementById('cal-modal');
    if (!modal) return;

    doc.addEventListener('click', function (ev) {
      var t = ev.target.closest ? ev.target.closest('[data-open-url]') : null;
      if (!t) return;
      ev.preventDefault();
      var tz = t.getAttribute('data-open-url');
      if (t.getAttribute('data-kind') === 'class' && !/date=/.test(tz)) {
        if (t.getAttribute('data-date')) tz += '?date=' + t.getAttribute('data-date');
      }
      openEventModal(tz);
    });

    modal.addEventListener('click', function (ev) {
      if (ev.target === modal) { modal.close(); return; }
      if (ev.target.closest && ev.target.closest('[data-close-modal]')) {
        modal.close();
      }
    });

    doc.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && modal.open && typeof modal.close === 'function') {
        modal.close();
      }
    });
  }

  /* ── Filters: toggle + autosubmit ──────────────────────────────────────── */

  function initFilters() {
    var toggle = doc.getElementById('calFilterToggle');
    var panel = doc.getElementById('calFilters');
    if (!toggle || !panel) return;

    function reflect() {
      toggle.setAttribute('aria-expanded', panel.hidden !== true ? 'true' : 'false');
    }

    toggle.addEventListener('click', function () {
      panel.hidden = !panel.hidden;
      reflect();
    });
    if (toggle.getAttribute('data-open-if-filters') === '1') {
      panel.hidden = false;
    }
    reflect();

    var selects = panel.querySelectorAll('select');
    for (var i = 0; i < selects.length; i++) {
      selects[i].addEventListener('change', function () { panel.submit(); });
    }
  }

  /* Generic autosubmit for any form marked [data-autosubmit] */
  function initAutosubmits() {
    var forms = doc.querySelectorAll('form[data-autosubmit]');
    for (var i = 0; i < forms.length; i++) {
      var form = forms[i];
      if (form === doc.getElementById('calFilters')) continue;
      var selects = form.querySelectorAll('select');
      for (var j = 0; j < selects.length; j++) {
        selects[j].addEventListener('change', function () { form.submit(); });
      }
    }
  }

  /* ── Progressive confirmation (works inside modal fragments too) ───────── */

  function initConfirms() {
    doc.addEventListener('submit', function (ev) {
      var form = ev.target;
      if (!form || form.tagName !== 'FORM') return;
      var msg = form.getAttribute('data-confirm');
      if (!msg) return;
      if (!window.confirm(msg)) ev.preventDefault();
    });
  }

  /* ── Debounced live search against calendar_search ─────────────────────── */

  function initSearch() {
    var form = doc.querySelector('.cal-search');
    if (!form) return;
    var input = form.querySelector('input[name="q"]');
    var box = form.querySelector('.search-results');
    if (!input || !box) return;
    var timer = null;

    function closeBox() { box.hidden = true; }

    input.addEventListener('input', function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 2) { closeBox(); return; }
      timer = setTimeout(function () {
        fetch('/scheduling/calendar/search/?q=' + encodeURIComponent(q),
              { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
          .then(function (res) { return res.json(); })
          .then(function (data) {
            var results = data.results || [];
            if (!results.length) {
              box.innerHTML = '<div class="sr-empty">No matches.</div>';
            } else {
              box.innerHTML = results.map(function (r) {
                return '<a href="' + r.url + '">' +
                  '<span class="sr-label">' + r.label +
                  ' <span style="opacity:.55">(' + r.type + ')</span></span>' +
                  (r.sub ? '<span class="sr-sub">' + r.sub + '</span>' : '') +
                  '</a>';
              }).join('');
            }
            box.hidden = false;
          })
          .catch(function () { closeBox(); });
      }, 220);
    });

    doc.addEventListener('click', function (ev) {
      if (box.hidden) return;
      if (box.contains(ev.target) || form.contains(ev.target)) return;
      closeBox();
    }, true);

    doc.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && !box.hidden) closeBox();
    });

    form.addEventListener('submit', function (ev) { ev.preventDefault(); });
  }

  /* ── Horizontal wheel scrolling on legacy week grids ───────────────────── */

  function initWeekGrid() {
    var grid = doc.querySelector('.attendance-week-grid');
    if (!grid) return;

    grid.addEventListener('wheel', function (ev) {
      if (Math.abs(ev.deltaY) <= Math.abs(ev.deltaX)) return;
      if (grid.scrollWidth <= grid.clientWidth) return;
      ev.preventDefault();
      grid.scrollLeft += ev.deltaY;
    }, { passive: false });

    var dragging = false, startX = 0, startLeft = 0;
    grid.addEventListener('mousedown', function (ev) {
      if (ev.target.closest && ev.target.closest('a, button, input, select')) return;
      dragging = true;
      startX = ev.pageX;
      startLeft = grid.scrollLeft;
      grid.classList.add('dragging');
    });
    doc.addEventListener('mousemove', function (ev) {
      if (!dragging) return;
      grid.scrollLeft = startLeft - (ev.pageX - startX);
    });
    doc.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false;
      grid.classList.remove('dragging');
    });
  }

  /* ── Boot ──────────────────────────────────────────────────────────────── */

  function boot() {
    initToday();
    initLiveClock();
    initTimeGrid();
    initModal();
    initFilters();
    initAutosubmits();
    initConfirms();
    initSearch();
    initWeekGrid();
  }

  if (doc.readyState === 'loading') {
    doc.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();