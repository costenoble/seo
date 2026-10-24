(function () {
  "use strict";
  var SEV = ["critical", "high", "medium", "low", "info"];
  var SEV_LABEL = { critical: "Critique", high: "Élevée", medium: "Moyenne", low: "Faible", info: "Info" };
  var CAT_LABEL = { http: "HTTP", indexabilite: "Indexabilité", contenu: "Titres et contenu", liens: "Liens",
    performance: "Performance", technique: "Technique", international: "International", donnees: "Données structurées" };
  var TINTS = ["blue", "pink", "", "blue", "pink", ""];
  var main = document.getElementById("main"), overlay = document.getElementById("overlay");
  var CONFIG = null, sb = null, SESSION = null, ME = null;

  /* ---------- utilitaires ---------- */
  // Tout contenu venant d'un site exploré passe par textContent : jamais d'innerHTML avec des données.
  function h(tag, attrs) {
    var n = document.createElement(tag), kids = Array.prototype.slice.call(arguments, 2);
    for (var k in attrs || {}) {
      var v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    kids.flat(Infinity).forEach(function (c) { if (c != null && c !== false) n.append(c.nodeType ? c : document.createTextNode(String(c))); });
    return n;
  }
  function svg(tag, attrs) {
    var n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (var k in attrs || {}) n.setAttribute(k, attrs[k]);
    Array.prototype.slice.call(arguments, 2).forEach(function (c) { n.append(c.nodeType ? c : document.createTextNode(c)); });
    return n;
  }
  // Remplace le contenu d'un noeud ; accepte des listes imbriquées, contrairement à replaceChildren.
  function fill(node) {
    var kids = Array.prototype.slice.call(arguments, 1).flat(Infinity).filter(function (c) { return c != null && c !== false; });
    node.replaceChildren.apply(node, kids.map(function (c) { return c.nodeType ? c : document.createTextNode(String(c)); }));
    if (node === main) {  // transition : les blocs de la page apparaissent l'un après l'autre
      Array.prototype.forEach.call(main.children, function (c, i) { c.style.setProperty("--n", Math.min(i, 10)); });
      main.querySelectorAll(".grid, .kpis, .features").forEach(function (g) {
        Array.prototype.forEach.call(g.children, function (c, i) { c.style.setProperty("--n2", Math.min(i, 12)); });
      });
      main.classList.remove("enter"); void main.offsetWidth; main.classList.add("enter");
    }
    countUp(node);
  }
  var REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // Les chiffres des indicateurs et des anneaux montent de 0 jusqu'à leur valeur.
  function countUp(root) {
    if (REDUCED || !root.querySelectorAll) return;
    root.querySelectorAll(".kpi .v, .ring .v, .price").forEach(function (el) {
      if (el.dataset.counted) return;
      el.dataset.counted = "1";
      var first = el.firstChild;
      if (!first || first.nodeType !== 3) return;
      var m = first.nodeValue.match(/^(\D*?)(-?\d[\d\s\u202f\u00a0]*(?:,\d+)?)(.*)$/);
      if (!m) return;
      var target = parseFloat(m[2].replace(/[\s\u202f\u00a0]/g, "").replace(",", "."));
      if (!isFinite(target) || target === 0) return;
      var dec = (m[2].split(",")[1] || "").length, start = null, dur = 900 + Math.min(600, String(Math.round(target)).length * 60);
      function frame(t) {
        if (start === null) start = t;
        var k = Math.min(1, (t - start) / dur), e = 1 - Math.pow(1 - k, 3);
        first.nodeValue = m[1] + (target * e).toLocaleString("fr-FR", { minimumFractionDigits: dec, maximumFractionDigits: dec }) + m[3];
        if (k < 1) requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    });
  }
  var nf = new Intl.NumberFormat("fr-FR");
  function fmt(n, d) { return n == null || n === "" ? "–" : (d ? Number(n).toLocaleString("fr-FR", { maximumFractionDigits: d }) : nf.format(Math.round(n))); }
  // Pays dans l'ordre utile (les clés numériques d'un objet JS sont sinon triées par code)
  var COUNTRY_ORDER = ["2250", "2056", "2756", "2124"];
  function countryKeys(obj) { return COUNTRY_ORDER.filter(function (k) { return k in obj; }).concat(Object.keys(obj).filter(function (k) { return COUNTRY_ORDER.indexOf(k) === -1; })); }
  function fdate(s, long) { return new Date(s).toLocaleDateString("fr-FR", long ? { day: "numeric", month: "long", year: "numeric" } : { day: "numeric", month: "short" }); }
  function fdatetime(s) { return new Date(s).toLocaleString("fr-FR", { dateStyle: "long", timeStyle: "short" }); }
  function scoreColor(s) { return s >= 85 ? "var(--green)" : s >= 60 ? "var(--sev-medium)" : "var(--orange)"; }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

  function authHeaders() { return SESSION ? { Authorization: "Bearer " + SESSION.access_token } : {}; }
  function api(path, body, method) {
    var opts = { method: method || (body === undefined ? "GET" : "POST"), headers: authHeaders() };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.status === 401 && SESSION) { sb.auth.signOut(); }
        if (!r.ok) { var e = new Error(d.error || ("Erreur " + r.status)); e.status = r.status; e.data = d; throw e; }
        return d;
      });
    });
  }
  function download(path, name) {
    return fetch(path, { headers: authHeaders() }).then(function (r) {
      if (!r.ok) throw new Error("Téléchargement impossible");
      return r.blob();
    }).then(function (b) { saveBlob(b, name); });
  }
  function saveBlob(blob, name) {
    var a = h("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1500);
  }
  function csv(rows, name) {
    var data = rows.map(function (r) { return r.map(function (c) { return '"' + String(c == null ? "" : c).replace(/"/g, '""') + '"'; }).join(";"); }).join("\n");
    saveBlob(new Blob(["﻿" + data], { type: "text/csv" }), name);
  }
  function loading() { fill(main, h("div", { class: "empty" }, h("span", { class: "spinner", role: "status", "aria-label": "Chargement" }))); }
  function errorBox(e) {
    var box = h("div", { class: "error", role: "alert" }, e.message || String(e));
    if (e.status === 402) box.append(" ", h("a", { href: "#/compte" }, "Voir les offres"));
    return box;
  }
  function fail(e) { fill(main, h("div", { class: "empty" }, h("h2", {}, "Oups"), errorBox(e), h("a", { class: "btn", href: "#/" }, "Retour"))); }

  /* ---------- composants ---------- */
  function ring(score, size) {
    var r = 42, c = 2 * Math.PI * r, wrap = h("div", { class: "ring", role: "img", "aria-label": "Score " + score + " sur 100" });
    wrap.style.setProperty("--s", (size || 96) + "px");
    wrap.append(svg("svg", { viewBox: "0 0 100 100" },
      svg("circle", { cx: 50, cy: 50, r: r, fill: "none", style: "stroke: var(--line)", "stroke-width": 10 }),
      svg("circle", { cx: 50, cy: 50, r: r, fill: "none", style: "stroke:" + scoreColor(score), "stroke-width": 10, "stroke-linecap": "round",
        "stroke-dasharray": (REDUCED ? c * score / 100 : 0) + " " + c })), h("span", { class: "v" }, score));
    if (!REDUCED) setTimeout(function () { wrap.querySelector("circle:last-child").setAttribute("stroke-dasharray", (c * score / 100) + " " + c); }, 60);
    return wrap;
  }
  function delta(d) {
    if (d == null || d === 0) return null;
    return h("span", { class: "delta " + (d > 0 ? "up" : "down") }, (d > 0 ? "▲ +" : "▼ ") + d);
  }
  function sevDot(s) { var d = h("span", { class: "dot" }); d.style.background = "var(--sev-" + s + ")"; return d; }
  function sevs(counts) {
    return h("div", { class: "sevs" }, SEV.filter(function (s) { return counts && counts[s]; }).map(function (s) {
      return h("span", {}, sevDot(s), h("b", {}, fmt(counts[s])), " " + SEV_LABEL[s].toLowerCase());
    }));
  }
  function lineChart(points, o) {
    o = o || {};
    var W = 760, H = o.height || 240, P = { l: 40, r: 16, t: 14, b: 28 };
    if (!points.length) return h("p", { class: "muted" }, "Pas encore de données.");
    var ys = points.map(function (p) { return p.y; });
    var lo = o.min != null ? o.min : Math.min.apply(null, ys), hi = o.max != null ? o.max : Math.max.apply(null, ys);
    if (lo === hi) { lo -= 1; hi += 1; }
    var xs = points.map(function (p) { return +p.x; }), x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    var X = function (v) { return P.l + (x1 === x0 ? (W - P.l - P.r) / 2 : (v - x0) / (x1 - x0) * (W - P.l - P.r)); };
    var Y = function (v) { var t = (v - lo) / (hi - lo); return P.t + (o.invert ? t : 1 - t) * (H - P.t - P.b); };
    var color = o.color || "var(--ink)", s = svg("svg", { class: "chart", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.label || "Graphique" });
    for (var i = 0; i <= 4; i++) {
      var v = lo + (hi - lo) * i / 4, y = Y(v);
      s.append(svg("line", { class: "grid-line", x1: P.l, x2: W - P.r, y1: y, y2: y }), svg("text", { x: P.l - 8, y: y + 4, "text-anchor": "end" }, fmt(v, hi - lo < 10 ? 1 : 0)));
    }
    var d = points.map(function (p, i) { return (i ? "L" : "M") + X(+p.x).toFixed(1) + " " + Y(p.y).toFixed(1); }).join(" ");
    if (points.length > 1) s.append(svg("path", { class: "area", d: d + " L" + X(x1) + " " + (H - P.b) + " L" + X(x0) + " " + (H - P.b) + " Z", style: "fill:" + color }));
    s.append(svg("path", { d: d, fill: "none", style: "stroke:" + color, "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    points.forEach(function (p, i) {
      if (points.length > 40 && i !== points.length - 1) return;
      s.append(svg("circle", { cx: X(+p.x), cy: Y(p.y), r: 5, style: "fill: var(--paper); stroke:" + color, "stroke-width": 3 }, svg("title", {}, fdate(p.x, true) + " : " + fmt(p.y, 1))));
    });
    (points.length > 1 ? [points[0], points[points.length - 1]] : [points[0]]).forEach(function (p, i) {
      s.append(svg("text", { x: X(+p.x), y: H - 6, "text-anchor": points.length === 1 ? "middle" : i ? "end" : "start" }, fdate(p.x)));
    });
    return s;
  }
  function spark(values) {
    var W = 120, H = 36;
    if (values.length < 2) return h("span");
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    if (lo === hi) { lo -= 1; hi += 1; }
    var d = values.map(function (v, i) { return (i ? "L" : "M") + (i / (values.length - 1) * (W - 4) + 2).toFixed(1) + " " + (H - 4 - (v - lo) / (hi - lo) * (H - 8)).toFixed(1); }).join(" ");
    return svg("svg", { width: W, height: H, viewBox: "0 0 " + W + " " + H, "aria-hidden": "true" }, svg("path", { d: d, fill: "none", style: "stroke: var(--ink)", "stroke-width": 2.5, "stroke-linecap": "round", "stroke-linejoin": "round" }));
  }
  function kpi(value, label, tint) { return h("div", { class: "kpi " + (tint || "") }, h("span", { class: "v" }, value), h("span", { class: "l" }, label)); }
  function emptyState(title, text, cta) {
    return h("div", { class: "card empty" }, eyeMark(96), h("h2", {}, title), h("p", { class: "muted" }, text), cta);
  }
  function progressBar(done, total) {
    var pct = total ? Math.min(100, Math.round(done / total * 100)) : 5, bar = h("div", { class: "progress", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 });
    var fillEl = h("span"); fillEl.style.width = pct + "%"; bar.append(fillEl); return bar;
  }
  var ICONS = {
    audit: "M4 6h16M4 12h10M4 18h7M17 15l3 3-3 3",
    chart: "M4 19V5M4 19h16M8 15l4-4 3 3 5-6",
    search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
    users: "M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2 20c0-3 3-5 6-5s6 2 6 5M16 11a3 3 0 1 0 0-6M18 15c2 .5 4 2 4 5"
  };
  function icon(name) {
    return h("span", { class: "icon", "aria-hidden": "true" }, svg("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "#2bb673", "stroke-width": 2.2, "stroke-linecap": "round", "stroke-linejoin": "round" }, svg("path", { d: ICONS[name] })));
  }

  /* ---------- l'œil de la vigie : il suit la souris, regarde autour de lui et cligne ---------- */
  var eyes = [], pointer = null, eyeFrame = 0, eyeCount = 0;
  function eyeMark(size, extraClass) {
    var id = "eye-clip-" + (++eyeCount), w = 32 * 1.28, hh = 32 * 0.78;
    var el = svg("svg", { class: "eye-anim " + (extraClass || ""), viewBox: "0 0 64 64", width: size, height: size, "aria-hidden": "true" },
      svg("defs", {}, svg("clipPath", { id: id }, svg("circle", { cx: 32, cy: 32, r: 27.5 }))),
      svg("circle", { class: "eye-bg", cx: 32, cy: 32, r: 32 }),
      svg("g", { "clip-path": "url(#" + id + ")" }, svg("g", { class: "eye-lid" },
        svg("path", { class: "eye-white", d: "M" + (32 - w) + " 32 Q32 " + (32 - hh * 1.25) + " " + (32 + w) + " 32 Q32 " + (32 + hh * 1.25) + " " + (32 - w) + " 32Z" }),
        svg("g", { class: "eye-pupil" }, svg("circle", { cx: 32, cy: 32, r: 11.5, fill: "#2bb673" }), svg("circle", { cx: 32, cy: 32, r: 4.8, fill: "#0b0b0b" }),
          svg("circle", { cx: 36.2, cy: 27.8, r: 1.9, fill: "#fff" })))));
    watchEye(el);
    return el;
  }
  function watchEye(el) {
    if (REDUCED || el._eye) return;
    el._eye = { pupil: el.querySelector(".eye-pupil"), bg: el.querySelector(".eye-bg"), idle: [0, 0] };
    eyes.push(el);
    (function blinkLater() {   // clignement à intervalles irréguliers, parfois double
      setTimeout(function () {
        if (!el.isConnected) return;
        blink(el); if (Math.random() < .25) setTimeout(function () { blink(el); }, 260);
        blinkLater();
      }, 2500 + Math.random() * 4500);
    })();
  }
  function blink(el) { el.classList.remove("blink"); void el.getBoundingClientRect(); el.classList.add("blink"); setTimeout(function () { el.classList.remove("blink"); }, 230); }
  function look() {
    eyeFrame = 0;
    eyes = eyes.filter(function (el) { return el.isConnected; });
    eyes.forEach(function (el) {
      var e = el._eye, box = e.bg.getBoundingClientRect(), r = +e.bg.getAttribute("r"), max = r * .3, dx, dy;
      if (!box.width) return;
      if (pointer) {
        dx = pointer.x - (box.left + box.width / 2); dy = pointer.y - (box.top + box.height / 2);
        var d = Math.hypot(dx, dy) || 1, k = Math.min(1, d / (box.width * 2.5));
        dx = dx / d * max * k; dy = dy / d * max * k * .65;
      } else { dx = e.idle[0] * max; dy = e.idle[1] * max * .6; }
      e.pupil.style.transform = "translate(" + dx.toFixed(2) + "px," + dy.toFixed(2) + "px)";
    });
  }
  function scheduleLook() { if (!eyeFrame) eyeFrame = requestAnimationFrame(look); }
  if (!REDUCED) {
    document.addEventListener("mousemove", function (e) { pointer = { x: e.clientX, y: e.clientY }; scheduleLook(); }, { passive: true });
    document.addEventListener("mouseleave", function () { pointer = null; scheduleLook(); });
    window.addEventListener("scroll", scheduleLook, { passive: true });
    // sans souris (mobile, ou souris immobile hors de la page) : l'œil regarde de lui-même autour de lui
    setInterval(function () {
      if (pointer) return;
      var dirs = [[0, 0], [-1, 0], [1, 0], [-.7, -.6], [.7, -.6], [0, .8], [0, 0]];
      eyes.forEach(function (el) { el._eye.idle = dirs[Math.floor(Math.random() * dirs.length)]; });
      look();
    }, 2600);
    document.querySelectorAll(".eye-anim").forEach(watchEye);
  }

  /* ---------- menus déroulants maison ----------
     Le <select> natif reste dans la page (valeur, formulaires, événements « change ») mais caché ;
     un bouton et une liste aux couleurs du site le remplacent à l'écran. Clavier : flèches, Début/Fin,
     Entrée/Espace, Échap, Tab, et saisie d'une lettre pour aller à l'option correspondante. */
  var selCount = 0;
  function chevron() {
    return svg("svg", { viewBox: "0 0 24 24", width: 18, height: 18, "aria-hidden": "true" },
      svg("path", { d: "M6 9.5l6 6 6-6", fill: "none", stroke: "currentColor", "stroke-width": 2.8, "stroke-linecap": "round", "stroke-linejoin": "round" }));
  }
  function enhanceSelect(sel) {
    if (sel.dataset.enhanced || sel.multiple) return;
    sel.dataset.enhanced = "1";
    var uid = "sel" + (++selCount), wrap = h("div", { class: "sel" }), label = h("span", { class: "sel-label" });
    var btn = h("button", { type: "button", class: "sel-btn", "aria-haspopup": "listbox", "aria-expanded": "false", "aria-controls": uid + "-list" },
      label, h("span", { class: "sel-arrow" }, chevron()));
    var list = h("ul", { class: "sel-list", role: "listbox", id: uid + "-list", tabindex: -1, hidden: true });
    if (sel.id) { btn.id = sel.id; sel.id = sel.id + "-natif"; }           // les <label for> visent le bouton
    if (sel.getAttribute("aria-label")) btn.setAttribute("aria-label", sel.getAttribute("aria-label"));
    if (sel.getAttribute("style")) wrap.setAttribute("style", sel.getAttribute("style"));
    sel.parentNode.insertBefore(wrap, sel);
    wrap.append(sel, btn, list);
    sel.tabIndex = -1; sel.setAttribute("aria-hidden", "true");
    var active = 0, typed = "", typedAt = 0;
    function sync() { var o = sel.options[sel.selectedIndex]; label.textContent = o ? o.text : ""; }
    new MutationObserver(sync).observe(sel, { childList: true, subtree: true, attributes: true, characterData: true });
    sel.addEventListener("change", sync); sync();
    function setActive(i) {
      var opts = list.children; if (!opts.length) return;
      active = Math.max(0, Math.min(opts.length - 1, i));
      Array.prototype.forEach.call(opts, function (li, k) { li.classList.toggle("active", k === active); });
      btn.setAttribute("aria-activedescendant", opts[active].id);
      opts[active].scrollIntoView({ block: "nearest" });
    }
    function open() {
      if (!list.hidden) return;
      document.querySelectorAll(".sel.open").forEach(function (w) { if (w !== wrap && w._close) w._close(); });
      fill(list, Array.prototype.map.call(sel.options, function (o, i) {
        var li = h("li", { role: "option", id: uid + "-o" + i, class: "sel-opt", "aria-selected": String(i === sel.selectedIndex), style: "--i:" + Math.min(i, 12) },
          h("span", {}, o.text), i === sel.selectedIndex ? h("span", { class: "sel-check", "aria-hidden": "true" }, "✓") : null);
        li.addEventListener("mousedown", function (e) { e.preventDefault(); });
        li.addEventListener("click", function () { choose(i); });
        li.addEventListener("mousemove", function () { if (active !== i) setActive(i); });
        return li;
      }));
      var rect = btn.getBoundingClientRect();
      wrap.classList.toggle("up", window.innerHeight - rect.bottom < 300 && rect.top > 300);
      list.hidden = false; wrap.classList.add("open"); btn.setAttribute("aria-expanded", "true");
      setActive(Math.max(0, sel.selectedIndex));
      document.addEventListener("mousedown", outside, true);
    }
    function close() {
      if (list.hidden) return;
      list.hidden = true; wrap.classList.remove("open"); btn.setAttribute("aria-expanded", "false");
      btn.removeAttribute("aria-activedescendant");
      document.removeEventListener("mousedown", outside, true);
    }
    wrap._close = close;
    function outside(e) { if (!wrap.contains(e.target)) close(); }
    function choose(i) {
      if (i !== sel.selectedIndex) { sel.selectedIndex = i; sel.dispatchEvent(new Event("change", { bubbles: true })); sel.dispatchEvent(new Event("input", { bubbles: true })); }
      sync(); close(); btn.focus();
    }
    btn.addEventListener("click", function () { if (list.hidden) open(); else close(); });
    btn.addEventListener("keydown", function (e) {
      var isOpen = !list.hidden, k = e.key;
      if (k === "ArrowDown" || k === "ArrowUp") { e.preventDefault(); if (!isOpen) open(); else setActive(active + (k === "ArrowDown" ? 1 : -1)); }
      else if (k === "Home" || k === "End") { if (isOpen) { e.preventDefault(); setActive(k === "Home" ? 0 : list.children.length - 1); } }
      else if (k === "Enter" || k === " ") { e.preventDefault(); if (isOpen) choose(active); else open(); }
      else if (k === "Escape") { if (isOpen) { e.preventDefault(); e.stopPropagation(); close(); } }
      else if (k === "Tab") close();
      else if (k.length === 1 && /\S/.test(k)) {
        var now = Date.now(); typed = (now - typedAt > 700 ? "" : typed) + k.toLowerCase(); typedAt = now;
        var i = Array.prototype.findIndex.call(sel.options, function (o) { return o.text.toLowerCase().indexOf(typed) === 0; });
        if (i >= 0) { if (!isOpen) open(); setActive(i); }
      }
    });
  }
  new MutationObserver(function (records) {
    records.forEach(function (r) { r.addedNodes.forEach(function (n) {
      if (n.nodeType !== 1) return;
      if (n.tagName === "SELECT") enhanceSelect(n); else n.querySelectorAll && n.querySelectorAll("select").forEach(enhanceSelect);
    }); });
  }).observe(document.body, { childList: true, subtree: true });

  /* ---------- notifications maison (bas de l'écran) ---------- */
  var toastBox = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
  document.body.append(toastBox);
  function toast(message, tone) {
    var t = h("div", { class: "toast " + (tone || "") }, h("span", { class: "toast-dot", "aria-hidden": "true" }), h("span", {}, message),
      h("button", { type: "button", class: "toast-close", "aria-label": "Fermer", onclick: function () { dismiss(); } }, "×"));
    function dismiss() { t.classList.add("leaving"); setTimeout(function () { t.remove(); }, 300); }
    toastBox.append(t);
    setTimeout(dismiss, tone === "bad" ? 7000 : 4000);
  }

  /* ---------- fenêtres ---------- */
  function closeOverlay() { overlay.hidden = true; fill(overlay); }
  function modal(title, body) {
    fill(overlay, h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title }, body));
    overlay.hidden = false;
    var f = overlay.querySelector("input, textarea, button"); if (f) f.focus();
  }
  overlay.addEventListener("click", function (e) { if (e.target === overlay) closeOverlay(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !overlay.hidden) closeOverlay(); });

  // Fait avancer un audit tant que la fenêtre est ouverte (le planificateur continue sinon).
  function followAudit(auditId, title, onDone) {
    var status = h("p", { class: "muted" }, "Préparation…"), barBox = h("div", {}, progressBar(0, 0)), err = h("div");
    var stopped = false;
    modal(title, [h("p", { class: "eyebrow" }, "Audit en cours"), h("h2", {}, title), barBox, status, err,
      h("p", { class: "note" }, "Vous pouvez fermer cette fenêtre : l'audit continue sur nos serveurs et apparaîtra dans vos projets."),
      h("div", { class: "form-row" }, h("button", { class: "btn btn-small", type: "button", onclick: function () { stopped = true; closeOverlay(); } }, "Continuer en arrière-plan"))]);
    var PHASE = { init: "Lecture de robots.txt et des sitemaps…", crawl: "Exploration des pages…", sitemap: "Vérification des pages du sitemap…", finalize: "Calcul du rapport…" };
    (function loop() {
      if (stopped) return;
      api("/api/audits/" + auditId + "/step", {}).then(function (a) {
        fill(barBox, progressBar(a.progress.done, a.progress.total));
        status.textContent = (PHASE[a.phase] || "En cours…") + (a.progress.total ? " " + fmt(a.progress.done) + " / " + fmt(a.progress.total) + " pages" : "");
        if (a.status === "done") { closeOverlay(); toast("Audit terminé ✓" + (a.score != null ? " Score : " + a.score + "/100" : ""), "good"); onDone(a); return; }
        if (a.status === "failed") { fill(err, errorBox(new Error(a.error || "L'audit a échoué"))); return; }
        setTimeout(loop, 600);
      }).catch(function (e) { fill(err, errorBox(e)); setTimeout(loop, 4000); });
    })();
  }

  function newAuditModal(prefill) {
    var max = ME ? ME.limits.max_pages : 100;
    var url = h("input", { type: "text", id: "au-url", value: prefill || "", placeholder: "www.exemple.fr", autocomplete: "off", inputmode: "url", required: true });
    var pages = h("input", { type: "number", id: "au-pages", value: Math.min(200, max), min: 1, max: max });
    var err = h("div"), goBtn = h("button", { class: "btn btn-dark", type: "submit" }, "Lancer l'audit");
    var form = h("form", { style: "display:grid;gap:18px", onsubmit: function (e) {
      e.preventDefault(); goBtn.disabled = true; fill(err);
      api("/api/audits", { url: url.value, max_pages: +pages.value }).then(function (r) {
        followAudit(r.id, url.value.replace(/^https?:\/\//, ""), function (a) { refreshMe(); go("#/audit/" + a.id); });
      }).catch(function (x) { goBtn.disabled = false; fill(err, errorBox(x)); });
    } },
      h("div", { class: "field" }, h("label", { for: "au-url" }, "Adresse du site"), url),
      h("div", { class: "field" }, h("label", { for: "au-pages" }, "Pages à explorer (jusqu'à " + fmt(max) + " avec votre offre)"), pages),
      err, h("div", { class: "form-row" }, goBtn, h("button", { class: "btn", type: "button", onclick: closeOverlay }, "Annuler")));
    var usage = ME ? h("p", { class: "note" }, fmt(ME.usage.audit) + " / " + fmt(ME.limits.audits_month) + " audits utilisés ce mois-ci.") : null;
    modal("Nouvel audit", [h("p", { class: "eyebrow" }, "Nouvel audit"), h("h2", {}, "Quel site analyser ?"), form, usage]);
  }

  /* ---------- page publique ---------- */
  function signIn() {
    sessionStorage.setItem("after-login", location.hash && location.hash !== "#/offres" ? location.hash : "#/");
    sb.auth.signInWithOAuth({ provider: "google", options: { redirectTo: location.origin + "/" } });
  }
  function plansGrid(current) {
    var P = CONFIG.plans;
    return h("div", { class: "features" }, Object.keys(P).map(function (k, i) {
      var p = P[k], featured = k === "pro";
      return h("div", { class: "card plan " + (featured ? "ink" : TINTS[i]) },
        featured ? h("span", { class: "tag" }, "Le plus choisi") : null,
        h("h3", { style: "margin-top:12px" }, p.label),
        h("p", { class: "price" }, p.price ? p.price + " €" : "0 €", h("small", {}, p.price ? " / mois" : " pour toujours")),
        h("ul", {},
          h("li", {}, fmt(p.max_pages) + " pages par audit"), h("li", {}, fmt(p.audits_month) + " audits par mois"),
          h("li", {}, fmt(p.gsc_properties) + " propriété" + (p.gsc_properties > 1 ? "s" : "") + " Search Console suivie" + (p.gsc_properties > 1 ? "s" : "")),
          h("li", {}, fmt(p.keywords_month) + " recherches de mots-clés par mois"), h("li", {}, fmt(p.tracked_keywords) + " mots-clés suivis chaque jour"), h("li", {}, fmt(p.domains_month) + " analyses de domaines par mois"), h("li", {}, fmt(p.competitors_month) + " analyses de concurrents par mois")),
        current === k ? h("span", { class: "btn btn-small", "aria-disabled": "true" }, "Votre offre actuelle")
          : current ? h("a", { class: "btn btn-small" + (featured ? " btn-green" : ""), href: "mailto:contact@vigieseo.fr?subject=" + encodeURIComponent("Passer à l'offre " + p.label) }, p.price ? "Passer à " + p.label : "Revenir au gratuit")
            : h("button", { class: "btn " + (featured ? "btn-green" : "btn-dark"), type: "button", onclick: signIn }, p.price ? "Essayer gratuitement" : "Commencer"));
    }));
  }
  function viewLanding(scrollToOffers) {
    var feats = [
      ["audit", "Audit technique complet", "Une quarantaine de contrôles : erreurs, redirections, indexabilité, balises, hreflang, données structurées, contenus dupliqués.", "blue"],
      ["chart", "Suivi de vos positions", "Vos requêtes Search Console importées chaque jour : clics, impressions, position et tendances.", "pink"],
      ["search", "Idées de mots-clés", "Suggestions, questions et variantes réellement tapées par les internautes, croisées avec vos requêtes.", ""],
      ["users", "Face à vos concurrents", "Comparez balises, contenu, données structurées et temps de réponse avec les sites qui vous devancent.", "blue"]];
    fill(main,
      h("section", { class: "landing-hero" },
        h("div", { class: "hero" }, h("p", { class: "eyebrow" }, "Audit et suivi SEO"),
          h("h1", {}, "Gardez un œil sur la santé de vos sites."),
          h("p", {}, "Vigie SEO explore votre site, repère ce qui freine son référencement et suit vos positions Google, audit après audit."),
          h("div", { class: "actions" }, h("button", { class: "btn btn-dark", type: "button", onclick: signIn }, "Commencer gratuitement"),
            h("a", { class: "btn", href: "#/offres" }, "Voir les offres")),
          h("p", { class: "note" }, "Connexion avec votre compte Google. Sans carte bancaire.")),
        h("div", { class: "hero-art", "aria-hidden": "true" }, eyeMark(280, "hero-eye"),
          h("span", { class: "bubble", style: "top:12%;left:8%" }, "Score 92 ▲"), h("span", { class: "bubble", style: "bottom:14%;right:6%" }, "3 erreurs corrigées"))),
      h("section", {}, h("h2", { style: "margin-bottom:22px" }, "Tout ce qu'il faut pour progresser"),
        h("div", { class: "features" }, feats.map(function (f) { return h("div", { class: "card " + f[3] }, icon(f[0]), h("h3", {}, f[1]), h("p", {}, f[2])); }))),
      h("section", { class: "section", id: "offres" }, h("h2", { style: "margin-bottom:22px" }, "Des offres simples"), plansGrid(null)));
    if (scrollToOffers) setTimeout(function () { document.getElementById("offres").scrollIntoView(); }, 50);
  }

  /* ---------- vue : projets ---------- */
  function viewHome() {
    loading();
    api("/api/sites").then(function (sites) {
      var hero = h("section", { class: "hero" }, h("p", { class: "eyebrow" }, "Vos projets"),
        h("h1", { style: "font-size:clamp(2rem,4vw,3rem)" }, "Bonjour" + (firstName() ? ", " + firstName() : "") + "."),
        h("div", { class: "actions" }, h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(); } }, "Analyser un site"),
          h("a", { class: "btn", href: "#/positions" }, "Suivre mes positions")));
      var body;
      if (!sites.length) body = emptyState("Aucun audit pour le moment", "Lancez votre premier audit : il apparaîtra ici avec son score et son historique.",
        h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(); } }, "Lancer un audit"));
      else body = h("div", { class: "grid" }, sites.map(function (s, i) {
        var l = s.latest, d = l && l.comparison && delta(l.comparison.score_delta);
        return h("a", { class: "card site-card " + TINTS[i % TINTS.length], href: "#/site/" + encodeURIComponent(s.key) },
          h("div", { class: "row" }, h("div", {}, h("p", { class: "name" }, s.key),
            h("p", { class: "muted" }, l ? s.audits + " audit" + (s.audits > 1 ? "s" : "") + " · dernier le " + fdate(l.date, true) : "Premier audit en cours…")),
            l ? ring(l.score) : h("span", { class: "spinner" })),
          h("div", { class: "row", style: "align-items:end" }, l ? sevs(l.issues_by_severity) : h("span"),
            h("div", { style: "display:grid;gap:6px;justify-items:end" }, s.running && l ? h("span", { class: "delta" }, "Audit en cours") : d, spark(s.history.map(function (x) { return x.score; })))));
      }));
      fill(main, hero, body);
    }).catch(fail);
  }

  /* ---------- vue : projet ---------- */
  function viewSite(key) {
    loading();
    api("/api/sites/" + encodeURIComponent(key)).then(function (site) {
      var audits = site.audits, done = audits.filter(function (a) { return a.status === "done"; }), last = done[0];
      if (!last) {
        var running = audits.find(function (a) { return a.status === "queued" || a.status === "running"; });
        fill(main, h("a", { class: "crumb", href: "#/" }, "← Projets"), h("div", { style: "height:16px" }),
          running ? emptyState("Premier audit en cours", "L'exploration de " + key + " est en cours.", h("button", { class: "btn btn-dark", type: "button", onclick: function () { followAudit(running.id, key, function (a) { go("#/audit/" + a.id); }); } }, "Suivre l'avancement"))
            : emptyState("Aucun audit terminé", audits[0] && audits[0].error ? "Le dernier audit a échoué : " + audits[0].error : "Relancez un audit de ce site.", h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(key); } }, "Relancer un audit")));
        return;
      }
      var cats = Object.keys(last.category_scores || {}), prev = done[1];
      fill(main,
        h("a", { class: "crumb", href: "#/" }, "← Projets"),
        h("div", { class: "page-head", style: "margin-top:16px" }, ring(last.score, 132),
          h("div", { class: "grow" }, h("p", { class: "eyebrow" }, "Projet"), h("h1", { style: "font-size:clamp(2rem,4vw,3rem);word-break:break-word" }, key),
            h("p", { class: "muted" }, fmt(last.pages) + " pages explorées, dont " + fmt(last.indexable) + " indexables · audit du " + fdatetime(last.date)),
            sevs(last.issues_by_severity)),
          h("div", { class: "form-row" }, h("a", { class: "btn btn-dark", href: "#/plan/" + encodeURIComponent(key) }, "Plan d'action"),
            h("a", { class: "btn", href: "#/audit/" + last.id }, "Voir les problèmes"),
            h("button", { class: "btn", type: "button", onclick: function () { newAuditModal(last.url); } }, "Relancer un audit"))),
        h("section", {}, h("h2", { style: "margin-bottom:20px" }, "Score par catégorie"),
          h("div", { class: "grid", style: "grid-template-columns:repeat(auto-fill,minmax(min(100%,220px),1fr))" }, cats.map(function (c, i) {
            var p = prev && prev.category_scores && prev.category_scores[c];
            return h("div", { class: "card " + TINTS[i % TINTS.length], style: "padding:22px;display:flex;gap:16px;align-items:center" },
              ring(last.category_scores[c], 64), h("div", {}, h("h3", { style: "font-size:1rem" }, CAT_LABEL[c] || c), p != null ? delta(last.category_scores[c] - p) : null));
          }))),
        h("section", { class: "section" }, h("h2", { style: "margin-bottom:20px" }, "Évolution du score"),
          h("div", { class: "card" }, lineChart(done.slice().reverse().map(function (a) { return { x: new Date(a.date), y: a.score }; }),
            { min: 0, max: 100, color: "#137445", label: "Score de santé au fil des audits" }))),
        h("section", { class: "section" }, h("header", {}, h("h2", {}, "Historique des audits"),
          h("a", { class: "btn btn-small", href: "#/concurrents/" + encodeURIComponent(key) }, "Comparer à des concurrents")),
          h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Date"), h("th", { class: "num" }, "Score"), h("th", { class: "num" }, "Pages"),
            h("th", {}, "Problèmes"), h("th", {}, "Évolution"), h("th", {}, h("span", { class: "sr-only" }, "Actions")))),
            h("tbody", {}, audits.map(function (a) {
              var c = a.comparison, ok = a.status === "done";
              return h("tr", { class: ok ? "click" : "", onclick: ok ? function (e) { if (e.target.tagName !== "BUTTON") go("#/audit/" + a.id); } : null },
                h("td", {}, ok ? h("a", { href: "#/audit/" + a.id }, fdatetime(a.date)) : fdatetime(a.date)),
                h("td", { class: "num" }, ok ? h("b", {}, a.score) : "–"), h("td", { class: "num" }, fmt(a.pages)),
                h("td", {}, ok ? sevs(a.issues_by_severity) : h("span", { class: "muted" }, a.status === "failed" ? "Échec : " + (a.error || "") : "En cours…")),
                h("td", {}, c ? [delta(c.score_delta), " ", h("span", { class: "muted" }, "+" + c.new_count + " / −" + c.fixed_count)] : ok ? h("span", { class: "muted" }, "Premier audit") : null),
                h("td", {}, h("button", { class: "btn btn-small", type: "button", "aria-label": "Supprimer l'audit du " + fdatetime(a.date), onclick: function () {
                  if (!confirm("Supprimer cet audit ?")) return;
                  api("/api/audits/" + a.id, undefined, "DELETE").then(function () { toast("Audit supprimé", "good"); viewSite(key); }).catch(function (e) { toast(e.message, "bad"); });
                } }, "Supprimer")));
            }))))));
    }).catch(fail);
  }

  /* ---------- vue : audit ---------- */
  function viewAudit(id) {
    loading();
    api("/api/audits/" + id).then(function (a) {
      if (a.status !== "done") {
        fill(main, h("a", { class: "crumb", href: "#/site/" + encodeURIComponent(a.site) }, "← " + a.site), h("div", { style: "height:16px" }),
          a.status === "failed" ? emptyState("Audit en échec", a.error || "", h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(a.url); } }, "Relancer"))
            : emptyState("Audit en cours", fmt(a.progress.done) + " pages explorées pour l'instant.", h("button", { class: "btn btn-dark", type: "button", onclick: function () { followAudit(id, a.site, function () { viewAudit(id); }); } }, "Suivre l'avancement")));
        return;
      }
      return api("/api/audits/" + id + "/report").then(function (data) { renderAudit(id, a, data); });
    }).catch(fail);
  }
  function renderAudit(id, a, data) {
    var S = data.summary, issues = data.issues, site = a.site, slug = site.replace(/[^a-z0-9.-]/gi, "");
    var state = { cat: "", sev: "", q: "" };
    var pillsEl = h("div", { class: "pills", role: "group", "aria-label": "Filtrer par catégorie" });
    var listEl = h("div"), countEl = h("p", { class: "muted", "aria-live": "polite", style: "margin-bottom:14px" });
    var catCounts = {};
    issues.forEach(function (i) { catCounts[i.category] = (catCounts[i.category] || 0) + 1; });
    function pill(label, value, n) {
      return h("button", { class: "pill", type: "button", "aria-pressed": String(state.cat === value), onclick: function () { state.cat = value; renderPills(); renderList(); } }, label, h("span", { class: "n" }, n));
    }
    function renderPills() { fill(pillsEl, pill("Tous", "", issues.length), Object.keys(catCounts).map(function (c) { return pill(CAT_LABEL[c] || c, c, catCounts[c]); })); }
    function renderList() {
      var term = state.q.toLowerCase();
      var f = issues.filter(function (i) {
        return (!state.cat || i.category === state.cat) && (!state.sev || i.severity === state.sev) &&
          (!term || (i.url + " " + i.message + " " + i.detail).toLowerCase().indexOf(term) !== -1);
      });
      var groups = {};
      f.forEach(function (i) { (groups[i.code] = groups[i.code] || { severity: i.severity, message: i.message.replace(/\s*\(.*\)$/, "").replace(/\s*:.*$/, ""), items: [] }).items.push(i); });
      var arr = Object.values(groups).sort(function (x, y) { return SEV.indexOf(x.severity) - SEV.indexOf(y.severity) || y.items.length - x.items.length; });
      countEl.textContent = fmt(f.length) + " problème" + (f.length > 1 ? "s" : "") + " sur " + fmt(arr.length) + " type" + (arr.length > 1 ? "s" : "");
      fill(listEl, arr.length ? arr.map(function (g, idx) {
        return h("details", { class: "acc", open: arr.length === 1 || (idx === 0 && !!state.cat) },
          h("summary", {}, sevDot(g.severity), g.message, h("span", { class: "n" }, fmt(g.items.length))),
          h("ul", {}, g.items.slice(0, 100).map(function (i) {
            return h("li", {}, h("a", { class: "url", href: i.url, target: "_blank", rel: "noopener noreferrer" }, i.url), i.detail ? h("span", { class: "detail" }, i.detail) : null);
          }), g.items.length > 100 ? h("li", { class: "muted" }, "… et " + fmt(g.items.length - 100) + " autres (voir le CSV)") : null));
      }) : h("div", { class: "card empty" }, h("h3", {}, "Rien à signaler ici"), h("p", { class: "muted" }, "Aucun problème ne correspond à ces filtres.")));
    }
    var sevSel = h("select", { "aria-label": "Gravité", onchange: function (e) { state.sev = e.target.value; renderList(); } },
      h("option", { value: "" }, "Toutes les gravités"), SEV.map(function (s) { return h("option", { value: s }, SEV_LABEL[s]); }));
    var search = h("input", { type: "search", placeholder: "Filtrer par URL ou texte", "aria-label": "Filtrer par URL ou texte", oninput: function (e) { state.q = e.target.value; renderList(); } });
    renderPills(); renderList();
    function exportIssues() { csv([["url", "categorie", "code", "severite", "message", "detail"]].concat(issues.map(function (i) { return [i.url, CAT_LABEL[i.category] || i.category, i.code, SEV_LABEL[i.severity], i.message, i.detail]; })), "problemes-" + slug + ".csv"); }
    function exportPages() { var cols = data.pages.length ? Object.keys(data.pages[0]) : ["url"]; csv([cols].concat(data.pages.map(function (p) { return cols.map(function (c) { return p[c]; }); })), "pages-" + slug + ".csv"); }
    var cmp = S.comparison;
    fill(main,
      h("a", { class: "crumb", href: "#/site/" + encodeURIComponent(site) }, "← " + site),
      h("div", { class: "page-head", style: "margin-top:16px" }, ring(S.health_score, 112),
        h("div", { class: "grow" }, h("p", { class: "eyebrow" }, "Audit du " + fdatetime(S.date)), h("h1", { style: "font-size:clamp(1.8rem,3.6vw,2.6rem)" }, "Problèmes à corriger"), sevs(S.issues_by_severity)),
        h("div", { class: "form-row" },
          h("button", { class: "btn btn-small", type: "button", onclick: function () { download("/api/audits/" + id + "/report.html", "rapport-" + slug + ".html").then(function () { toast("Rapport téléchargé ✓", "good"); }).catch(function (e) { toast(e.message, "bad"); }); } }, "Rapport HTML"),
          h("button", { class: "btn btn-small", type: "button", onclick: exportIssues }, "CSV problèmes"),
          h("button", { class: "btn btn-small", type: "button", onclick: exportPages }, "CSV pages"))),
      h("div", { class: "kpis" }, kpi(fmt(S.pages_crawled), "pages explorées"), kpi(fmt(S.pages_indexable), "pages indexables", "blue"), kpi(fmt(S.sitemap_urls), "URLs dans le sitemap"),
        cmp ? kpi((cmp.score_delta > 0 ? "+" : "") + cmp.score_delta, "points depuis le " + fdate(cmp.previous_date), cmp.score_delta >= 0 ? "blue" : "pink") : null),
      cmp ? h("section", { class: "section" }, h("h2", { style: "margin-bottom:20px" }, "Depuis l'audit précédent"),
        h("div", { class: "grid" },
          h("details", { class: "acc", style: "align-self:start" }, h("summary", {}, h("span", { class: "dot", style: "background:var(--orange)" }), "Nouveaux problèmes", h("span", { class: "n" }, fmt(cmp.new_count))), changeList(cmp.new)),
          h("details", { class: "acc", style: "align-self:start" }, h("summary", {}, h("span", { class: "dot", style: "background:var(--green)" }), "Problèmes corrigés", h("span", { class: "n" }, fmt(cmp.fixed_count))), changeList(cmp.fixed)))) : null,
      h("section", { class: "section" }, h("header", {}, h("h2", {}, "Tous les problèmes"), h("div", { class: "form-row" }, sevSel, search)),
        pillsEl, h("div", { style: "height:20px" }), countEl, listEl));
  }
  function changeList(items) {
    return h("ul", {}, items.slice(0, 60).map(function (i) {
      return h("li", {}, sevDot(i.severity), i.message, h("a", { class: "detail url", href: i.url, target: "_blank", rel: "noopener noreferrer" }, i.url));
    }), items.length ? null : h("li", { class: "muted" }, "Aucun"));
  }

  /* ---------- vue : positions ---------- */
  function connectSearchConsole() {
    // Nouvelle autorisation Google avec la permission Search Console (lecture seule) ; au retour,
    // onAuthStateChange transmet le jeton de rafraîchissement au serveur, qui le chiffre.
    sessionStorage.setItem("gsc-connect", "1");
    sessionStorage.setItem("after-login", "#/positions");
    sb.auth.signInWithOAuth({ provider: "google", options: { redirectTo: location.origin + "/", scopes: CONFIG.google_scope,
      queryParams: { access_type: "offline", prompt: "consent", login_hint: ME && ME.email || "" } } });
  }
  function viewPositions(flash) {
    loading();
    api("/api/google/status").then(function (st) {
      var head = h("section", { class: "hero", style: "margin-bottom:28px" }, h("p", { class: "eyebrow" }, "Search Console"), h("h1", {}, "Où apparaissent vos pages ?"),
        h("p", {}, "Clics, impressions et position moyenne de chaque requête, importés depuis la Search Console puis mis à jour automatiquement chaque jour."));
      var msg = h("div", { role: "status" });
      if (flash === "ok") fill(msg, h("p", { class: "delta up", style: "padding:10px 16px;margin-bottom:16px" }, "Search Console connectée."));
      if (flash === "erreur") fill(msg, errorBox(new Error("La connexion à la Search Console a échoué. Vérifiez que vous avez coché l'accès à la Search Console.")));
      if (flash === "refuse") fill(msg, errorBox(new Error("Google a refusé l'accès à la Search Console. Tant que l'application est en test, seuls les comptes ajoutés comme « utilisateurs test » dans Google Cloud peuvent la connecter.")));
      if (!st.connected) {
        fill(main, head, msg, h("div", { class: "card blue", style: "display:grid;gap:14px;justify-items:start" },
          h("h3", {}, "Connectez votre Search Console"),
          h("p", { class: "note", style: "color:#3d3a38" }, "Google vous demandera d'autoriser la lecture seule de vos données Search Console. Choisissez le compte Google qui a accès à vos sites. Vigie SEO ne peut rien modifier."),
          h("button", { class: "btn btn-dark", type: "button", onclick: connectSearchConsole }, "Connecter la Search Console")));
        return;
      }
      var tracked = st.properties.map(function (p) { return p.site; });
      var current = sessionStorage.getItem("pos-site");
      if (tracked.indexOf(current) === -1) current = tracked[0] || "";
      var sel = h("select", { id: "ps-site", "aria-label": "Propriété Search Console" }, h("option", { value: "" }, "Chargement de vos propriétés…"));
      var days = h("select", { id: "ps-days", "aria-label": "Période d'import" }, [30, 90, 180, 480].map(function (d) { return h("option", { value: d, selected: d === 90 }, d + " jours"); }));
      var err = h("div"), syncBtn = h("button", { class: "btn btn-dark", type: "submit" }, "Importer"), syncStatus = h("p", { class: "note", "aria-live": "polite" });
      api("/api/google/sites").then(function (props) {
        fill(sel, props.length ? props.map(function (p) { return h("option", { value: p.site, selected: p.site === current }, p.site); }) : h("option", { value: "" }, "Aucune propriété sur ce compte Google"));
      }).catch(function (e) { fill(sel, h("option", { value: "" }, "Indisponible")); fill(err, errorBox(e)); if (e.data && e.data.reconnect) err.append(h("button", { class: "btn btn-small", type: "button", onclick: connectSearchConsole }, "Reconnecter")); });
      var form = h("form", { class: "card", style: "display:grid;gap:16px", onsubmit: function (e) {
        e.preventDefault(); if (!sel.value) return;
        syncBtn.disabled = true; fill(err); sessionStorage.setItem("pos-site", sel.value);
        var total = 0;
        (function chunk(offset) {
          syncStatus.textContent = "Import en cours… " + fmt(total) + " lignes";
          api("/api/positions/sync", { site: sel.value, days: +days.value, offset: offset }).then(function (r) {
            total += r.imported;
            if (!r.done) return chunk(r.offset);
            syncStatus.textContent = fmt(total) + " lignes importées." + (r.pages === false ? " Analyse des pages…" : "");
            toast("Search Console importée ✓ " + fmt(total) + " lignes", "good");
            return (r.pages === false ? api("/api/positions/sync-pages", { site: sel.value }) : Promise.resolve()).then(function () { viewPositions(); });
          }).catch(function (x) { syncBtn.disabled = false; syncStatus.textContent = ""; fill(err, errorBox(x)); });
        })(0);
      } }, h("div", { style: "display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center" },
          h("h3", {}, "Importer une propriété"),
          h("span", { class: "muted" }, "Connecté : " + (st.email || "compte Google") + " ",
            h("button", { class: "btn btn-small", type: "button", onclick: function () { api("/api/google/disconnect", {}).then(function () { viewPositions(); }); } }, "Déconnecter"))),
        h("div", { class: "form-row" }, h("div", { class: "field" }, h("label", { for: "ps-site" }, "Propriété"), sel),
          h("div", { class: "field", style: "flex:0 0 150px;min-width:130px" }, h("label", { for: "ps-days" }, "Historique"), days), syncBtn),
        h("p", { class: "note" }, "Ensuite, les propriétés importées sont mises à jour automatiquement chaque jour. Votre offre permet d'en suivre " + st.max_properties + "."), syncStatus, err);

      var results = h("div"), insightsBox = h("div");
      function load(site, n) {
        loadInsights(insightsBox, site);
        fill(results, h("div", { class: "empty" }, h("span", { class: "spinner" })));
        api("/api/positions/summary?site=" + encodeURIComponent(site) + "&days=" + n).then(function (d) { renderPositions(results, d); }).catch(function (e) { fill(results, errorBox(e)); });
      }
      var picker = tracked.length ? h("div", { class: "form-row", style: "margin:28px 0 20px" },
        h("div", { class: "field", style: "max-width:420px" }, h("label", { for: "ps-pick" }, "Propriété suivie"),
          h("select", { id: "ps-pick", onchange: function (e) { sessionStorage.setItem("pos-site", e.target.value); load(e.target.value, 28); } },
            tracked.map(function (s) { return h("option", { value: s, selected: s === current }, s); })))) : null;
      fill(main, head, msg, form, picker, insightsBox, results);
      if (current) load(current, 28);
      else fill(results, h("div", { style: "height:28px" }), emptyState("Aucune donnée importée", "Choisissez une propriété ci-dessus et lancez l'import.", null));
    }).catch(fail);
  }
  /* Recommandations calculées à partir de la Search Console (et du dernier audit du site). */
  var PRIO = { high: ["pink", "Priorité haute"], medium: ["blue", "À prévoir"], low: ["", "À surveiller"], good: ["good", "Bon point"] };
  function loadInsights(box, site) {
    fill(box, h("div", { class: "card", style: "margin-bottom:28px" }, h("span", { class: "spinner" }), " Analyse de vos données…"));
    api("/api/positions/insights?site=" + encodeURIComponent(site)).then(function (d) { renderInsights(box, d, site); })
      .catch(function (e) { fill(box, errorBox(e)); });
  }
  function renderInsights(box, d, site) {
    var o = d.overview, n = 0;
    var cards = d.insights.map(function (i) {
      var p = PRIO[i.priority] || PRIO.low;
      if (i.priority !== "good") n++;
      return h("article", { class: "card insight " + p[0] },
        h("div", { class: "insight-head" }, i.priority !== "good" ? h("span", { class: "insight-n", "aria-hidden": "true" }, n) : h("span", { class: "insight-n ok", "aria-hidden": "true" }, "✓"),
          h("div", {}, h("p", { class: "eyebrow", style: "margin:0 0 4px" }, p[1]), h("h3", {}, i.title))),
        h("p", {}, i.text),
        i.chips.length ? h("div", { class: "chips small" }, i.chips.map(function (c) { return h("span", { class: "chip" }, c); })) : null,
        i.table ? h("div", { class: "table-wrap inner" }, h("table", {}, h("thead", {}, h("tr", {}, i.table.columns.map(function (c, k) { return h("th", { class: k && typeof i.table.rows[0][k] === "number" ? "num" : "" }, c); }))),
          h("tbody", {}, i.table.rows.map(function (r) { return h("tr", {}, r.map(function (v, k) { return h("td", { class: typeof v === "number" ? "num" : (k === 0 ? "url" : "") }, typeof v === "number" ? fmt(v, 1) : v); })); })))) : null,
        i.action ? h("p", { class: "insight-action" }, "→ " + i.action) : null);
    });
    var note = d.note ? h("div", { class: "card", style: "display:flex;gap:16px;align-items:center;flex-wrap:wrap;justify-content:space-between" }, h("p", { class: "note" }, d.note),
      !o.has_pages && o.impressions >= 50 ? h("button", { class: "btn btn-small", type: "button", onclick: function (e) {
        e.currentTarget.disabled = true; e.currentTarget.textContent = "Analyse en cours…";
        api("/api/positions/sync-pages", { site: site }).then(function () { loadInsights(box, site); }).catch(function (x) { fill(box, errorBox(x)); });
      } }, "Analyser mes pages") : null) : null;
    fill(box, h("section", { style: "margin-bottom:44px" },
      h("header", { style: "margin-bottom:18px" }, h("h2", {}, "Ce qu'il faut faire"),
        h("p", { class: "muted", style: "margin-top:6px" }, "Sur 90 jours : " + fmt(o.clicks) + " clics, " + fmt(o.impressions) + " impressions" + (o.position ? ", position moyenne " + fmt(o.position, 1) : "") + ". Par ordre de priorité.")),
      note, h("div", { class: "insights" }, cards)));
  }

  function renderPositions(box, d) {
    if (!d.last_date) { fill(box, emptyState("Pas encore de données", "Lancez un import pour cette propriété.", null)); return; }
    var q = d.queries, clicks = q.reduce(function (s, x) { return s + x.clicks; }, 0), imps = q.reduce(function (s, x) { return s + x.impressions; }, 0);
    var wpos = q.reduce(function (s, x) { return s + (x.position || 0) * x.impressions; }, 0) / (imps || 1);
    var state = { filter: "", sort: "impressions", dir: -1, view: "all" }, tbody = h("tbody"), hist = h("div");
    function change(x) { return x.previous_position == null || x.position == null ? null : +(x.previous_position - x.position).toFixed(1); }
    function render() {
      var f = q.filter(function (x) {
        var c = change(x);
        return x.query.toLowerCase().indexOf(state.filter.toLowerCase()) !== -1 &&
          (state.view === "all" || (state.view === "up" && c > 0.5) || (state.view === "down" && c < -0.5) || (state.view === "p2" && x.position > 10 && x.position <= 20));
      });
      f.sort(function (a, b) { var va = state.sort === "change" ? (change(a) || 0) : a[state.sort], vb = state.sort === "change" ? (change(b) || 0) : b[state.sort]; return (typeof va === "string" ? va.localeCompare(vb) : (va || 0) - (vb || 0)) * state.dir; });
      fill(tbody, f.slice(0, 150).map(function (x) {
        var c = change(x);
        return h("tr", { class: "click", tabindex: 0, onclick: function () { showHistory(x.query); }, onkeydown: function (e) { if (e.key === "Enter") showHistory(x.query); } },
          h("td", {}, x.query), h("td", { class: "num" }, fmt(x.clicks)), h("td", { class: "num" }, fmt(x.impressions)), h("td", { class: "num" }, (x.ctr * 100).toFixed(1).replace(".", ",") + " %"),
          h("td", { class: "num" }, h("b", {}, fmt(x.position, 1))), h("td", { class: "num" }, c == null ? h("span", { class: "muted" }, "nouveau") : delta(c) || h("span", { class: "muted" }, "=")));
      }));
      if (!f.length) tbody.append(h("tr", {}, h("td", { colspan: 6, class: "muted" }, "Aucune requête ne correspond.")));
      renderHead();
    }
    var thead = h("thead");
    function th(label, key, num) {
      return h("th", { class: num ? "num" : "", "aria-sort": state.sort === key ? (state.dir > 0 ? "ascending" : "descending") : "none" },
        h("button", { type: "button", class: "th-sort", onclick: function () { state.dir = state.sort === key ? -state.dir : -1; state.sort = key; render(); } }, label + (state.sort === key ? (state.dir > 0 ? " ↑" : " ↓") : "")));
    }
    function renderHead() { fill(thead, h("tr", {}, th("Requête", "query"), th("Clics", "clicks", 1), th("Impr.", "impressions", 1), th("CTR", "ctr", 1), th("Position", "position", 1), th("Évolution", "change", 1))); }
    function showHistory(query) {
      fill(hist, h("div", { class: "card" }, h("span", { class: "spinner" })));
      api("/api/positions/history?site=" + encodeURIComponent(d.site) + "&query=" + encodeURIComponent(query)).then(function (rows) {
        fill(hist, h("div", { class: "card blue" }, h("h3", { style: "margin-bottom:14px" }, "« " + query + " » — position moyenne"),
          lineChart(rows.map(function (r) { return { x: new Date(r.date), y: r.position }; }), { invert: true, height: 200, label: "Position de " + query }),
          h("p", { class: "note", style: "margin-top:8px" }, "Plus la courbe est haute, meilleure est la position (1 = premier résultat).")));
        hist.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    }
    var views = [["all", "Toutes"], ["up", "En progression"], ["down", "En recul"], ["p2", "Page 2 (positions 11-20)"]];
    var pills = h("div", { class: "pills", role: "group", "aria-label": "Filtre" });
    function renderViews() { fill(pills, views.map(function (v) { return h("button", { class: "pill", type: "button", "aria-pressed": String(state.view === v[0]), onclick: function () { state.view = v[0]; renderViews(); render(); } }, v[1]); })); }
    renderViews(); render();
    fill(box,
      h("div", { class: "kpis" }, kpi(fmt(clicks), "clics sur " + d.days + " jours", "blue"), kpi(fmt(imps), "impressions"), kpi((clicks / (imps || 1) * 100).toFixed(1).replace(".", ",") + " %", "taux de clic"), kpi(fmt(wpos, 1), "position moyenne", "pink")),
      h("div", { class: "card", style: "margin-top:20px" }, h("h3", { style: "margin-bottom:12px" }, "Clics par jour"),
        lineChart(d.daily.map(function (r) { return { x: new Date(r.date), y: r.clicks }; }), { color: "#137445", height: 200, label: "Clics par jour" })),
      h("div", { style: "height:20px" }), hist,
      h("section", { class: "section", style: "margin-top:36px" }, h("header", {}, h("h2", {}, "Requêtes"), h("div", { class: "form-row" },
        h("input", { type: "search", placeholder: "Filtrer les requêtes", "aria-label": "Filtrer les requêtes", oninput: function (e) { state.filter = e.target.value; render(); } }),
        h("button", { class: "btn btn-small", type: "button", onclick: function () { csv([["requete", "clics", "impressions", "ctr", "position", "position precedente"]].concat(q.map(function (x) { return [x.query, x.clicks, x.impressions, x.ctr, x.position, x.previous_position]; })), "positions.csv"); } }, "Exporter CSV"))),
        pills, h("div", { style: "height:16px" }),
        h("div", { class: "table-wrap" }, h("table", {}, thead, tbody))));
  }

  /* ---------- vue : mots-clés ---------- */
  var INTENT_LABEL = { info: "Info", commercial: "Commercial", transactionnel: "Transactionnel", navigation: "Navigation", local: "Local" };
  var QUESTION_RE = /^(comment|pourquoi|quel|quelle|quels|quelles|où|ou|quand|combien|qui|que|qu'|est-ce|peut-on|faut-il)\b|\?/i;
  function kdBadge(kd) {
    if (kd == null) return h("span", { class: "muted" }, "–");
    var c = kd < 30 ? "easy" : kd < 50 ? "mid" : kd < 70 ? "hard" : "vhard";
    var label = { easy: "facile", mid: "moyen", hard: "difficile", vhard: "très difficile" }[c];
    return h("span", { class: "kd kd-" + c, title: "Difficulté " + kd + "/100 : " + label }, kd);
  }
  function viewKeywords() {
    var KIND = { suggestion: "Suggestions", question: "Questions", variante: "Variantes", search_console: "Vos requêtes réelles" };
    var out = h("div"), err = h("div"), seed = h("input", { type: "text", id: "kw-seed", placeholder: "ex. logiciel facturation", autocomplete: "off", required: true });
    var country = h("select", { id: "kw-loc", "aria-label": "Pays" }, h("option", { value: 2250 }, "France"));
    var btn = h("button", { class: "btn btn-dark", type: "submit" }, "Rechercher");
    var cfg = { market: false };
    api("/api/keywords/config").then(function (c) {
      cfg = c;
      fill(country, countryKeys(c.locations).map(function (k) { return h("option", { value: k, selected: k === "2250" }, c.locations[k]); }));
      fill(intro, c.market ? "Volumes de recherche mensuels, difficulté, coût par clic et intention, croisés avec vos positions Search Console."
        : "Idées issues de l'autocomplétion de Google et de vos requêtes Search Console (volumes indisponibles).");
    });
    var intro = h("p", {}, "");
    var form = h("form", { class: "form-row", onsubmit: function (e) {
      e.preventDefault(); btn.disabled = true; fill(err);
      fill(out, h("div", { class: "empty" }, h("span", { class: "spinner" }), h("p", { class: "muted" }, "Recherche en cours… quelques secondes.")));
      api("/api/keywords", { seed: seed.value, location: +country.value, site: sessionStorage.getItem("pos-site") || null })
        .then(function (r) { refreshMe(); renderKeywordResults(out, r); })
        .catch(function (x) { fill(out); fill(err, errorBox(x)); }).then(function () { btn.disabled = false; });
    } }, h("div", { class: "field", style: "flex:3;min-width:240px" }, h("label", { for: "kw-seed" }, "Mot-clé de départ"), seed),
      h("div", { class: "field", style: "flex:0 0 170px;min-width:150px" }, h("label", { for: "kw-loc" }, "Pays"), country), btn);

    function renderKeywordResults(box, r) {
      var m = r.market, ideas = r.ideas;
      var parts = [];
      if (m && m.error) parts.push(errorBox(new Error("Données de marché indisponibles : " + m.error)));
      if (m && m.items.length) parts.push(marketTable(m, r.seed));
      var known = {}; (m ? m.items : []).forEach(function (i) { known[i.keyword.toLowerCase()] = 1; });
      var extra = ideas.filter(function (i) { return !known[i.idea.toLowerCase()]; });
      if (extra.length) parts.push(freeIdeas(extra, m && m.items.length));
      if (!parts.length) parts.push(emptyState("Aucun résultat", "Essayez un mot-clé plus court ou plus courant.", null));
      fill(box, parts);
    }

    function marketTable(m, seedText) {
      var hasMatch = m.items.some(function (i) { return i.group === "match"; });
      var st = { group: hasMatch ? "match" : "", intent: "", question: false, kd: 100, minVol: 0, q: "", sort: "volume", dir: -1 };
      var groupPills = h("div", { class: "pills", role: "group", "aria-label": "Type de mots-clés" });
      var tbody = h("tbody"), thead = h("thead"), count = h("p", { class: "muted", "aria-live": "polite" });
      var intents = {}; m.items.forEach(function (i) { if (i.intent) intents[i.intent] = (intents[i.intent] || 0) + 1; });
      var pills = h("div", { class: "pills", role: "group", "aria-label": "Intention" });
      function filtered() {
        var q = st.q.toLowerCase();
        return m.items.filter(function (i) {
          return (!st.group || i.group === st.group) && (!st.intent || i.intent === st.intent) && (!st.question || QUESTION_RE.test(i.keyword)) &&
            (i.difficulty == null || i.difficulty <= st.kd) && (i.volume || 0) >= st.minVol && (!q || i.keyword.toLowerCase().indexOf(q) !== -1);
        }).sort(function (a, b) {
          var va = a[st.sort], vb = b[st.sort];
          if (st.sort === "keyword") return String(va).localeCompare(vb) * st.dir;
          if (va == null) return 1; if (vb == null) return -1;
          return (va - vb) * st.dir;
        });
      }
      function th(label, key, num) {
        return h("th", { class: num ? "num" : "", "aria-sort": st.sort === key ? (st.dir > 0 ? "ascending" : "descending") : "none" },
          h("button", { type: "button", class: "th-sort", onclick: function () { st.dir = st.sort === key ? -st.dir : (key === "keyword" || key === "difficulty" || key === "position" ? 1 : -1); st.sort = key; draw(); } },
            label + (st.sort === key ? (st.dir > 0 ? " ↑" : " ↓") : "")));
      }
      function draw() {
        var nMatch = m.items.filter(function (i) { return i.group === "match"; }).length;
        fill(groupPills, [["match", "Contiennent « " + seedText + " »", nMatch], ["related", "Associés", m.items.length - nMatch], ["", "Tous", m.items.length]].map(function (g) {
          return h("button", { class: "pill", type: "button", "aria-pressed": String(st.group === g[0]), onclick: function () { st.group = g[0]; draw(); } }, g[1], h("span", { class: "n" }, g[2]));
        }));
        fill(pills, h("button", { class: "pill", type: "button", "aria-pressed": String(!st.intent && !st.question), onclick: function () { st.intent = ""; st.question = false; draw(); } }, "Toutes intentions"),
          Object.keys(intents).map(function (k) { return h("button", { class: "pill", type: "button", "aria-pressed": String(st.intent === k), onclick: function () { st.intent = st.intent === k ? "" : k; draw(); } }, INTENT_LABEL[k] || k, h("span", { class: "n" }, intents[k])); }),
          h("button", { class: "pill", type: "button", "aria-pressed": String(st.question), onclick: function () { st.question = !st.question; draw(); } }, "Questions"));
        fill(thead, h("tr", {}, th("Mot-clé", "keyword"), th("Volume", "volume", 1), h("th", {}, "Tendance"), th("Difficulté", "difficulty", 1),
          th("CPC", "cpc", 1), h("th", {}, "Intention"), th("Votre position", "position", 1)));
        var f = filtered();
        var vol = f.reduce(function (s, i) { return s + (i.volume || 0); }, 0);
        count.textContent = fmt(f.length) + " mot" + (f.length > 1 ? "s" : "") + "-clé" + (f.length > 1 ? "s" : "") + " · " + fmt(vol) + " recherches par mois au total" +
          (m.total > m.items.length ? " · " + fmt(m.total) + " existent dans la base, les " + fmt(m.items.length) + " plus recherchés sont affichés" : "");
        fill(tbody, f.slice(0, 300).map(function (i) {
          return h("tr", {}, h("td", {}, i.keyword), h("td", { class: "num" }, h("b", {}, fmt(i.volume))),
            h("td", {}, spark(i.trend || [])), h("td", { class: "num" }, kdBadge(i.difficulty)),
            h("td", { class: "num" }, i.cpc != null ? fmt(i.cpc, 2) + " €" : "–"),
            h("td", {}, i.intent ? h("span", { class: "intent intent-" + i.intent }, INTENT_LABEL[i.intent]) : "–"),
            h("td", { class: "num" }, i.position != null ? h("b", {}, fmt(i.position, 1)) : h("span", { class: "muted" }, "–")));
        }));
        if (!f.length) tbody.append(h("tr", {}, h("td", { colspan: 7, class: "muted" }, "Aucun mot-clé ne correspond aux filtres.")));
      }
      var kdSel = h("select", { "aria-label": "Difficulté maximum", onchange: function (e) { st.kd = +e.target.value; draw(); } },
        [[100, "Toutes difficultés"], [29, "Facile (< 30)"], [49, "Jusqu'à moyen (< 50)"], [69, "Jusqu'à difficile (< 70)"]].map(function (o) { return h("option", { value: o[0] }, o[1]); }));
      var volIn = h("input", { type: "number", min: 0, step: 10, placeholder: "Volume min.", "aria-label": "Volume minimum", style: "max-width:150px", oninput: function (e) { st.minVol = +e.target.value || 0; draw(); } });
      var qIn = h("input", { type: "search", placeholder: "Contient…", "aria-label": "Filtrer les mots-clés", oninput: function (e) { st.q = e.target.value; draw(); } });
      draw();
      var top = m.items.filter(function (i) { return i.volume; });
      var avgKd = top.filter(function (i) { return i.difficulty != null; });
      return h("section", {},
        h("div", { class: "kpis", style: "margin:28px 0 20px" },
          kpi(fmt(m.total), "mots-clés trouvés", "blue"),
          kpi(fmt(top.length ? top[0].volume : 0), "recherches/mois pour « " + (top.length ? top[0].keyword : seedText) + " »"),
          kpi(avgKd.length ? Math.round(avgKd.reduce(function (s, i) { return s + i.difficulty; }, 0) / avgKd.length) : "–", "difficulté moyenne /100", "pink")),
        groupPills, h("div", { style: "height:12px" }),
        h("header", { style: "display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:end;margin-bottom:14px" }, pills,
          h("div", { class: "form-row" }, qIn, kdSel, volIn,
            h("button", { class: "btn btn-small", type: "button", onclick: function () {
              csv([["mot-cle", "volume", "difficulte", "cpc", "intention", "votre position"]].concat(filtered().map(function (i) { return [i.keyword, i.volume, i.difficulty, i.cpc, INTENT_LABEL[i.intent] || "", i.position]; })), "mots-cles-" + seedText.replace(/\s+/g, "-") + ".csv");
            } }, "Exporter CSV"))),
        count, h("div", { class: "table-wrap", style: "margin-top:10px" }, h("table", {}, thead, tbody)),
        m.cached ? h("p", { class: "note", style: "margin-top:10px" }, "Résultats issus du cache (moins de 30 jours).") : null);
    }

    function freeIdeas(ideas, hasMarket) {
      var stI = { kind: "" }, chips = h("div", { class: "chips" }), pills = h("div", { class: "pills", role: "group", "aria-label": "Type d'idée" });
      var counts = {}; ideas.forEach(function (i) { counts[i.kind] = (counts[i.kind] || 0) + 1; });
      function draw() {
        fill(pills, h("button", { class: "pill", type: "button", "aria-pressed": String(!stI.kind), onclick: function () { stI.kind = ""; draw(); } }, "Toutes", h("span", { class: "n" }, ideas.length)),
          Object.keys(counts).map(function (k) { return h("button", { class: "pill", type: "button", "aria-pressed": String(stI.kind === k), onclick: function () { stI.kind = k; draw(); } }, KIND[k] || k, h("span", { class: "n" }, counts[k])); }));
        fill(chips, ideas.filter(function (i) { return !stI.kind || i.kind === stI.kind; }).map(function (i) {
          return h("button", { class: "chip " + i.kind, type: "button", title: "Copier", onclick: function (e) { if (navigator.clipboard) navigator.clipboard.writeText(i.idea); e.currentTarget.style.outline = "2px solid var(--green)"; } },
            i.idea, i.position != null ? h("span", { class: "muted" }, " · pos. " + fmt(i.position, 1)) : null);
        }));
      }
      draw();
      return h("section", { class: "section" }, h("h2", { style: "margin-bottom:6px" }, hasMarket ? "Autres idées" : ideas.length + " idées"),
        h("p", { class: "note", style: "margin-bottom:16px" }, hasMarket ? "Recherches proposées par l'autocomplétion de Google, absentes de la base de volumes (souvent très précises ou récentes)." : "Cliquez sur une idée pour la copier."),
        pills, h("div", { style: "height:16px" }), chips);
    }

    fill(main, h("section", { class: "hero", style: "margin-bottom:28px" }, h("p", { class: "eyebrow" }, "Recherche de mots-clés"), h("h1", {}, "Ce que cherchent vraiment les internautes."), intro),
      h("div", { class: "card" }, form, h("p", { class: "note", style: "margin-top:14px" }, ME ? fmt(ME.usage.keywords) + " / " + fmt(ME.limits.keywords_month) + " recherches ce mois-ci." : ""), err), out);
  }

  /* ---------- vue : concurrents ---------- */
  var METRICS = [
    ["pages", "Pages analysées", 0], ["sitemap_urls", "URLs du sitemap", 0], ["avg_title_length", "Longueur moyenne du title", 0],
    ["pct_with_description", "Pages avec meta description (%)", 1], ["pct_single_h1", "Pages avec un seul H1 (%)", 1],
    ["avg_word_count", "Mots par page (moyenne)", 1], ["pct_with_schema", "Pages avec données structurées (%)", 1],
    ["pct_with_hreflang", "Pages avec hreflang (%)", 1], ["avg_ttfb_ms", "Temps de réponse serveur (ms)", -1]];
  function viewCompetitors(key) {
    loading();
    if (!key) {
      api("/api/sites").then(function (sites) {
        fill(main, h("section", { class: "hero" }, h("p", { class: "eyebrow" }, "Concurrents"), h("h1", {}, "Où vous situez-vous face aux autres ?"), h("p", {}, "Choisissez un projet pour le comparer à des sites concurrents.")),
          sites.length ? h("div", { class: "grid" }, sites.map(function (s, i) { return h("a", { class: "card " + TINTS[i % 6], href: "#/concurrents/" + encodeURIComponent(s.key) }, h("h3", {}, s.key), h("p", { class: "muted" }, "Comparer →")); }))
            : emptyState("Lancez d'abord un audit", "La comparaison part de l'audit de votre propre site.", h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(); } }, "Lancer un audit")));
      }).catch(fail);
      return;
    }
    api("/api/competitors/" + encodeURIComponent(key)).then(function (d) {
      var err = h("div"), url = h("input", { type: "text", id: "cp-url", placeholder: "www.concurrent.fr", required: true });
      var form = h("form", { class: "form-row", onsubmit: function (e) {
        e.preventDefault(); fill(err);
        api("/api/competitors", { site: key, url: url.value }).then(function (r) {
          followAudit(r.id, "Analyse de " + url.value.replace(/^https?:\/\//, ""), function () { refreshMe(); viewCompetitors(key); });
        }).catch(function (x) { fill(err, errorBox(x)); });
      } }, h("div", { class: "field" }, h("label", { for: "cp-url" }, "Ajouter un concurrent"), url), h("button", { class: "btn btn-dark", type: "submit" }, "Analyser"));
      var ready = d.competitors.filter(function (c) { return c.profile; });
      var pending = d.competitors.filter(function (c) { return !c.profile; });
      var table = null;
      if (d.own && ready.length) {
        var all = [{ name: key + " (vous)", p: d.own }].concat(ready.map(function (c) { return { name: c.site, p: c.profile }; }));
        table = h("div", { class: "table-wrap cmp" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Indicateur"), all.map(function (c) { return h("th", { class: "num" }, c.name); }))),
          h("tbody", {}, METRICS.map(function (m) {
            var vals = all.map(function (c) { return c.p[m[0]]; }), best = null, ok = vals.filter(function (v) { return v != null; });
            if (m[2] && ok.length > 1 && Math.max.apply(null, ok) !== Math.min.apply(null, ok)) best = m[2] > 0 ? Math.max.apply(null, ok) : Math.min.apply(null, ok);
            return h("tr", {}, h("td", {}, m[1]), vals.map(function (v) { return h("td", { class: "num" + (best != null && v === best ? " best" : "") }, fmt(v, 1)); }));
          }).concat(h("tr", {}, h("td", {}, "Types de données structurées"), all.map(function (c) { return h("td", { class: "num" }, Object.keys(c.p.schema_types || {}).slice(0, 5).join(", ") || "–"); }))))));
      }
      fill(main, h("a", { class: "crumb", href: "#/concurrents" }, "← Concurrents"),
        h("section", { class: "hero", style: "margin:16px 0 28px" }, h("p", { class: "eyebrow" }, "Comparaison"), h("h1", { style: "font-size:clamp(2rem,4vw,3rem)" }, key)),
        h("div", { class: "card" }, form, h("p", { class: "note", style: "margin-top:14px" }, "Exploration légère (" + (ME ? ME.limits.competitor_pages : 20) + " pages au plus, robots.txt respecté). Seules les pages publiques sont lues."), err),
        pending.length ? h("div", { style: "margin-top:16px" }, pending.map(function (c) {
          return h("p", { class: "note" }, c.site + " : " + (c.status === "failed" ? "échec (" + (c.error || "") + ")" : "analyse en cours (" + fmt(c.progress.done) + " pages)"), " ",
            c.status !== "failed" ? h("button", { class: "btn btn-small", type: "button", onclick: function () { followAudit(c.id, c.site, function () { viewCompetitors(key); }); } }, "Suivre") : null);
        })) : null,
        h("div", { style: "height:24px" }),
        !d.own ? emptyState("Aucun audit pour ce projet", "Lancez un audit de votre site pour pouvoir le comparer.", h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(key); } }, "Lancer un audit"))
          : table || emptyState("Aucun concurrent analysé", "Ajoutez l'adresse d'un concurrent ci-dessus.", null),
        table ? h("p", { class: "note", style: "margin-top:14px" }, "Les cases vertes indiquent la meilleure valeur lorsqu'une comparaison a un sens.") : null);
    }).catch(fail);
  }

  /* ---------- vue : plan d'action ---------- */
  var PRIO_LABEL = { high: "Priorité haute", medium: "À prévoir", low: "À surveiller" };
  function viewPlan(key) {
    loading();
    if (!key) {
      Promise.all([api("/api/sites"), api("/api/plan")]).then(function (r) {
        var sites = r[0], open = r[1];
        if (sites.length === 1) { location.hash = "#/plan/" + encodeURIComponent(sites[0].key); return; }
        fill(main, h("section", { class: "hero" }, h("p", { class: "eyebrow" }, "Plan d'action"), h("h1", {}, "Que faire cette semaine ?"),
          h("p", {}, "Les recommandations de vos audits et de la Search Console, transformées en tâches. Cochez ce que vous avez fait : Vigie SEO mesure l'effet.")),
          sites.length ? h("div", { class: "grid" }, sites.map(function (s, i) {
            return h("a", { class: "card " + TINTS[i % 6], href: "#/plan/" + encodeURIComponent(s.key) }, h("h3", {}, s.key),
              h("p", { class: "muted" }, open[s.key] ? open[s.key] + " tâche" + (open[s.key] > 1 ? "s" : "") + " à faire" : "Voir le plan →"));
          })) : emptyState("Lancez d'abord un audit", "Le plan d'action part de l'audit de votre site et de vos données Search Console.",
            h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(); } }, "Lancer un audit")));
      }).catch(fail);
      return;
    }
    api("/api/plan/" + encodeURIComponent(key)).then(function (d) { renderPlan(key, d); }).catch(fail);
  }
  function renderPlan(key, d) {
    var state = { status: "todo", source: "" }, list = h("div", { class: "insights" }), pills = h("div", { class: "pills", role: "group", "aria-label": "Statut" });
    var srcPills = h("div", { class: "pills", role: "group", "aria-label": "Origine" });
    var todo = d.tasks.filter(function (t) { return t.status === "todo"; }).length, done = d.tasks.filter(function (t) { return t.status === "done"; }).length;
    function setStatus(t, status) {
      api("/api/plan/tasks/" + t.id, { status: status }).then(function () {
        toast(status === "done" ? "Bravo, tâche faite ✓ L'effet sera mesuré." : status === "ignored" ? "Tâche ignorée" : "Tâche rouverte", status === "done" ? "good" : "");
        viewPlan(key);
      }).catch(function (e) { toast(e.message, "bad"); });
    }
    function impactView(t) {
      var i = t.impact;
      if (t.status !== "done") return null;
      if (t.source === "audit") {
        if (!i) return null;
        return h("p", { class: "impact good" }, i.count_now === 0 ? "✓ Corrigé : plus aucune page concernée au dernier audit" + (i.resolved_by_audit ? " (fermée automatiquement)" : "")
          : "Encore " + i.count_now + " page(s) concernée(s) au dernier audit (" + i.count_before + " au départ)");
      }
      if (!i) {
        var days = Math.floor((Date.now() - new Date(t.done_at)) / 864e5);
        return h("p", { class: "impact" }, "Mesure de l'effet en cours : premiers chiffres 7 jours après (J+" + days + "). Google publie ses données avec 2 jours de décalage.");
      }
      var parts = [];
      if (i.impressions_pct != null) parts.push((i.impressions_pct > 0 ? "+" : "") + i.impressions_pct + " % d'impressions");
      if (i.clicks_pct != null) parts.push((i.clicks_pct > 0 ? "+" : "") + i.clicks_pct + " % de clics");
      if (i.position_before && i.position_after) parts.push("position " + fmt(i.position_before, 1) + " → " + fmt(i.position_after, 1));
      var good = (i.impressions_pct || 0) > 0 || (i.position_before && i.position_after && i.position_after < i.position_before);
      return h("p", { class: "impact" + (good ? " good" : "") }, (parts.join(", ") || "Pas encore de données sur ces requêtes") +
        " — " + (i.final ? "sur 4 semaines" : "mesure provisoire, J+" + i.days_after));
    }
    function card(t) {
      var pages = t.pages || [];
      return h("article", { class: "card insight task " + (t.status === "todo" ? (t.priority === "high" ? "pink" : t.priority === "medium" ? "blue" : "") : "") },
        h("div", { style: "display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:start" },
          h("div", {}, h("p", { class: "eyebrow", style: "margin:0 0 6px" }, (t.source === "audit" ? "Audit" : "Search Console") + " · " + (PRIO_LABEL[t.priority] || "")),
            h("h3", {}, t.title)),
          h("div", { class: "form-row" },
            t.status === "todo" ? [h("button", { class: "btn btn-dark btn-small", type: "button", onclick: function () { setStatus(t, "done"); } }, "C'est fait"),
              h("button", { class: "btn btn-small", type: "button", onclick: function () { setStatus(t, "ignored"); } }, "Ignorer")]
              : h("button", { class: "btn btn-small", type: "button", onclick: function () { setStatus(t, "todo"); } }, "Rouvrir"))),
        t.detail ? h("p", {}, t.detail) : null,
        t.action ? h("p", { class: "insight-action" }, "→ " + t.action) : null,
        pages.length ? h("details", { class: "pages-list" }, h("summary", {}, pages.length + " page" + (pages.length > 1 ? "s" : "") + " concernée" + (pages.length > 1 ? "s" : "")),
          h("ul", {}, pages.slice(0, 30).map(function (u) { return h("li", {}, h("a", { class: "url", href: u, target: "_blank", rel: "noopener noreferrer" }, u)); }))) : null,
        impactView(t));
    }
    function draw() {
      var counts = { todo: 0, done: 0, ignored: 0 };
      d.tasks.forEach(function (t) { if (!state.source || t.source === state.source) counts[t.status]++; });
      fill(pills, [["todo", "À faire"], ["done", "Faites"], ["ignored", "Ignorées"]].map(function (v) {
        return h("button", { class: "pill", type: "button", "aria-pressed": String(state.status === v[0]), onclick: function () { state.status = v[0]; draw(); } }, v[1], h("span", { class: "n" }, counts[v[0]]));
      }));
      fill(srcPills, [["", "Tout"], ["audit", "Audit"], ["gsc", "Search Console"]].map(function (v) {
        return h("button", { class: "pill", type: "button", "aria-pressed": String(state.source === v[0]), onclick: function () { state.source = v[0]; draw(); } }, v[1]);
      }));
      var f = d.tasks.filter(function (t) { return t.status === state.status && (!state.source || t.source === state.source); });
      fill(list, f.length ? f.map(card) : h("div", { class: "card empty" }, h("h3", {}, state.status === "todo" ? "Tout est fait 🎉" : "Rien ici pour l'instant"),
        h("p", { class: "muted" }, state.status === "todo" ? "Relancez un audit ou importez la Search Console pour de nouvelles recommandations." : "")));
    }
    draw();
    var pct = todo + done ? Math.round(done / (todo + done) * 100) : 0;
    fill(main, h("a", { class: "crumb", href: "#/plan" }, "← Plan d'action"),
      h("div", { class: "page-head", style: "margin-top:16px" },
        h("div", { class: "grow" }, h("p", { class: "eyebrow" }, "Plan d'action"), h("h1", { style: "font-size:clamp(2rem,4vw,3rem)" }, key),
          h("p", { class: "muted" }, todo + " à faire · " + done + " faite" + (done > 1 ? "s" : "") +
            (d.audit_date ? " · audit du " + fdate(d.audit_date, true) : " · aucun audit") + (d.properties.length ? " · Search Console : " + d.properties.join(", ") : " · Search Console non importée")),
          h("div", { style: "max-width:420px" }, progressBar(done, todo + done))),
        h("div", { class: "form-row" }, h("button", { class: "btn", type: "button", onclick: function () { newAuditModal(key); } }, "Relancer un audit"),
          d.properties.length ? null : h("a", { class: "btn", href: "#/positions" }, "Importer la Search Console"))),
      h("div", { style: "display:flex;gap:16px;flex-wrap:wrap;justify-content:space-between;margin-bottom:20px" }, pills, srcPills), list,
      h("p", { class: "note", style: "margin-top:20px" }, "Progression : " + pct + " %. Les problèmes d'audit corrigés se ferment tout seuls au prochain audit ; pour les tâches Search Console, l'effet est mesuré sur les requêtes concernées, 4 semaines avant et après."));
  }

  /* ---------- vue : suivi de positions ---------- */
  var SERIES_COLORS = ["var(--ink)", "#ff6235", "#2bb673", "#6fb1dc", "#b06ad9", "#f2a93b", "#cf3434", "#7a6f6a"];
  // Plusieurs courbes de positions (1 en haut) ; les absences du top coupent la ligne.
  function multiChart(series, o) {
    o = o || {};
    var W = 760, H = o.height || 260, P = { l: 36, r: 16, t: 14, b: 28 };
    var all = []; series.forEach(function (sr) { sr.points.forEach(function (p) { if (p.y != null) all.push(p); }); });
    if (!all.length) return h("p", { class: "muted" }, "Pas encore de position relevée dans le top suivi.");
    var xs = []; series.forEach(function (sr) { sr.points.forEach(function (p) { xs.push(+p.x); }); });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), hi = Math.max(10, Math.max.apply(null, all.map(function (p) { return p.y; })));
    var X = function (v) { return P.l + (x1 === x0 ? (W - P.l - P.r) / 2 : (v - x0) / (x1 - x0) * (W - P.l - P.r)); };
    var Y = function (v) { return P.t + (v - 1) / (hi - 1) * (H - P.t - P.b); };
    var s = svg("svg", { class: "chart", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.label || "Évolution des positions" });
    [1, Math.round(hi / 3), Math.round(2 * hi / 3), hi].forEach(function (v) {
      s.append(svg("line", { class: "grid-line", x1: P.l, x2: W - P.r, y1: Y(v), y2: Y(v) }), svg("text", { x: P.l - 8, y: Y(v) + 4, "text-anchor": "end" }, v));
    });
    series.forEach(function (sr, i) {
      var color = SERIES_COLORS[i % SERIES_COLORS.length], d = "", pen = false;
      sr.points.forEach(function (p) {
        if (p.y == null) { pen = false; return; }
        d += (pen ? "L" : "M") + X(+p.x).toFixed(1) + " " + Y(p.y).toFixed(1) + " "; pen = true;
      });
      if (d) s.append(svg("path", { d: d, fill: "none", style: "stroke:" + color, "stroke-width": i ? 2.5 : 3.5, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      sr.points.forEach(function (p) { if (p.y != null) s.append(svg("circle", { cx: X(+p.x), cy: Y(p.y), r: i ? 3 : 4, style: "fill:" + color }, svg("title", {}, sr.name + " : " + p.y + "e le " + fdate(p.x, true)))); });
    });
    s.append(svg("text", { x: X(x0), y: H - 6 }, fdate(x0)));
    if (x1 !== x0) s.append(svg("text", { x: X(x1), y: H - 6, "text-anchor": "end" }, fdate(x1)));
    return h("div", {}, s, h("div", { class: "legend-row" }, series.map(function (sr, i) {
      var dot = h("span", { class: "dot" }); dot.style.background = SERIES_COLORS[i % SERIES_COLORS.length];
      return h("span", {}, dot, sr.name);
    })));
  }
  function posChange(now, before) {
    if (now == null && before == null) return h("span", { class: "muted" }, "–");
    if (before == null) return now != null ? h("span", { class: "delta up" }, "nouveau") : h("span", { class: "muted" }, "–");
    if (now == null) return h("span", { class: "delta down" }, "sorti");
    return delta(before - now) || h("span", { class: "muted" }, "=");
  }
  function posCell(p) { return p == null ? h("span", { class: "muted" }, "> 30") : h("b", {}, p); }

  function viewTracking(site) {
    loading();
    api("/api/tracking").then(function (cfg) {
      if (!site) {
        var domains = Object.keys(cfg.sites).concat(cfg.projects.filter(function (p) { return !(p in cfg.sites); }));
        if (Object.keys(cfg.sites).length === 1) { location.hash = "#/suivi/" + encodeURIComponent(Object.keys(cfg.sites)[0]); return; }
        var other = h("input", { type: "text", id: "tr-domain", placeholder: "exemple.fr", required: true });
        fill(main, h("section", { class: "hero" }, h("p", { class: "eyebrow" }, "Suivi de positions"), h("h1", {}, "Votre place sur Google, chaque jour."),
          h("p", {}, "Choisissez vos mots-clés : Vigie SEO relève chaque jour votre position et celle de vos concurrents, même sur des recherches où vous n'apparaissez pas encore.")),
          !cfg.enabled ? h("div", { class: "card" }, h("p", {}, "Le suivi de positions nécessite le service de données (DataForSEO), qui n'est pas configuré.")) : null,
          domains.length ? h("div", { class: "grid" }, domains.map(function (d, i) {
            return h("a", { class: "card " + TINTS[i % 6], href: "#/suivi/" + encodeURIComponent(d) }, h("h3", {}, d),
              h("p", { class: "muted" }, cfg.sites[d] ? cfg.sites[d] + " mot" + (cfg.sites[d] > 1 ? "s" : "") + "-clé" + (cfg.sites[d] > 1 ? "s" : "") + " suivi" + (cfg.sites[d] > 1 ? "s" : "") : "Commencer le suivi →"));
          })) : null,
          h("form", { class: "card form-row", style: "margin-top:20px", onsubmit: function (e) { e.preventDefault(); location.hash = "#/suivi/" + encodeURIComponent(other.value.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "")); } },
            h("div", { class: "field" }, h("label", { for: "tr-domain" }, "Suivre un domaine"), other), h("button", { class: "btn btn-dark", type: "submit" }, "Ouvrir")));
        return;
      }
      return api("/api/tracking/" + encodeURIComponent(site)).then(function (d) { renderTracking(d, cfg); });
    }).catch(fail);
  }

  function renderTracking(d, cfg) {
    var site = d.site, k = d.kpis, comps = d.competitors.slice(1).map(function (c) { return c.domain; });
    var err = h("div");
    var kwIn = h("textarea", { id: "tr-kw", rows: 4, placeholder: "Un mot-clé par ligne\nlogiciel de facturation\nlogiciel facture charpentier", style: "width:100%;border:2px solid var(--ink);border-radius:var(--r-md);padding:14px 18px;font:1rem var(--body)" });
    var loc = h("select", { id: "tr-loc", "aria-label": "Pays" }, countryKeys(cfg.locations).map(function (c) { return h("option", { value: c }, cfg.locations[c]); }));
    var dev = h("select", { id: "tr-dev", "aria-label": "Appareil" }, h("option", { value: "desktop" }, "Ordinateur"), h("option", { value: "mobile" }, "Mobile"));
    var addBtn = h("button", { class: "btn btn-dark", type: "submit" }, "Suivre");
    var addForm = h("form", { class: "card", style: "display:grid;gap:14px", onsubmit: function (e) {
      e.preventDefault(); addBtn.disabled = true; fill(err);
      api("/api/tracking/" + encodeURIComponent(site) + "/keywords", { keywords: kwIn.value, location: +loc.value, device: dev.value })
        .then(function (r) { toast(r.added + " mot" + (r.added > 1 ? "s" : "") + "-clé" + (r.added > 1 ? "s" : "") + " suivi" + (r.added > 1 ? "s" : "") + " ✓ Premier relevé sous une heure.", "good"); viewTracking(site); })
        .catch(function (x) { addBtn.disabled = false; fill(err, errorBox(x)); });
    } }, h("h3", {}, "Ajouter des mots-clés"), h("div", { class: "field" }, h("label", { for: "tr-kw" }, "Mots-clés"), kwIn),
      h("div", { class: "form-row" }, h("div", { class: "field", style: "flex:0 0 170px" }, h("label", { for: "tr-loc" }, "Pays"), loc),
        h("div", { class: "field", style: "flex:0 0 170px" }, h("label", { for: "tr-dev" }, "Appareil"), dev), addBtn),
      h("p", { class: "note" }, d.used + " / " + d.limit + " mots-clés suivis avec votre offre. Premier relevé sous une heure, puis chaque jour."), err);

    var compIn = h("input", { type: "text", placeholder: "concurrent.fr", "aria-label": "Ajouter un concurrent" }), compErr = h("div");
    function addComp(domain) {
      api("/api/tracking/" + encodeURIComponent(site) + "/competitors", { domain: domain }).then(function () { toast("Concurrent ajouté ✓", "good"); viewTracking(site); }).catch(function (x) { fill(compErr, errorBox(x)); });
    }
    var compTable = h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Domaine"), h("th", { class: "num" }, "Visibilité"), h("th", { class: "num" }, "7 jours"), h("th", { class: "num" }, "Pos. moyenne"), h("th", { class: "num" }, "Top 10"), h("th", {}, ""))),
      h("tbody", {}, d.competitors.map(function (c) {
        var dv = c.visibility != null && c.visibility_week != null ? +(c.visibility - c.visibility_week).toFixed(1) : null;
        return h("tr", { class: c.you ? "me" : "" }, h("td", {}, h("b", {}, c.domain), c.you ? " (vous)" : ""),
          h("td", { class: "num" }, c.visibility != null ? fmt(c.visibility, 1) + " %" : "–"), h("td", { class: "num" }, dv != null ? delta(dv) || "=" : "–"),
          h("td", { class: "num" }, fmt(c.avg_position, 1)), h("td", { class: "num" }, c.top10),
          h("td", {}, c.you ? null : h("button", { class: "btn btn-small", type: "button", "aria-label": "Retirer " + c.domain, onclick: function () {
            api("/api/tracking/" + encodeURIComponent(site) + "/competitors/" + encodeURIComponent(c.domain), undefined, "DELETE").then(function () { viewTracking(site); });
          } }, "Retirer")));
      }))));

    var st = { sort: "volume", dir: -1, q: "" }, tbody = h("tbody"), thead = h("thead");
    function th(label, key, num) {
      return h("th", { class: num ? "num" : "" }, key ? h("button", { type: "button", class: "th-sort", onclick: function () { st.dir = st.sort === key ? -st.dir : (key === "position" ? 1 : -1); st.sort = key; draw(); } },
        label + (st.sort === key ? (st.dir > 0 ? " ↑" : " ↓") : "")) : label);
    }
    function draw() {
      fill(thead, h("tr", {}, th("Mot-clé", "keyword"), th("Position", "position", 1), th("1 j", null, 1), th("7 j", null, 1), th("30 jours"), th("Volume", "volume", 1),
        comps.slice(0, 3).map(function (c) { return th(c, null, 1); }), th("Page", null), th("")));
      var rows = d.keywords.filter(function (r) { return !st.q || r.keyword.indexOf(st.q.toLowerCase()) !== -1; }).sort(function (a, b) {
        var va = a[st.sort], vb = b[st.sort];
        if (st.sort === "keyword") return va.localeCompare(vb) * st.dir;
        if (va == null) return 1; if (vb == null) return -1; return (va - vb) * st.dir;
      });
      fill(tbody, rows.map(function (r) {
        var hist = r.history.map(function (x) { return x[1] == null ? 31 : x[1]; });
        return h("tr", { class: "click", tabindex: 0, onclick: function (e) { if (e.target.tagName !== "BUTTON" && e.target.tagName !== "A") keywordDetail(r); }, onkeydown: function (e) { if (e.key === "Enter") keywordDetail(r); } },
          h("td", {}, r.keyword, h("span", { class: "detail" }, (cfg.locations[r.location] || r.location) + " · " + (r.device === "mobile" ? "mobile" : "ordinateur"))),
          h("td", { class: "num" }, r.pending ? h("span", { class: "muted" }, "en attente") : posCell(r.position)),
          h("td", { class: "num" }, r.pending ? "" : posChange(r.position, r.prev_day)), h("td", { class: "num" }, r.pending ? "" : posChange(r.position, r.prev_week)),
          h("td", {}, hist.length > 1 ? spark(hist.map(function (p) { return -p; })) : null),
          h("td", { class: "num" }, fmt(r.volume)),
          comps.slice(0, 3).map(function (c) { return h("td", { class: "num" }, r.pending ? "" : posCell(r.competitors[c])); }),
          h("td", { class: "url" }, r.url ? h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, r.url.replace(/^https?:\/\/[^/]+/, "") || "/") : ""),
          h("td", {}, h("button", { class: "btn btn-small", type: "button", "aria-label": "Ne plus suivre " + r.keyword, onclick: function () {
            if (confirm("Ne plus suivre « " + r.keyword + " » ?")) api("/api/tracking/keywords/" + r.id, undefined, "DELETE").then(function () { viewTracking(site); });
          } }, "Retirer")));
      }));
      if (!rows.length) tbody.append(h("tr", {}, h("td", { colspan: 12, class: "muted" }, "Aucun mot-clé suivi pour l'instant.")));
    }
    draw();

    function keywordDetail(r) {
      modal("Historique", [h("p", { class: "eyebrow" }, "Mot-clé suivi"), h("h2", {}, r.keyword), h("span", { class: "spinner" })]);
      api("/api/tracking/keywords/" + r.id).then(function (k) {
        var series = Object.keys(k.series).map(function (dom) {
          return { name: dom === k.site ? dom + " (vous)" : dom, points: k.series[dom].map(function (p) { return { x: new Date(p[0]), y: p[1] }; }) };
        });
        overlay.querySelector(".modal").style.width = "min(860px, 100%)";
        fill(overlay.querySelector(".modal"), h("p", { class: "eyebrow" }, "Mot-clé suivi · " + (cfg.locations[k.location] || "") + " · " + (k.device === "mobile" ? "mobile" : "ordinateur") + (k.volume ? " · " + fmt(k.volume) + " recherches/mois" : "")),
          h("h2", {}, k.keyword), multiChart(series, { label: "Positions sur " + k.keyword }),
          k.top ? h("div", {}, h("h3", { style: "margin:8px 0 10px" }, "Top 10 au dernier relevé"), h("ol", { class: "top10" }, k.top.map(function (t) {
            return h("li", { class: t[1] === k.site ? "me" : "" }, h("b", {}, t[1]), h("a", { class: "detail url", href: t[2], target: "_blank", rel: "noopener noreferrer" }, t[3] || t[2]));
          }))) : null,
          h("div", { class: "form-row" }, h("button", { class: "btn", type: "button", onclick: closeOverlay }, "Fermer")));
      }).catch(function (e) { fill(overlay.querySelector(".modal"), errorBox(e)); });
    }

    var vDelta = k.visibility != null && k.visibility_week != null ? +(k.visibility - k.visibility_week).toFixed(1) : null;
    fill(main, h("a", { class: "crumb", href: "#/suivi" }, "← Suivi de positions"),
      h("div", { class: "page-head", style: "margin-top:16px" }, h("div", { class: "grow" }, h("p", { class: "eyebrow" }, "Suivi de positions"),
        h("h1", { style: "font-size:clamp(2rem,4vw,3rem)" }, site),
        h("p", { class: "muted" }, d.keywords.length + " mot" + (d.keywords.length > 1 ? "s" : "") + "-clé" + (d.keywords.length > 1 ? "s" : "") + " suivi" + (d.keywords.length > 1 ? "s" : "") + (k.pending ? " · " + k.pending + " en attente du premier relevé" : "")))),
      d.keywords.length ? h("div", { class: "kpis" },
        kpi(k.visibility != null ? fmt(k.visibility, 1) + " %" : "–", "visibilité" + (vDelta != null ? " (" + (vDelta > 0 ? "+" : "") + fmt(vDelta, 1) + " sur 7 jours)" : ""), "blue"),
        kpi(fmt(k.avg_position, 1), "position moyenne"), kpi(k.top3 + " / " + k.top10, "dans le top 3 / top 10", "pink"), kpi(k.out, "absents du top 30")) : null,
      d.keywords.length ? h("section", { class: "section" }, h("header", {}, h("h2", {}, "Mots-clés"),
        h("input", { type: "search", placeholder: "Filtrer", "aria-label": "Filtrer les mots-clés", oninput: function (e) { st.q = e.target.value; draw(); } })),
        h("div", { class: "table-wrap" }, h("table", {}, thead, tbody))) : null,
      h("section", { class: "section" }, h("h2", { style: "margin-bottom:16px" }, "Concurrents"), compTable,
        h("form", { class: "form-row", style: "margin-top:14px", onsubmit: function (e) { e.preventDefault(); addComp(compIn.value); } },
          h("div", { class: "field" }, compIn), h("button", { class: "btn btn-small", type: "submit" }, "Ajouter un concurrent")),
        d.suggested.length ? h("div", { style: "margin-top:12px" }, h("p", { class: "note", style: "margin-bottom:8px" }, "Souvent dans le top 10 de vos mots-clés :"),
          h("div", { class: "chips" }, d.suggested.map(function (s) { return h("button", { class: "chip", type: "button", onclick: function () { addComp(s); } }, "+ " + s); }))) : null, compErr),
      h("section", { class: "section" }, addForm));
  }

  /* ---------- vue : analyse de domaine ---------- */
  var COUNTRY = { 2250: "France", 2056: "Belgique", 2756: "Suisse", 2124: "Canada" };
  function keywordTable(rows, cols, filename) {
    var st = { sort: cols.indexOf("traffic") !== -1 ? "traffic" : "volume", dir: -1, q: "" }, tbody = h("tbody"), thead = h("thead");
    var LABEL = { keyword: "Mot-clé", position: "Position", their_position: "Sa position", volume: "Volume", difficulty: "Difficulté", cpc: "CPC", traffic: "Trafic", intent: "Intention", url: "Page" };
    function th(c) {
      var sortable = c !== "url" && c !== "intent", num = c !== "keyword" && c !== "url" && c !== "intent";
      return h("th", { class: num ? "num" : "" }, sortable ? h("button", { type: "button", class: "th-sort", onclick: function () {
        st.dir = st.sort === c ? -st.dir : (c === "keyword" || c === "position" || c === "difficulty" ? 1 : -1); st.sort = c; draw(); } },
        LABEL[c] + (st.sort === c ? (st.dir > 0 ? " ↑" : " ↓") : "")) : LABEL[c]);
    }
    function cell(r, c) {
      if (c === "keyword") return h("td", {}, r.keyword);
      if (c === "position") return h("td", { class: "num" }, h("b", {}, r.position), r.prev && r.prev !== r.position ? [" ", delta(r.prev - r.position)] : null);
      if (c === "difficulty") return h("td", { class: "num" }, kdBadge(r.difficulty));
      if (c === "cpc") return h("td", { class: "num" }, r.cpc != null ? fmt(r.cpc, 2) + " €" : "–");
      if (c === "intent") return h("td", {}, r.intent ? h("span", { class: "intent intent-" + r.intent }, INTENT_LABEL[r.intent] || r.intent) : "–");
      if (c === "url") return h("td", { class: "url" }, r.url ? h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, r.url.replace(/^https?:\/\/[^/]+/, "") || "/") : "");
      return h("td", { class: "num" }, fmt(r[c]));
    }
    function draw() {
      fill(thead, h("tr", {}, cols.map(th)));
      var q = st.q.toLowerCase();
      var f = rows.filter(function (r) { return !q || r.keyword.toLowerCase().indexOf(q) !== -1; }).sort(function (a, b) {
        var va = a[st.sort], vb = b[st.sort];
        if (st.sort === "keyword") return String(va).localeCompare(vb) * st.dir;
        if (va == null) return 1; if (vb == null) return -1; return (va - vb) * st.dir;
      });
      fill(tbody, f.map(function (r) { return h("tr", {}, cols.map(function (c) { return cell(r, c); })); }));
      if (!f.length) tbody.append(h("tr", {}, h("td", { colspan: cols.length, class: "muted" }, "Aucun mot-clé.")));
    }
    draw();
    return h("div", {}, h("div", { class: "form-row", style: "justify-content:flex-end;margin-bottom:12px" },
      h("input", { type: "search", placeholder: "Filtrer", "aria-label": "Filtrer les mots-clés", oninput: function (e) { st.q = e.target.value; draw(); } }),
      h("button", { class: "btn btn-small", type: "button", onclick: function () {
        csv([cols.map(function (c) { return LABEL[c]; })].concat(rows.map(function (r) { return cols.map(function (c) { return c === "intent" ? INTENT_LABEL[r.intent] || "" : r[c]; }); })), filename);
      } }, "Exporter CSV")),
      h("div", { class: "table-wrap" }, h("table", {}, thead, tbody)));
  }
  function distributionBar(dist) {
    var labels = ["1-5", "6-10", "11-20", "21-50", "51-100"], colors = ["#137445", "#2bb673", "#a5dfc0", "#d2edff", "#e9dfdb"];
    var total = dist.reduce(function (a, b) { return a + b; }, 0) || 1, bar = h("div", { class: "dist", role: "img", "aria-label": "Répartition des positions" });
    dist.forEach(function (n, i) { if (n) { var seg = h("span", { title: "Positions " + labels[i] + " : " + fmt(n) }); seg.style.flex = n; seg.style.background = colors[i]; bar.append(seg); } });
    return h("div", {}, bar, h("div", { class: "legend-row" }, dist.map(function (n, i) {
      var dot = h("span", { class: "dot" }); dot.style.background = colors[i];
      return h("span", {}, dot, "Positions " + labels[i] + " : ", h("b", {}, fmt(n)), " (" + Math.round(n / total * 100) + " %)");
    })));
  }
  function viewDomain(domain, loc) {
    loc = loc || "2250";
    var input = h("input", { type: "text", id: "dm-domain", value: domain || "", placeholder: "concurrent.fr", required: true });
    var country = h("select", { id: "dm-loc", "aria-label": "Pays" }, countryKeys(COUNTRY).map(function (c) { return h("option", { value: c, selected: c === loc }, COUNTRY[c]); }));
    var form = h("form", { class: "card form-row", onsubmit: function (e) {
      e.preventDefault();
      location.hash = "#/domaine/" + encodeURIComponent(input.value.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "")) + "?loc=" + country.value;
    } }, h("div", { class: "field", style: "flex:3;min-width:240px" }, h("label", { for: "dm-domain" }, "Domaine à analyser"), input),
      h("div", { class: "field", style: "flex:0 0 170px" }, h("label", { for: "dm-loc" }, "Pays"), country), h("button", { class: "btn btn-dark", type: "submit" }, "Analyser"));
    var out = h("div");
    fill(main, h("section", { class: "hero", style: "margin-bottom:28px" }, h("p", { class: "eyebrow" }, "Analyse de domaine"),
      h("h1", {}, domain ? domain : "Ce que Google voit de n'importe quel site."),
      h("p", {}, "Trafic estimé, mots-clés positionnés, concurrents, écart de mots-clés et backlinks : pour votre site comme pour ceux de vos concurrents.")),
      form, h("p", { class: "note", style: "margin:10px 0 0" }, ME ? fmt(ME.usage.domain || 0) + " / " + fmt(ME.limits.domains_month) + " analyses ce mois-ci (un domaine déjà analysé il y a moins de 7 jours ne compte pas)." : ""), out);
    if (!domain) {
      api("/api/sites").then(function (sites) {
        if (sites.length) fill(out, h("div", { style: "margin-top:24px" }, h("p", { class: "note", style: "margin-bottom:8px" }, "Vos sites :"),
          h("div", { class: "chips" }, sites.map(function (s) { return h("a", { class: "chip", href: "#/domaine/" + encodeURIComponent(s.key) }, s.key); }))));
      });
      return;
    }
    fill(out, h("div", { class: "empty" }, h("span", { class: "spinner" }), h("p", { class: "muted" }, "Analyse de " + domain + "…")));
    api("/api/domain?domain=" + encodeURIComponent(domain) + "&location=" + loc).then(function (d) {
      refreshMe();
      var o = d.overview, b = d.backlinks, mine = (ME && sessionStorage.getItem("my-domain")) || null;
      var gapBox = h("div");
      function showGap(competitor, me) {
        fill(gapBox, h("div", { class: "card" }, h("span", { class: "spinner" }), " Recherche des mots-clés de " + competitor + " absents de " + me + "…"));
        gapBox.scrollIntoView({ behavior: "smooth", block: "start" });
        api("/api/domain/gap?competitor=" + encodeURIComponent(competitor) + "&mine=" + encodeURIComponent(me) + "&location=" + loc).then(function (g) {
          fill(gapBox, h("section", { class: "section" }, h("h2", {}, "Écart de mots-clés"),
            h("p", { class: "muted", style: "margin:6px 0 16px" }, g.keywords.length + " mots-clés où " + competitor + " est positionné et pas " + me + ", par volume. Autant d'idées de pages ou d'articles."),
            keywordTable(g.keywords, ["keyword", "volume", "difficulty", "cpc", "position", "intent", "url"], "ecart-" + competitor + "-" + me + ".csv")));
        }).catch(function (e) { fill(gapBox, errorBox(e)); });
      }
      var myInput = h("input", { type: "text", placeholder: "votre-site.fr", value: mine || "", "aria-label": "Votre domaine", style: "max-width:220px" });
      fill(out,
        h("div", { class: "kpis", style: "margin-top:28px" },
          kpi(fmt(o.traffic), "visites organiques estimées / mois", "blue"), kpi(fmt(o.keywords), "mots-clés positionnés"),
          kpi(fmt(o.value) + " €", "valeur du trafic / mois"), kpi(b.authority + " / 100", "autorité (backlinks)", "pink"), kpi(fmt(b.refdomains), "domaines référents")),
        h("p", { class: "muted", style: "margin:14px 0 0" }, "Ce mois-ci : ", h("b", {}, "+" + fmt(o.new)), " nouveaux mots-clés, ", h("b", {}, "−" + fmt(o.lost)), " perdus, ",
          h("b", {}, "▲ " + fmt(o.up)), " en hausse, ", h("b", {}, "▼ " + fmt(o.down)), " en baisse." + (d.cached ? " (données de moins de 7 jours)" : "")),
        h("section", { class: "section" }, h("h2", { style: "margin-bottom:16px" }, "Répartition des positions"), h("div", { class: "card" }, distributionBar(o.distribution))),
        h("section", { class: "section" }, h("h2", { style: "margin-bottom:6px" }, "Mots-clés qui apportent du trafic"),
          h("p", { class: "muted", style: "margin-bottom:12px" }, "Les " + d.keywords.length + " premiers, classés par trafic estimé."),
          d.keywords.length ? keywordTable(d.keywords, ["keyword", "position", "volume", "difficulty", "cpc", "traffic", "intent", "url"], "mots-cles-" + domain + ".csv")
            : h("p", { class: "muted" }, "Aucun mot-clé positionné dans le top 100 pour l'instant.")),
        h("section", { class: "section" }, h("header", {}, h("h2", {}, "Concurrents organiques"),
          h("form", { class: "form-row", onsubmit: function (e) { e.preventDefault(); sessionStorage.setItem("my-domain", myInput.value.trim()); } },
            h("span", { class: "note" }, "Votre domaine, pour l'écart de mots-clés :"), myInput)),
          d.competitors.length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Domaine"), h("th", { class: "num" }, "Mots-clés en commun"),
            h("th", { class: "num" }, "Mots-clés qu'il a en plus"), h("th", { class: "num" }, "Trafic estimé"), h("th", {}, ""))),
            h("tbody", {}, d.competitors.map(function (c) {
              return h("tr", {}, h("td", {}, h("a", { href: "#/domaine/" + encodeURIComponent(c.domain) + "?loc=" + loc }, c.domain)),
                h("td", { class: "num" }, fmt(c.common)), h("td", { class: "num" }, fmt(c.missing)), h("td", { class: "num" }, fmt(c.traffic)),
                h("td", {}, h("button", { class: "btn btn-small", type: "button", onclick: function () {
                  var me = myInput.value.trim() || domain;
                  showGap(me === domain ? c.domain : c.domain, me);
                } }, "Ses mots-clés absents de chez moi")));
            })))) : h("p", { class: "muted" }, "Pas encore assez de mots-clés pour identifier des concurrents."), gapBox),
        h("section", { class: "section" }, h("h2", { style: "margin-bottom:16px" }, "Backlinks"),
          h("div", { class: "kpis" }, kpi(fmt(b.backlinks), "backlinks"), kpi(fmt(b.refdomains), "domaines référents", "blue"),
            kpi(fmt(b.dofollow_refdomains), "domaines en dofollow"), kpi(b.dofollow_pct + " %", "liens dofollow")),
          b.anchors.length ? h("div", { class: "grid", style: "margin-top:20px" },
            h("div", { class: "card" }, h("h3", { style: "margin-bottom:10px" }, "Ancres les plus utilisées"), h("ol", { class: "top10" }, b.anchors.map(function (a) { return h("li", {}, a[0] || "(sans texte)", h("span", { class: "muted" }, " · " + fmt(a[1]))); }))),
            h("div", { class: "card" }, h("h3", { style: "margin-bottom:10px" }, "Pages les plus liées"), h("ol", { class: "top10" }, b.pages.map(function (p) { return h("li", {}, h("a", { class: "url", href: p[0], target: "_blank", rel: "noopener noreferrer" }, (p[0] || "").replace(/^https?:\/\/[^/]+/, "") || "/"), h("span", { class: "muted" }, " · " + fmt(p[1]))); }))))
            : h("p", { class: "muted", style: "margin-top:12px" }, "Aucun backlink connu pour ce domaine.")));
    }).catch(function (e) { fill(out, errorBox(e)); });
  }

  /* ---------- réglages des e-mails ---------- */
  function toggle(id, label, desc, checked, onchange) {
    var input = h("input", { type: "checkbox", id: id, role: "switch", checked: checked, onchange: function (e) { onchange(e.target.checked, input); } });
    return h("label", { class: "switch-row", for: id }, h("span", { class: "switch-text" }, h("b", {}, label), h("span", { class: "muted" }, desc)),
      input, h("span", { class: "switch", "aria-hidden": "true" }, h("span", { class: "switch-knob" })));
  }
  function emailSettings() {
    var box = h("section", { class: "section" }, h("h2", { style: "margin-bottom:16px" }, "E-mails"), h("div", { class: "card" }, h("span", { class: "spinner" })));
    api("/api/notifications").then(function (n) {
      var status = h("p", { class: "note", "aria-live": "polite" });
      function save(field) {
        return function (value, input) {
          var data = {}; data[field] = value;
          api("/api/notifications", data).then(function () { toast(value ? "C'est activé ✓" : "C'est coupé", value ? "good" : ""); })
            .catch(function (e) { input.checked = !value; toast(e.message, "bad"); });
        };
      }
      var testBtn = h("button", { class: "btn btn-small", type: "button", onclick: function () {
        testBtn.disabled = true;
        api("/api/notifications/test", {}).then(function (r) { toast("E-mail de test envoyé à " + r.to + " ✓", "good"); })
          .catch(function (e) { toast(e.message, "bad"); }).then(function () { testBtn.disabled = false; });
      } }, "M'envoyer un e-mail de test");
      fill(box.lastChild,
        toggle("ntf-digest", "Résumé hebdomadaire", "Chaque lundi matin : vos scores, vos positions et les actions prioritaires.", n.weekly_digest, save("weekly_digest")),
        toggle("ntf-alerts", "Alertes", "Score en baisse, problème grave, mot-clé qui chute ou entre dans le top 10. Une fois par heure au plus.", n.alerts, save("alerts")),
        h("div", { class: "form-row", style: "margin-top:16px;align-items:center" }, n.enabled ? testBtn : null,
          h("span", { class: "note" }, n.enabled ? "Envoyés à " + n.email + "." + (n.test_mode ? " Mode test : seule l'adresse du compte Resend reçoit les e-mails tant que le domaine d'envoi n'est pas vérifié." : "")
            : "Les e-mails ne sont pas encore configurés sur ce serveur.")), status);
    }).catch(function (e) { fill(box.lastChild, errorBox(e)); });
    return box;
  }

  /* ---------- vue : compte ---------- */
  function viewAccount() {
    loading();
    refreshMe().then(function () {
      var L = ME.limits, U = ME.usage;
      function meter(label, used, limit) {
        var pct = Math.min(100, Math.round(used / limit * 100)), m = h("div", { class: "meter" + (used >= limit ? " full" : "") }), s = h("span"); s.style.width = pct + "%"; m.append(s);
        return h("div", {}, h("div", { style: "display:flex;justify-content:space-between" }, h("span", {}, label), h("b", {}, fmt(used) + " / " + fmt(limit))), m);
      }
      var confirmIn = h("input", { type: "text", id: "del-confirm", placeholder: "SUPPRIMER", autocomplete: "off" }), delErr = h("div");
      fill(main,
        h("section", { class: "hero", style: "margin-bottom:28px" }, h("p", { class: "eyebrow" }, "Mon compte"), h("h1", { style: "font-size:clamp(2rem,4vw,3rem)" }, fullName() || ME.email || "Mon compte"),
          h("p", {}, (fullName() && ME.email ? ME.email + " · " : "") + "Offre " + L.label + (ME.running ? " · " + ME.running + " audit(s) en cours" : ""))),
        h("div", { class: "grid" },
          h("div", { class: "card", style: "display:grid;gap:18px" }, h("h3", {}, "Consommation ce mois-ci"),
            meter("Audits", U.audit, L.audits_month), meter("Analyses de concurrents", U.competitor, L.competitors_month), meter("Recherches de mots-clés", U.keywords, L.keywords_month), meter("Analyses de domaines", U.domain || 0, L.domains_month),
            h("p", { class: "note" }, "Jusqu'à " + fmt(L.max_pages) + " pages par audit et " + L.running + " audit(s) en parallèle.")),
          h("div", { class: "card blue", style: "display:grid;gap:14px;align-content:start;justify-items:start" }, h("h3", {}, "Search Console"),
            h("p", {}, ME.google ? "Connectée." : "Non connectée."), h("a", { class: "btn btn-small", href: "#/positions" }, ME.google ? "Gérer" : "Connecter"),
            h("button", { class: "btn btn-small", type: "button", onclick: function () { sb.auth.signOut(); } }, "Se déconnecter"))),
        emailSettings(),
        h("section", { class: "section", id: "offres" }, h("h2", { style: "margin-bottom:8px" }, "Changer d'offre"),
          h("p", { class: "note", style: "margin-bottom:22px" }, "Le paiement en ligne arrive bientôt : en attendant, écrivez-nous pour changer d'offre."), plansGrid(ME.plan)),
        h("section", { class: "section" }, h("details", { class: "acc" }, h("summary", {}, "Supprimer mon compte"),
          h("div", { style: "padding:0 22px 22px;display:grid;gap:14px" },
            h("p", { class: "note" }, "Tous vos audits, positions et idées seront supprimés définitivement, et l'accès à votre Search Console sera révoqué. Tapez SUPPRIMER pour confirmer."),
            h("div", { class: "form-row" }, h("div", { class: "field" }, h("label", { for: "del-confirm" }, "Confirmation"), confirmIn),
              h("button", { class: "btn danger", type: "button", onclick: function () {
                api("/api/me/delete", { confirm: confirmIn.value }).then(function () { sb.auth.signOut(); }).catch(function (e) { fill(delErr, errorBox(e)); });
              } }, "Supprimer définitivement")), delErr))));
    }).catch(fail);
  }

  /* ---------- session et routage ---------- */
  // Prénom fourni par Google (via Supabase), sinon la partie de l'e-mail avant le @.
  function firstName() {
    var meta = (SESSION && SESSION.user && SESSION.user.user_metadata) || {};
    var full = meta.full_name || meta.name || "";
    if (full) return full.trim().split(/\s+/)[0];
    return ME && ME.email ? ME.email.split("@")[0] : "";
  }
  function fullName() {
    var meta = (SESSION && SESSION.user && SESSION.user.user_metadata) || {};
    return (meta.full_name || meta.name || "").trim();
  }
  function refreshMe() { return api("/api/me").then(function (m) { ME = m; return m; }); }
  /* ---------- navigation : barre du haut et menu plein écran ---------- */
  var SECTIONS = [
    { group: "Audit", items: [["home", "#/", "Projets", "Score, historique et problèmes de vos sites"],
      ["plan", "#/plan", "Plan d'action", "Ce qu'il faut faire cette semaine, par priorité"]] },
    { group: "Visibilité Google", items: [["positions", "#/positions", "Positions", "Vos requêtes Search Console et leur analyse"],
      ["tracking", "#/suivi", "Suivi", "Vos positions chaque jour, face aux concurrents"]] },
    { group: "Recherche", items: [["keywords", "#/mots-cles", "Mots-clés", "Volumes, difficulté et intention"],
      ["domain", "#/domaine", "Domaines", "Trafic, mots-clés et backlinks de n'importe quel site"],
      ["competitors", "#/concurrents", "Concurrents", "Comparaison technique avec vos rivaux"]] }];
  var ROUTE_SECTION = { site: "home", audit: "home", "mots-cles": "keywords", concurrents: "competitors", suivi: "tracking", domaine: "domain", compte: "account", offres: "account" };
  var currentSection = "home";
  var menu = document.getElementById("menu"), burger = document.getElementById("burger"), topBar = document.querySelector(".top");
  var lastFocus = null, menuTimer = null;
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function buildMenu() {
    var n = 0;
    fill(menu, h("div", { class: "menu-inner" },
      h("div", { class: "menu-groups" }, SECTIONS.map(function (g) {
        return h("section", { class: "menu-group" }, h("p", { class: "menu-label", style: "--i:" + (n++) }, g.group),
          h("ul", {}, g.items.map(function (it) {
            return h("li", { style: "--i:" + (n++) }, h("a", { class: "menu-item", href: it[1], "aria-current": currentSection === it[0] ? "page" : null },
              h("span", { class: "menu-title" }, it[2], h("span", { class: "menu-arrow", "aria-hidden": "true" }, "→")), h("span", { class: "menu-desc" }, it[3])));
          })));
      })),
      h("div", { class: "menu-foot", style: "--i:" + (n++) },
        h("button", { class: "btn btn-green", type: "button", onclick: function () { closeMenu(); newAuditModal(); } }, "Nouvel audit"),
        h("a", { class: "btn btn-ghost", href: "#/compte" }, "Mon compte" + (ME ? " · offre " + ME.limits.label : "")),
        h("button", { class: "btn btn-ghost", type: "button", onclick: function () { closeMenu(); sb.auth.signOut(); } }, "Se déconnecter"),
        h("button", { class: "btn btn-ghost theme-switch", type: "button", "aria-pressed": String(isDark()), onclick: toggleTheme }, isDark() ? "☀ Mode clair" : "☾ Mode sombre"))));
  }
  function isDark() {
    var t = document.documentElement.dataset.theme;
    return t ? t === "dark" : !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function toggleTheme() {
    var next = isDark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("vigie-theme", next); } catch (e) {}
    buildMenu();
    toast(next === "dark" ? "Mode sombre activé" : "Mode clair activé", "good");
  }
  function openMenu() {
    clearTimeout(menuTimer);
    buildMenu();
    lastFocus = document.activeElement;
    menu.hidden = false; menu.classList.remove("closing");
    topBar.classList.add("menu-open"); document.documentElement.classList.add("no-scroll");
    burger.setAttribute("aria-expanded", "true"); burger.setAttribute("aria-label", "Fermer le menu");
    requestAnimationFrame(function () { menu.classList.add("open"); });
    setTimeout(function () { var f = menu.querySelector(".menu-item"); if (f) f.focus(); }, reduceMotion ? 0 : 350);
  }
  function closeMenu() {
    if (menu.hidden) return;
    menu.classList.remove("open"); menu.classList.add("closing");
    topBar.classList.remove("menu-open"); document.documentElement.classList.remove("no-scroll");

    burger.setAttribute("aria-expanded", "false"); burger.setAttribute("aria-label", "Ouvrir le menu");
    menuTimer = setTimeout(function () { menu.hidden = true; menu.classList.remove("closing"); }, reduceMotion ? 0 : 420);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  burger.addEventListener("click", function () { if (menu.hidden || menu.classList.contains("closing")) openMenu(); else closeMenu(); });
  menu.addEventListener("click", function (e) { if (e.target.closest("a.menu-item, a.btn")) closeMenu(); });
  document.addEventListener("keydown", function (e) {
    if (menu.hidden) return;
    if (e.key === "Escape") { closeMenu(); return; }
    if (e.key === "Tab") {  // le focus reste dans le menu (et sur le bouton qui le ferme)
      var f = [burger].concat(Array.prototype.slice.call(menu.querySelectorAll("a, button")));
      var i = f.indexOf(document.activeElement);
      if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
    }
  });

  function renderTop() {
    var actions = document.getElementById("top-actions"), section = document.getElementById("top-section");
    burger.hidden = !SESSION; section.hidden = !SESSION;
    if (SESSION) {
      var initial = (firstName() || (ME && ME.email) || "?").charAt(0).toUpperCase();
      fill(actions, h("button", { class: "btn btn-dark top-cta", type: "button", onclick: function () { newAuditModal(); } }, h("span", { "aria-hidden": "true" }, "+ "), "Nouvel audit"),
        h("a", { class: "avatar", href: "#/compte", "aria-label": "Mon compte", title: "Mon compte" }, initial));
    } else fill(actions, h("a", { class: "top-link", href: "#/offres" }, "Offres"), h("button", { class: "btn btn-dark", type: "button", onclick: signIn }, "Se connecter"));
  }
  function setSection(name) {
    currentSection = ROUTE_SECTION[name] || name;
    var item = null;
    SECTIONS.forEach(function (g) { g.items.forEach(function (it) { if (it[0] === currentSection) item = it; }); });
    var el = document.getElementById("top-section");
    if (item) { el.href = item[1]; fill(el, h("span", { class: "dot-live", "aria-hidden": "true" }), item[2]); }
    else fill(el, h("span", { class: "dot-live", "aria-hidden": "true" }), currentSection === "account" ? "Mon compte" : "Vigie SEO");
  }
  function route() {
    var hash = location.hash.replace(/^#\/?/, ""), qs = new URLSearchParams(hash.split("?")[1] || "");
    var parts = hash.split("?")[0].split("/").map(decodeURIComponent), name = parts[0] || "home";
    closeOverlay(); closeMenu();
    window.scrollTo(0, 0);
    if (!SESSION) { viewLanding(name === "offres"); main.focus({ preventScroll: true }); return; }
    setSection(name);
    if (name === "site" && parts[1]) viewSite(parts[1]);
    else if (name === "audit" && parts[1]) viewAudit(parts[1]);
    else if (name === "plan") viewPlan(parts[1]);
    else if (name === "positions") viewPositions(qs.get("google"));
    else if (name === "suivi") viewTracking(parts[1]);
    else if (name === "domaine") viewDomain(parts[1], qs.get("loc"));
    else if (name === "mots-cles") viewKeywords();
    else if (name === "concurrents") viewCompetitors(parts[1]);
    else if (name === "compte" || name === "offres") viewAccount();
    else viewHome();
    main.focus({ preventScroll: true });
  }

  var booted = false;
  // Supabase émet SIGNED_IN à chaque retour sur l'onglet et TOKEN_REFRESHED toutes les heures :
  // on ne redessine la page que si l'utilisateur change (connexion, déconnexion), jamais pour le même compte.
  function onSession(event, session) {
    var prevId = SESSION && SESSION.user && SESSION.user.id, newId = session && session.user && session.user.id;
    SESSION = session;
    if (booted && prevId === newId) return;
    booted = true;
    renderTop();
    if (!session) { ME = null; if (prevId) location.hash = "#/"; route(); return; }
    var next = sessionStorage.getItem("after-login");
    sessionStorage.removeItem("after-login");
    var chain = refreshMe();
    if (sessionStorage.getItem("gsc-connect")) {
      // retour de l'autorisation Search Console : le jeton de rafraîchissement n'est présent qu'à ce moment-là
      sessionStorage.removeItem("gsc-connect");
      chain = chain.then(function () {
        if (!session.provider_refresh_token) throw new Error("pas de jeton");
        return api("/api/google/token", { refresh_token: session.provider_refresh_token }).then(function () { next = "#/positions?google=ok"; });
      }).catch(function () { next = "#/positions?google=erreur"; });
    }
    chain.catch(function () {}).then(function () {
      if (next && next !== location.hash) location.hash = next; else route();
    });
  }

  // Retour d'une connexion Google refusée (?error=access_denied…) : message clair au lieu d'une page muette.
  (function oauthError() {
    var q = new URLSearchParams(location.search), err = q.get("error");
    if (!err) return;
    var gsc = sessionStorage.getItem("gsc-connect");
    sessionStorage.removeItem("gsc-connect");
    sessionStorage.removeItem("after-login");
    history.replaceState(null, "", location.pathname + (gsc ? "#/positions?google=" + (err === "access_denied" ? "refuse" : "erreur") : "#/"));
  })();

  window.addEventListener("hashchange", route);
  fetch("/api/config").then(function (r) { return r.json(); }).then(function (c) {
    CONFIG = c;
    if (!c.supabase_url || !window.supabase) { fill(main, emptyState("Configuration incomplète", "Variables manquantes côté serveur : " + (c.missing || []).join(", "), null)); return; }
    sb = window.supabase.createClient(c.supabase_url, c.supabase_anon_key, { auth: { flowType: "pkce", detectSessionInUrl: true, persistSession: true } });
    sb.auth.onAuthStateChange(function (event, session) {
      setTimeout(function () { onSession(event, session); }, 0);
    });
  }).catch(function (e) { fail(e); });
})();
