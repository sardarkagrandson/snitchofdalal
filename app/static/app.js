(function () {
  // ---- Theme toggle -------------------------------------------------------
  var btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.addEventListener("click", function () {
      var root = document.documentElement;
      var dark = root.classList.toggle("dark");
      try { localStorage.setItem("mw-theme", dark ? "dark" : "light"); } catch (e) {}
    });
  }

  // ---- Sparklines ---------------------------------------------------------
  // <svg data-sparkline='[...]' data-up="true"> - de-emphasis line, accent end-dot.
  document.querySelectorAll("svg[data-sparkline]").forEach(function (svg) {
    var pts;
    try { pts = JSON.parse(svg.getAttribute("data-sparkline")); } catch (e) { return; }
    if (!pts || pts.length < 2) return;
    var w = 240, h = 56, pad = 4;
    var min = Math.min.apply(null, pts), max = Math.max.apply(null, pts);
    var span = max - min || 1;
    var step = (w - pad * 2) / (pts.length - 1);
    var d = pts.map(function (v, i) {
      var x = pad + i * step, y = h - pad - ((v - min) / span) * (h - pad * 2);
      return (i === 0 ? "M" : "L") + x.toFixed(1) + " " + y.toFixed(1);
    }).join(" ");
    var up = svg.getAttribute("data-up") === "true";
    var accent = up ? "#00D09C" : "#EB5B5B";
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    var last = pts[pts.length - 1];
    var lx = pad + (pts.length - 1) * step, ly = h - pad - ((last - min) / span) * (h - pad * 2);
    svg.innerHTML =
      '<path d="' + d + '" fill="none" stroke="#B8BAC4" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' +
      '<circle cx="' + lx.toFixed(1) + '" cy="' + ly.toFixed(1) + '" r="4" fill="' + accent + '" stroke="white" stroke-width="2"/>';
  });

  // ---- Confirm destructive actions ---------------------------------------
  document.querySelectorAll("form[data-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (ev) {
      if (!window.confirm(f.getAttribute("data-confirm"))) ev.preventDefault();
    });
  });
})();

// ---- Ticker type-ahead -----------------------------------------------------
// Any <input data-suggest> gets a dropdown fed by /api/suggest. data-submit="1" submits
// the form on selection; otherwise the chosen symbol is written into the input.
(function () {
  var inputs = document.querySelectorAll("input[data-suggest]");
  if (!inputs.length) return;
  inputs.forEach(function (input) {
    var wrap = input.parentElement;
    if (getComputedStyle(wrap).position === "static") wrap.style.position = "relative";
    var box = document.createElement("div");
    box.className = "suggest";
    box.hidden = true;
    wrap.appendChild(box);
    var items = [], active = -1, timer = null, lastQ = "", ctrl = null;

    function close() { box.hidden = true; box.innerHTML = ""; items = []; active = -1; }
    function choose(i) {
      var it = items[i]; if (!it) return;
      input.value = it.symbol;
      close();
      if (input.getAttribute("data-submit") === "1" && input.form) input.form.submit();
    }
    function render() {
      if (!items.length) { close(); return; }
      box.innerHTML = items.map(function (it, i) {
        return '<button type="button" class="suggest-item' + (i === active ? ' is-active' : '') + '" data-i="' + i + '">' +
          '<span class="suggest-name">' + esc(it.name) + '</span>' +
          '<span class="suggest-sym num">' + esc(it.base_symbol) + ' <small>' + esc(it.exchange) + '</small></span></button>';
      }).join("");
      box.hidden = false;
    }
    function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]; }); }
    function fetchSuggest(q) {
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      fetch("/api/suggest?q=" + encodeURIComponent(q), { signal: ctrl.signal })
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (data) { if (q !== lastQ) return; items = data; active = -1; render(); })
        .catch(function () {});
    }
    input.addEventListener("input", function () {
      var q = input.value.trim(); lastQ = q;
      clearTimeout(timer);
      if (q.length < 1) { close(); return; }
      timer = setTimeout(function () { fetchSuggest(q); }, 120);
    });
    input.addEventListener("keydown", function (ev) {
      if (box.hidden) return;
      if (ev.key === "ArrowDown") { ev.preventDefault(); active = (active + 1) % items.length; render(); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); active = (active - 1 + items.length) % items.length; render(); }
      else if (ev.key === "Enter" && active >= 0) { ev.preventDefault(); choose(active); }
      else if (ev.key === "Escape") { close(); }
    });
    box.addEventListener("mousedown", function (ev) {
      var btn = ev.target.closest(".suggest-item"); if (!btn) return;
      ev.preventDefault(); choose(parseInt(btn.getAttribute("data-i"), 10));
    });
    input.addEventListener("blur", function () { setTimeout(close, 150); });
    input.setAttribute("autocomplete", "off");
  });
})();

// ---- Copy buttons ------------------------------------------------------------
document.querySelectorAll("[data-copy]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var el = document.querySelector(btn.getAttribute("data-copy"));
    if (!el || !navigator.clipboard) return;
    navigator.clipboard.writeText(el.textContent.trim()).then(function () {
      var t = btn.textContent; btn.textContent = "Copied"; setTimeout(function () { btn.textContent = t; }, 1500);
    });
  });
});

// ---- Simple tabs (IPO page) ---------------------------------------------------
(function () {
  var btns = document.querySelectorAll(".tab-btn");
  if (!btns.length) return;
  function show(name) {
    document.querySelectorAll(".tab-panel").forEach(function (p) { p.hidden = p.id !== "tab-" + name; });
    btns.forEach(function (b) {
      var on = b.getAttribute("data-tab") === name;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    try { history.replaceState(null, "", "#" + name); } catch (e) {}
  }
  btns.forEach(function (b) { b.addEventListener("click", function () { show(b.getAttribute("data-tab")); }); });
  var initial = (location.hash || "").replace("#", "");
  show(document.getElementById("tab-" + initial) ? initial : btns[0].getAttribute("data-tab"));
})();

// ---- IPO page: reload once the background refresh finishes --------------------
(function () {
  var el = document.getElementById("ipo-loading");
  if (!el) return;
  var seen = el.getAttribute("data-updated") || "";
  var tries = 0;
  (function poll() {
    if (tries++ > 120) return;  // ~6 minutes
    fetch("/api/ipo/status").then(function (r) { return r.json(); }).then(function (s) {
      if (!s.loading && (s.updated_at || "") !== seen) { location.reload(); return; }
      if (!s.loading && s.error) { location.reload(); return; }
      setTimeout(poll, 3000);
    }).catch(function () { setTimeout(poll, 5000); });
  })();
})();

// ---- Price chart (stock page) ----------------------------------------------
(function () {
  var card = document.getElementById("chart-card");
  if (!card) return;
  var symbol = card.getAttribute("data-symbol");
  var chartBase = card.getAttribute("data-chart-url") || ("/api/chart/" + encodeURIComponent(symbol));
  var defaultRange = card.getAttribute("data-default-range") || "1y";
  var currency = card.getAttribute("data-currency") || "₹";
  var svg = document.getElementById("price-chart");
  var tip = document.getElementById("chart-tip");
  var empty = document.getElementById("chart-empty");
  var meta = document.getElementById("chart-meta");
  var changeEl = document.getElementById("chart-change");
  var rangeLabel = document.getElementById("chart-range-label");
  var customForm = document.getElementById("chart-custom");
  var btns = card.querySelectorAll(".range-btn");
  var NS = "http://www.w3.org/2000/svg";
  var state = { points: [], intraday: false, xs: [], ys: [], w: 0, h: 0 };
  var ctrl = null;

  function el(name, attrs) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function fmtPrice(v) { return currency + v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: v < 100 ? 4 : 2 }); }
  function fmtTime(ms, intraday, long) {
    var d = new Date(ms);
    var opts = intraday ? { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }
                        : { day: "2-digit", month: "short", year: long ? "numeric" : undefined };
    return d.toLocaleString("en-IN", Object.assign({ timeZone: "Asia/Kolkata" }, opts));
  }

  function draw(data) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var pts = data.points || [];
    state.points = pts; state.intraday = data.intraday;
    empty.classList.toggle("hidden", pts.length > 1);
    empty.classList.toggle("flex", pts.length <= 1);
    if (pts.length <= 1) { changeEl.textContent = "—"; return; }

    var rect = svg.getBoundingClientRect();
    var w = state.w = Math.max(rect.width, 320), h = state.h = Math.max(rect.height, 200);
    var padL = 56, padR = 12, padT = 10, padB = 24;
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    var xs = pts.map(function (p) { return p[0]; }), ys = pts.map(function (p) { return p[1]; });
    var xmin = xs[0], xmax = xs[xs.length - 1], ymin = Math.min.apply(null, ys), ymax = Math.max.apply(null, ys);
    if (ymax === ymin) { ymax += 1; ymin -= 1; }
    var pad = (ymax - ymin) * 0.08; ymin -= pad; ymax += pad;
    var X = function (t) { return padL + (t - xmin) / (xmax - xmin || 1) * (w - padL - padR); };
    var Y = function (v) { return padT + (1 - (v - ymin) / (ymax - ymin)) * (h - padT - padB); };
    state.xs = xs.map(X); state.ys = ys.map(Y);

    // Gridlines + y labels (4 steps)
    for (var i = 0; i <= 4; i++) {
      var v = ymin + (ymax - ymin) * i / 4, y = Y(v);
      svg.appendChild(el("line", { x1: padL, x2: w - padR, y1: y, y2: y, class: "chart-grid" }));
      var t = el("text", { x: padL - 6, y: y + 3, "text-anchor": "end", class: "chart-axis" });
      t.textContent = v >= 1000 ? Math.round(v).toLocaleString("en-IN") : v.toFixed(v >= 100 ? 1 : 2);
      svg.appendChild(t);
    }
    // x labels (5)
    for (var j = 0; j <= 4; j++) {
      var idx = Math.round((pts.length - 1) * j / 4);
      var tx = el("text", { x: X(xs[idx]), y: h - 6, "text-anchor": j === 0 ? "start" : j === 4 ? "end" : "middle", class: "chart-axis" });
      tx.textContent = fmtTime(xs[idx], data.intraday, false);
      svg.appendChild(tx);
    }
    var up = ys[ys.length - 1] >= ys[0];
    var color = up ? "#00D09C" : "#EB5B5B";
    var d = pts.map(function (p, k) { return (k ? "L" : "M") + state.xs[k].toFixed(1) + " " + state.ys[k].toFixed(1); }).join(" ");
    var gid = "chart-fill-" + (up ? "up" : "down");
    var defs = el("defs", {});
    var grad = el("linearGradient", { id: gid, x1: 0, x2: 0, y1: 0, y2: 1 });
    var s1 = el("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.18 });
    var s2 = el("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0 });
    grad.appendChild(s1); grad.appendChild(s2); defs.appendChild(grad); svg.appendChild(defs);
    svg.appendChild(el("path", { d: d + " L" + state.xs[state.xs.length - 1].toFixed(1) + " " + (h - padB) + " L" + state.xs[0].toFixed(1) + " " + (h - padB) + " Z", fill: "url(#" + gid + ")" }));
    svg.appendChild(el("path", { d: d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    // Hover elements
    var cross = el("line", { y1: padT, y2: h - padB, class: "chart-grid", "stroke-dasharray": "3 3", visibility: "hidden", id: "chart-cross" });
    var dot = el("circle", { r: 4, fill: color, stroke: "#fff", "stroke-width": 2, visibility: "hidden", id: "chart-dot" });
    svg.appendChild(cross); svg.appendChild(dot);

    var ch = data.change, pct = data.change_pct;
    changeEl.textContent = (ch >= 0 ? "+" : "") + ch.toFixed(2) + " (" + (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%)";
    changeEl.className = "num text-lg font-semibold " + (ch >= 0 ? "text-mint-600" : "text-crimson-600");
    rangeLabel.textContent = fmtTime(xs[0], data.intraday, true) + " → " + fmtTime(xs[xs.length - 1], data.intraday, true);
    meta.textContent = "High " + fmtPrice(data.high) + " · Low " + fmtPrice(data.low) + " · " + pts.length + " points";
  }

  function onMove(ev) {
    if (!state.points.length) return;
    var rect = svg.getBoundingClientRect();
    var px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - rect.left;
    var sx = px * state.w / rect.width;
    var best = 0, bd = Infinity;
    for (var i = 0; i < state.xs.length; i++) { var dd = Math.abs(state.xs[i] - sx); if (dd < bd) { bd = dd; best = i; } }
    var cross = document.getElementById("chart-cross"), dot = document.getElementById("chart-dot");
    cross.setAttribute("x1", state.xs[best]); cross.setAttribute("x2", state.xs[best]); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", state.xs[best]); dot.setAttribute("cy", state.ys[best]); dot.setAttribute("visibility", "visible");
    var p = state.points[best];
    tip.textContent = fmtTime(p[0], state.intraday, true) + "  " + fmtPrice(p[1]);
    tip.hidden = false;
    var left = state.xs[best] * rect.width / state.w;
    tip.style.left = Math.min(Math.max(left, 70), rect.width - 70) + "px";
    tip.style.top = Math.max(state.ys[best] * rect.height / state.h - 34, 0) + "px";
  }
  function onLeave() {
    tip.hidden = true;
    var c = document.getElementById("chart-cross"), d = document.getElementById("chart-dot");
    if (c) c.setAttribute("visibility", "hidden"); if (d) d.setAttribute("visibility", "hidden");
  }
  svg.addEventListener("mousemove", onMove); svg.addEventListener("touchmove", onMove, { passive: true });
  svg.addEventListener("mouseleave", onLeave); svg.addEventListener("touchend", onLeave);

  function load(range, start, end) {
    btns.forEach(function (b) { b.classList.toggle("is-active", b.getAttribute("data-range") === range); });
    customForm.classList.toggle("hidden", range !== "custom");
    customForm.classList.toggle("flex", range === "custom");
    if (range === "custom" && !start) return;
    meta.textContent = "Loading…";
    if (ctrl) ctrl.abort(); ctrl = new AbortController();
    var url = chartBase + "?range=" + range + (start ? "&start=" + start + (end ? "&end=" + end : "") : "");
    fetch(url, { signal: ctrl.signal }).then(function (r) { return r.ok ? r.json() : r.json().then(function (e) { throw new Error(e.detail || "error"); }); })
      .then(draw)
      .catch(function (e) { if (e.name !== "AbortError") { meta.textContent = "Could not load chart: " + e.message; empty.classList.remove("hidden"); empty.classList.add("flex"); } });
    try { localStorage.setItem("sod-chart-range:" + (card.getAttribute("data-chart-url") ? "mf" : "stock"), range); } catch (e) {}
  }
  btns.forEach(function (b) { b.addEventListener("click", function () { load(b.getAttribute("data-range")); }); });
  customForm.addEventListener("submit", function (ev) { ev.preventDefault(); load("custom", customForm.start.value, customForm.end.value); });
  // Hooks for the live quote poller: push a fresh last price into a daily chart, or refetch an intraday one.
  var currentRange = null;
  var origLoad = load;
  load = function (range, start, end) { currentRange = range; return origLoad(range, start, end); };
  window.sodChart = {
    isIntraday: function () { return state.intraday; },
    refresh: function () { if (currentRange && currentRange !== "custom") origLoad(currentRange); },
    setLast: function (price, ts) {
      if (!state.points.length || state.intraday) return;
      var last = state.points[state.points.length - 1];
      var sameDay = new Date(last[0]).toDateString() === new Date(ts).toDateString();
      if (sameDay) { last[1] = price; } else { state.points.push([ts, price]); }
      var ys = state.points.map(function (p) { return p[1]; });
      draw({ points: state.points, intraday: false, change: ys[ys.length - 1] - ys[0], change_pct: (ys[ys.length - 1] / ys[0] - 1) * 100,
             high: Math.max.apply(null, ys), low: Math.min.apply(null, ys) });
    }
  };
  var saved = null; try { saved = localStorage.getItem("sod-chart-range:" + (card.getAttribute("data-chart-url") ? "mf" : "stock")); } catch (e) {}
  var allowed = Array.prototype.map.call(btns, function (b) { return b.getAttribute("data-range"); });
  load(saved && saved !== "custom" && allowed.indexOf(saved) >= 0 ? saved : defaultRange);
  var rt; window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(function () { if (state.points.length) draw({ points: state.points, intraday: state.intraday, change: parseFloat(changeEl.textContent) || 0, change_pct: 0, high: Math.max.apply(null, state.points.map(function (p) { return p[1]; })), low: Math.min.apply(null, state.points.map(function (p) { return p[1]; })) }); }, 150); });
})();

// ---- Mutual fund guide: shown once, then hidden ------------------------------
(function () {
  var guide = document.getElementById("mf-guide");
  if (!guide) return;
  function setSeen(v) { try { localStorage.setItem("sod-mf-guide-seen", v ? "1" : "0"); } catch (e) {} document.documentElement.classList.toggle("mf-guide-seen", v); }
  var hide = document.getElementById("mf-guide-hide"), show = document.getElementById("mf-guide-show");
  if (hide) hide.addEventListener("click", function () { setSeen(true); });
  if (show) show.addEventListener("click", function () { setSeen(false); guide.scrollIntoView({ behavior: "smooth" }); });
})();

// ---- Live quote on the stock page ----------------------------------------------
(function () {
  var box = document.getElementById("live-quote");
  if (!box) return;
  var symbol = box.getAttribute("data-symbol"), cur = box.getAttribute("data-currency") || "₹";
  var priceEl = document.getElementById("live-price"), changeEl = document.getElementById("live-change");
  var asofEl = document.getElementById("live-asof"), srcEl = document.getElementById("live-source"), badge = document.getElementById("live-badge");
  var lowEl = document.getElementById("live-day-low"), highEl = document.getElementById("live-day-high"), marker = document.getElementById("live-day-marker");
  var volEl = document.getElementById("live-volume");
  var lastPrice = null, timer = null, stopped = false;
  function fmt(v, d) { return v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function signed(v, suffix, d) {
    var cls = v > 0 ? "text-mint-600" : v < 0 ? "text-crimson-600" : "";
    return '<span class="num ' + cls + '">' + (v > 0 ? "+" : "") + fmt(v, d) + (suffix || "") + "</span>";
  }
  function apply(q) {
    if (lastPrice !== null && q.ltp !== lastPrice) {
      priceEl.classList.remove("flash-up", "flash-down");
      void priceEl.offsetWidth;
      priceEl.classList.add(q.ltp > lastPrice ? "flash-up" : "flash-down");
      setTimeout(function () { priceEl.classList.remove("flash-up", "flash-down"); }, 900);
    }
    lastPrice = q.ltp;
    priceEl.textContent = cur + fmt(q.ltp, 2);
    changeEl.innerHTML = signed(q.change, "", 2) + ' <span class="text-ink-500">(</span>' + signed(q.change_pct, "%", 2) + '<span class="text-ink-500">)</span>';
    if (q.as_of) asofEl.textContent = q.as_of.slice(0, 16).replace("T", " ");
    if (srcEl) srcEl.textContent = q.source;
    if (lowEl && q.day_low != null) lowEl.textContent = fmt(q.day_low, 2);
    if (highEl && q.day_high != null) highEl.textContent = fmt(q.day_high, 2);
    if (marker && q.day_low != null && q.day_high != null && q.day_high > q.day_low) {
      marker.style.left = Math.max(0, Math.min(100, (q.ltp - q.day_low) / (q.day_high - q.day_low) * 100)).toFixed(1) + "%";
    }
    if (volEl && q.volume != null) volEl.textContent = fmt(q.volume, 0);
    badge.classList.toggle("hidden", !q.market_open);
    if (window.sodChart) {
      if (window.sodChart.isIntraday()) { if (q.market_open) window.sodChart.refresh(); }
      else if (q.market_open) window.sodChart.setLast(q.ltp, Date.parse(q.as_of) || Date.now());
    }
  }
  function tick() {
    if (stopped || document.hidden) { timer = setTimeout(tick, 5000); return; }
    fetch("/api/quote/" + encodeURIComponent(symbol)).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (q) { var wait = 15000; if (q) { apply(q); wait = q.next_poll_ms || 15000; } timer = setTimeout(tick, wait); })
      .catch(function () { timer = setTimeout(tick, 20000); });
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) { clearTimeout(timer); tick(); } });
  timer = setTimeout(tick, box.getAttribute("data-open") === "true" ? 3000 : 60000);
})();
