(function () {
  "use strict";
  var SEV = ["critical", "high", "medium", "low", "info"];
  var SEV_LABEL = { critical: "Critique", high: "Élevée", medium: "Moyenne", low: "Faible", info: "Info" };
  var CAT_LABEL = { http: "HTTP", indexabilite: "Indexabilité", contenu: "Titres et contenu", liens: "Liens",
    performance: "Performance", technique: "Technique", international: "International", donnees: "Données structurées" };
  var CARD_TINTS = ["blue", "pink", "", "blue", "pink", ""];
  var main = document.getElementById("main"), overlay = document.getElementById("overlay");

  /* ---------- utilitaires ---------- */
  // Tout contenu venant d'un site crawlé passe par textContent : jamais d'innerHTML avec des données.
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
  var nf = new Intl.NumberFormat("fr-FR");
  function fmt(n, d) { return n == null ? "–" : (d ? n.toLocaleString("fr-FR", { maximumFractionDigits: d }) : nf.format(Math.round(n))); }
  // Remplace le contenu d'un noeud ; accepte des listes imbriquées, contrairement à replaceChildren.
  function fill(node) {
    var kids = Array.prototype.slice.call(arguments, 1).flat(Infinity).filter(function (c) { return c != null && c !== false; });
    node.replaceChildren.apply(node, kids.map(function (c) { return c.nodeType ? c : document.createTextNode(String(c)); }));
  }
  function fdate(s, long) { return new Date(s).toLocaleDateString("fr-FR", long ? { day: "numeric", month: "long", year: "numeric" } : { day: "numeric", month: "short" }); }
  function fdatetime(s) { return new Date(s).toLocaleString("fr-FR", { dateStyle: "long", timeStyle: "short" }); }
  function scoreColor(s) { return s >= 85 ? "var(--green)" : s >= 60 ? "var(--sev-medium)" : "var(--orange)"; }
  function api(path, body) {
    var opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || ("Erreur " + r.status));
        return d;
      });
    });
  }
  function loading() { fill(main, h("div", { class: "empty" }, h("span", { class: "spinner", role: "status", "aria-label": "Chargement" }))); }
  function fail(e) { fill(main, h("div", { class: "empty" }, h("h2", {}, "Oups"), h("p", { class: "muted" }, e.message || String(e)), h("a", { class: "btn", href: "#/" }, "Retour"))); }

  /* ---------- composants ---------- */
  function ring(score, size) {
    var r = 42, c = 2 * Math.PI * r, wrap = h("div", { class: "ring", role: "img", "aria-label": "Score " + score + " sur 100" });
    wrap.style.setProperty("--s", (size || 96) + "px");
    wrap.append(svg("svg", { viewBox: "0 0 100 100" },
      svg("circle", { cx: 50, cy: 50, r: r, fill: "none", stroke: "rgba(0,0,0,.09)", "stroke-width": 10 }),
      svg("circle", { cx: 50, cy: 50, r: r, fill: "none", stroke: scoreColor(score), "stroke-width": 10, "stroke-linecap": "round",
        "stroke-dasharray": (c * score / 100) + " " + c })), h("span", { class: "v" }, score));
    return wrap;
  }
  function delta(d, invert) {
    if (d == null || d === 0) return null;
    var good = invert ? d < 0 : d > 0;
    return h("span", { class: "delta " + (good ? "up" : "down") }, (d > 0 ? "▲ +" : "▼ ") + d);
  }
  function sevDot(s) { var d = h("span", { class: "dot" }); d.style.background = "var(--sev-" + s + ")"; return d; }
  function sevs(counts) {
    return h("div", { class: "sevs" }, SEV.filter(function (s) { return counts[s]; }).map(function (s) {
      return h("span", {}, sevDot(s), h("b", {}, fmt(counts[s])), " " + SEV_LABEL[s].toLowerCase());
    }));
  }
  // Courbe SVG : points = [{x: Date|number, y: number}], options : min, max, invert, color, height
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
    var color = o.color || "#0b0b0b", s = svg("svg", { class: "chart", viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": o.label || "Graphique" });
    for (var i = 0; i <= 4; i++) {
      var v = lo + (hi - lo) * i / 4, y = Y(v);
      s.append(svg("line", { class: "grid-line", x1: P.l, x2: W - P.r, y1: y, y2: y }), svg("text", { x: P.l - 8, y: y + 4, "text-anchor": "end" }, fmt(v, hi - lo < 10 ? 1 : 0)));
    }
    var d = points.map(function (p, i) { return (i ? "L" : "M") + X(+p.x).toFixed(1) + " " + Y(p.y).toFixed(1); }).join(" ");
    if (points.length > 1) s.append(svg("path", { class: "area", d: d + " L" + X(x1) + " " + (H - P.b) + " L" + X(x0) + " " + (H - P.b) + " Z", fill: color }));
    s.append(svg("path", { d: d, fill: "none", stroke: color, "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    points.forEach(function (p, i) {
      if (points.length > 40 && i !== points.length - 1) return;
      s.append(svg("circle", { cx: X(+p.x), cy: Y(p.y), r: 5, fill: "#fff", stroke: color, "stroke-width": 3 }, svg("title", {}, fdate(p.x, true) + " : " + fmt(p.y, 1))));
    });
    [points[0], points[points.length - 1]].forEach(function (p, i) {
      s.append(svg("text", { x: X(+p.x), y: H - 6, "text-anchor": i ? "end" : "start" }, fdate(p.x)));
    });
    return s;
  }
  function spark(values) {
    var W = 120, H = 36;
    if (values.length < 2) return h("span", { class: "muted" }, "1 audit");
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values) || 1;
    if (lo === hi) { lo -= 1; hi += 1; }
    var d = values.map(function (v, i) { return (i ? "L" : "M") + (i / (values.length - 1) * (W - 4) + 2).toFixed(1) + " " + (H - 4 - (v - lo) / (hi - lo) * (H - 8)).toFixed(1); }).join(" ");
    return svg("svg", { width: W, height: H, viewBox: "0 0 " + W + " " + H, "aria-hidden": "true" }, svg("path", { d: d, fill: "none", stroke: "#0b0b0b", "stroke-width": 2.5, "stroke-linecap": "round", "stroke-linejoin": "round" }));
  }
  function kpi(value, label, tint) { return h("div", { class: "kpi " + (tint || "") }, h("span", { class: "v" }, value), h("span", { class: "l" }, label)); }
  function art() {
    return svg("svg", { class: "art", viewBox: "0 0 120 120", "aria-hidden": "true" },
      svg("circle", { cx: 60, cy: 60, r: 56, fill: "#d2edff" }), svg("circle", { cx: 60, cy: 60, r: 34, fill: "#ffc3c3" }),
      svg("circle", { cx: 60, cy: 60, r: 14, fill: "#2bb673" }), svg("path", { d: "M92 92l16 16", stroke: "#0b0b0b", "stroke-width": 8, "stroke-linecap": "round" }),
      svg("circle", { cx: 80, cy: 80, r: 20, fill: "none", stroke: "#0b0b0b", "stroke-width": 8 }));
  }
  function emptyState(title, text, cta) {
    return h("div", { class: "card empty" }, art(), h("h2", {}, title), h("p", { class: "muted" }, text), cta);
  }
  function csv(rows, name) {
    var data = rows.map(function (r) { return r.map(function (c) { return '"' + String(c == null ? "" : c).replace(/"/g, '""') + '"'; }).join(";"); }).join("\n");
    var a = h("a", { href: URL.createObjectURL(new Blob(["﻿" + data], { type: "text/csv" })), download: name });
    a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  /* ---------- tâches longues et fenêtres ---------- */
  function closeOverlay() { overlay.hidden = true; fill(overlay, ); }
  function modal(title, body) {
    fill(overlay, h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title }, body));
    overlay.hidden = false;
    var f = overlay.querySelector("input, button"); if (f) f.focus();
  }
  overlay.addEventListener("click", function (e) { if (e.target === overlay) closeOverlay(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !overlay.hidden) closeOverlay(); });

  function trackJob(job, title, onDone) {
    var log = h("div", { class: "log", "aria-live": "polite" }), status = h("p", {}, h("span", { class: "spinner" }), " En cours…");
    modal(title, [h("h2", {}, title), status, log, h("div", { class: "form-row" }, h("button", { class: "btn btn-small", type: "button", onclick: closeOverlay }, "Réduire"))]);
    (function poll() {
      api("/api/jobs/" + job.id).then(function (j) {
        log.textContent = j.log.join("\n"); log.scrollTop = log.scrollHeight;
        if (j.status === "running") return setTimeout(poll, 1200);
        if (j.status === "failed") { fill(status, h("span", { class: "error" }, j.error || "Échec")); return; }
        closeOverlay(); onDone(j.result);
      }).catch(function (e) { fill(status, h("span", { class: "error" }, e.message)); });
    })();
  }
  function startJob(path, body, title, onDone, errBox) {
    return api(path, body).then(function (job) { trackJob(job, title, onDone); }).catch(function (e) {
      if (errBox) fill(errBox, h("div", { class: "error", role: "alert" }, e.message));
      else alert(e.message);
    });
  }

  function newAuditModal(prefill) {
    var url = h("input", { type: "text", id: "au-url", value: prefill || "", placeholder: "www.exemple.fr", autocomplete: "off", inputmode: "url" });
    var pages = h("input", { type: "number", id: "au-pages", value: 200, min: 1, max: 5000 });
    var ext = h("input", { type: "checkbox", id: "au-ext" }), err = h("div");
    var go = h("button", { class: "btn btn-dark", type: "submit" }, "Lancer l'audit");
    var form = h("form", { class: "modal-form", style: "display:grid;gap:18px", onsubmit: function (e) {
      e.preventDefault(); go.disabled = true;
      startJob("/api/audits", { url: url.value, max_pages: +pages.value, check_external: ext.checked }, "Audit en cours", function (r) {
        location.hash = "#/site/" + r.site; route();
      }, err).then(function () { go.disabled = false; });
    } },
      h("div", { class: "field" }, h("label", { for: "au-url" }, "Adresse du site"), url),
      h("div", { class: "field" }, h("label", { for: "au-pages" }, "Pages à explorer au maximum"), pages),
      h("label", { class: "check" }, ext, "Vérifier aussi les liens externes (plus long)"), err,
      h("div", { class: "form-row" }, go, h("button", { class: "btn", type: "button", onclick: closeOverlay }, "Annuler")));
    modal("Nouvel audit", [h("p", { class: "eyebrow" }, "Nouvel audit"), h("h2", {}, "Quel site analyser ?"), form]);
  }
  document.getElementById("new-audit").addEventListener("click", function () { newAuditModal(); });

  /* ---------- vue : projets ---------- */
  function viewHome() {
    loading();
    api("/api/sites").then(function (sites) {
      var hero = h("section", { class: "hero" }, h("p", { class: "eyebrow" }, "Audit SEO technique"),
        h("h1", {}, "Gardez un œil sur la santé de vos sites."),
        h("p", {}, "Explorez un site, repérez ce qui freine son référencement, suivez les progrès audit après audit."),
        h("div", { class: "actions" }, h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(); } }, "Analyser un site"),
          h("a", { class: "btn", href: "#/positions" }, "Suivre mes positions")));
      var body;
      if (!sites.length) body = emptyState("Aucun audit pour le moment", "Lancez votre premier audit : il apparaîtra ici avec son score et son historique.",
        h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(); } }, "Lancer un audit"));
      else body = h("div", { class: "grid" }, sites.map(function (s, i) {
        var l = s.latest, tint = CARD_TINTS[i % CARD_TINTS.length], d = l.comparison && delta(l.comparison.score_delta);
        return h("a", { class: "card site-card " + tint, href: "#/site/" + encodeURIComponent(s.key) },
          h("div", { class: "row" }, h("div", {}, h("p", { class: "name" }, s.key), h("p", { class: "muted" }, s.audits + " audit" + (s.audits > 1 ? "s" : "") + " · dernier le " + fdate(l.date, true))), ring(l.score)),
          h("div", { class: "row", style: "align-items:end" }, sevs(l.issues_by_severity), h("div", { style: "display:grid;gap:6px;justify-items:end" }, d, spark(s.history.map(function (x) { return x.score; })))));
      }));
      fill(main, hero, h("section", {}, h("header", { class: "section", style: "margin:0 0 20px" }, h("h2", {}, "Vos projets")), body));
    }).catch(fail);
  }

  /* ---------- vue : projet ---------- */
  function viewSite(key) {
    loading();
    api("/api/sites/" + encodeURIComponent(key)).then(function (site) {
      var audits = site.audits, last = audits[0];
      var cats = Object.keys(last.category_scores || {});
      fill(main, 
        h("a", { class: "crumb", href: "#/" }, "← Projets"),
        h("div", { class: "page-head", style: "margin-top:16px" }, ring(last.score, 132),
          h("div", { class: "grow" }, h("p", { class: "eyebrow" }, "Projet"), h("h1", { style: "font-size:clamp(2rem,4vw,3rem);word-break:break-word" }, key),
            h("p", { class: "muted" }, fmt(last.pages) + " pages explorées, dont " + fmt(last.indexable) + " indexables · audit du " + fdatetime(last.date)),
            sevs(last.issues_by_severity)),
          h("div", { class: "form-row" }, h("a", { class: "btn btn-dark", href: "#/audit/" + last.dir }, "Voir les problèmes"),
            h("button", { class: "btn", type: "button", onclick: function () { newAuditModal(key); } }, "Relancer un audit"))),
        h("section", {}, h("h2", { style: "margin-bottom:20px" }, "Score par catégorie"),
          h("div", { class: "grid", style: "grid-template-columns:repeat(auto-fill,minmax(min(100%,220px),1fr))" }, cats.map(function (c, i) {
            var prev = audits[1] && audits[1].category_scores && audits[1].category_scores[c];
            return h("div", { class: "card " + CARD_TINTS[i % CARD_TINTS.length], style: "padding:22px;display:flex;gap:16px;align-items:center" },
              ring(last.category_scores[c], 64), h("div", {}, h("h3", { style: "font-size:1rem" }, CAT_LABEL[c] || c), prev != null ? delta(last.category_scores[c] - prev) : null));
          }))),
        h("section", { class: "section" }, h("h2", { style: "margin-bottom:20px" }, "Évolution du score"),
          h("div", { class: "card" }, lineChart(audits.slice().reverse().map(function (a) { return { x: new Date(a.date), y: a.score }; }),
            { min: 0, max: 100, color: "#137445", label: "Score de santé au fil des audits" }))),
        h("section", { class: "section" }, h("header", {}, h("h2", {}, "Historique des audits"),
          h("a", { class: "btn btn-small", href: "#/concurrents/" + encodeURIComponent(key) }, "Comparer à des concurrents")),
          h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Date"), h("th", { class: "num" }, "Score"), h("th", { class: "num" }, "Pages"),
            h("th", {}, "Problèmes"), h("th", {}, "Évolution"), h("th", {}, ""))),
            h("tbody", {}, audits.map(function (a) {
              var c = a.comparison;
              return h("tr", { class: "click", onclick: function () { location.hash = "#/audit/" + a.dir; } },
                h("td", {}, h("a", { href: "#/audit/" + a.dir }, fdatetime(a.date))), h("td", { class: "num" }, h("b", {}, a.score)), h("td", { class: "num" }, fmt(a.pages)),
                h("td", {}, sevs(a.issues_by_severity)),
                h("td", {}, c ? [delta(c.score_delta), " ", h("span", { class: "muted" }, "+" + c.new_count + " / −" + c.fixed_count)] : h("span", { class: "muted" }, "Premier audit")),
                h("td", {}, h("a", { href: "/reports/" + a.dir + "/rapport.html", target: "_blank", rel: "noopener" }, "Rapport autonome ↗")));
            }))))));
    }).catch(fail);
  }

  /* ---------- vue : audit ---------- */
  function viewAudit(dir) {
    loading();
    api("/api/audits/" + dir).then(function (data) {
      var S = data.summary, issues = data.issues, site = dir.replace(/-\d{8}-\d{4}$/, "");
      var state = { cat: "", sev: "", q: "" };
      var pillsEl = h("div", { class: "pills", role: "group", "aria-label": "Filtrer par catégorie" });
      var listEl = h("div"), countEl = h("p", { class: "muted", "aria-live": "polite", style: "margin-bottom:14px" });
      var catCounts = {};
      issues.forEach(function (i) { catCounts[i.category] = (catCounts[i.category] || 0) + 1; });

      function pill(label, value, n) {
        var b = h("button", { class: "pill", type: "button", "aria-pressed": String(state.cat === value), onclick: function () { state.cat = value; renderPills(); renderList(); } }, label, h("span", { class: "n" }, n));
        return b;
      }
      function renderPills() {
        fill(pillsEl, pill("Tous", "", issues.length), Object.keys(catCounts).map(function (c) { return pill(CAT_LABEL[c] || c, c, catCounts[c]); }));
      }
      function renderList() {
        var term = state.q.toLowerCase();
        var f = issues.filter(function (i) {
          return (!state.cat || i.category === state.cat) && (!state.sev || i.severity === state.sev) &&
            (!term || (i.url + " " + i.message + " " + i.detail).toLowerCase().indexOf(term) !== -1);
        });
        var groups = {};
        f.forEach(function (i) { (groups[i.code] = groups[i.code] || { code: i.code, severity: i.severity, message: i.message.replace(/\s*\(.*\)$/, "").replace(/\s*:.*$/, ""), items: [] }).items.push(i); });
        var arr = Object.values(groups).sort(function (a, b) { return SEV.indexOf(a.severity) - SEV.indexOf(b.severity) || b.items.length - a.items.length; });
        countEl.textContent = fmt(f.length) + " problème" + (f.length > 1 ? "s" : "") + " sur " + fmt(arr.length) + " type" + (arr.length > 1 ? "s" : "");
        fill(listEl, arr.length ? arr.map(function (g, idx) {
          return h("details", { class: "acc", open: arr.length === 1 || (idx === 0 && !!state.cat) },
            h("summary", {}, sevDot(g.severity), g.message, h("span", { class: "n" }, fmt(g.items.length))),
            h("ul", {}, g.items.slice(0, 100).map(function (i) {
              return h("li", {}, h("a", { class: "url", href: i.url, target: "_blank", rel: "noopener" }, i.url), i.detail ? h("span", { class: "detail" }, i.detail) : null);
            }), g.items.length > 100 ? h("li", { class: "muted" }, "… et " + fmt(g.items.length - 100) + " autres (voir le CSV)") : null));
        }) : h("div", { class: "card empty" }, h("h3", {}, "Rien à signaler ici"), h("p", { class: "muted" }, "Aucun problème ne correspond à ces filtres.")));
      }
      var sevSel = h("select", { "aria-label": "Gravité", onchange: function (e) { state.sev = e.target.value; renderList(); } },
        h("option", { value: "" }, "Toutes les gravités"), SEV.map(function (s) { return h("option", { value: s }, SEV_LABEL[s]); }));
      var search = h("input", { type: "search", placeholder: "Filtrer par URL ou texte", "aria-label": "Filtrer par URL ou texte", oninput: function (e) { state.q = e.target.value; renderList(); } });
      renderPills(); renderList();

      var cmp = S.comparison;
      fill(main, 
        h("a", { class: "crumb", href: "#/site/" + encodeURIComponent(site) }, "← " + site),
        h("div", { class: "page-head", style: "margin-top:16px" }, ring(S.health_score, 112),
          h("div", { class: "grow" }, h("p", { class: "eyebrow" }, "Audit du " + fdatetime(S.date)), h("h1", { style: "font-size:clamp(1.8rem,3.6vw,2.6rem)" }, "Problèmes à corriger"),
            sevs(S.issues_by_severity)),
          h("div", { class: "form-row" }, h("a", { class: "btn btn-small", href: "/reports/" + dir + "/rapport.html", target: "_blank", rel: "noopener" }, "Rapport autonome ↗"),
            h("a", { class: "btn btn-small", href: "/reports/" + dir + "/problemes.csv", download: "" }, "CSV problèmes"),
            h("a", { class: "btn btn-small", href: "/reports/" + dir + "/pages.csv", download: "" }, "CSV pages"))),
        h("div", { class: "kpis" }, kpi(fmt(S.pages_crawled), "pages explorées"), kpi(fmt(S.pages_indexable), "pages indexables", "blue"), kpi(fmt(S.sitemap_urls), "URLs dans le sitemap"),
          cmp ? kpi((cmp.score_delta > 0 ? "+" : "") + cmp.score_delta, "points depuis le " + fdate(cmp.previous_date), cmp.score_delta >= 0 ? "blue" : "pink") : null),
        cmp ? h("section", { class: "section" }, h("h2", { style: "margin-bottom:20px" }, "Depuis l'audit précédent"),
          h("div", { class: "grid" },
            h("details", { class: "acc", style: "align-self:start" }, h("summary", {}, h("span", { class: "dot", style: "background:var(--orange)" }), "Nouveaux problèmes", h("span", { class: "n" }, fmt(cmp.new_count))), changeList(cmp.new)),
            h("details", { class: "acc", style: "align-self:start" }, h("summary", {}, h("span", { class: "dot", style: "background:var(--green)" }), "Problèmes corrigés", h("span", { class: "n" }, fmt(cmp.fixed_count))), changeList(cmp.fixed)))) : null,
        h("section", { class: "section" }, h("header", {}, h("h2", {}, "Tous les problèmes"), h("div", { class: "form-row" }, sevSel, search)),
          pillsEl, h("div", { style: "height:20px" }), countEl, listEl));
    }).catch(fail);
  }
  function changeList(items) {
    return h("ul", {}, items.slice(0, 60).map(function (i) {
      return h("li", {}, sevDot(i.severity), i.message, h("a", { class: "detail url", href: i.url, target: "_blank", rel: "noopener" }, i.url));
    }), items.length ? null : h("li", { class: "muted" }, "Aucun"));
  }

  /* ---------- vue : positions ---------- */
  var GOOGLE_FLASH = { ok: ["Compte Google connecté.", false], refuse: ["Connexion annulée : vous n'avez pas autorisé l'accès.", true], erreur: ["La connexion à Google a échoué, réessayez.", true] };

  // Carte « Connecter Google » : trois états (identifiant OAuth absent, non connecté, connecté).
  function googleCard(st, sites, current, onChange, flash) {
    var err = h("div", { role: "status" });
    if (flash && GOOGLE_FLASH[flash]) fill(err, h("div", { class: GOOGLE_FLASH[flash][1] ? "error" : "delta up", style: "padding:10px 16px" }, GOOGLE_FLASH[flash][0]));
    function showErr(e) { fill(err, h("div", { class: "error", role: "alert" }, e.message)); }

    if (!st.configured) {
      var ta = h("textarea", { id: "g-json", rows: 5, placeholder: '{"installed":{"client_id":"…","client_secret":"…", …}}', style: "width:100%;border:2px solid var(--ink);border-radius:var(--r-md);padding:14px 18px;font:13px ui-monospace,Menlo,monospace" });
      return h("form", { class: "card", style: "display:grid;gap:16px", onsubmit: function (e) {
        e.preventDefault();
        api("/api/google/client", { json: ta.value }).then(onChange).catch(showErr);
      } }, h("h3", {}, "Étape 1 : configurer la connexion Google"),
        h("ol", { class: "note", style: "margin:0;padding-left:20px;display:grid;gap:4px" },
          h("li", {}, "Ouvrez la console Google Cloud, créez un projet, puis activez « Google Search Console API »."),
          h("li", {}, "Écran de consentement OAuth : type « Externe », ajoutez votre adresse comme utilisateur test."),
          h("li", {}, "Identifiants → Créer des identifiants → ID client OAuth → type « Application de bureau »."),
          h("li", {}, "Téléchargez le fichier JSON et collez son contenu ci-dessous.")),
        h("div", { class: "field" }, h("label", { for: "g-json" }, "Contenu du fichier JSON"), ta),
        h("div", { class: "form-row" }, h("button", { class: "btn btn-dark", type: "submit" }, "Enregistrer"),
          h("a", { class: "btn", href: "https://console.cloud.google.com/apis/credentials", target: "_blank", rel: "noopener" }, "Ouvrir Google Cloud ↗")),
        h("p", { class: "note" }, "À faire une seule fois par installation. Le fichier reste sur cette machine (dossier data/)."), err);
    }
    if (!st.connected) {
      return h("div", { class: "card blue", style: "display:grid;gap:14px;justify-items:start" },
        h("h3", {}, "Connectez votre Search Console"),
        h("p", { class: "note", style: "color:#3d3a38" }, "Vous serez redirigé vers Google pour autoriser la lecture seule de vos données. Vigie ne peut rien modifier sur vos sites."),
        h("button", { class: "btn btn-dark", type: "button", onclick: function (e) {
          e.currentTarget.disabled = true;
          api("/api/google/connect", {}).then(function (r) { location.href = r.url; }).catch(function (x) { e.currentTarget.disabled = false; showErr(x); });
        } }, "Connecter avec Google"), err);
    }
    var sel = h("select", { id: "ps-site", "aria-label": "Propriété Search Console" }, h("option", { value: "" }, "Chargement des propriétés…"));
    var days = h("select", { id: "ps-days", "aria-label": "Période d'import" }, [30, 90, 180, 480].map(function (d) { return h("option", { value: d, selected: d === 90 }, d + " jours"); }));
    api("/api/google/sites").then(function (props) {
      fill(sel, props.length ? props.map(function (p) { return h("option", { value: p.site, selected: p.site === current }, p.site); }) : h("option", { value: "" }, "Aucune propriété accessible"));
    }).catch(function (e) { fill(sel, h("option", { value: "" }, "Indisponible")); showErr(e); });
    return h("form", { class: "card", style: "display:grid;gap:16px", onsubmit: function (e) {
      e.preventDefault(); if (!sel.value) return;
      sessionStorage.setItem("pos-site", sel.value);
      startJob("/api/positions/sync", { site: sel.value, days: +days.value }, "Import Search Console", function () { viewPositions(); }, err);
    } }, h("div", { style: "display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center" },
        h("h3", {}, "Importer les données"),
        h("span", { class: "muted" }, "Connecté : " + (st.email || "compte Google"), " ",
          h("button", { class: "btn btn-small", type: "button", onclick: function () { api("/api/google/disconnect", {}).then(onChange).catch(showErr); } }, "Déconnecter"))),
      h("div", { class: "form-row" }, h("div", { class: "field" }, h("label", { for: "ps-site" }, "Propriété Search Console"), sel),
        h("div", { class: "field", style: "flex:0 0 150px;min-width:130px" }, h("label", { for: "ps-days" }, "Période"), days),
        h("button", { class: "btn btn-dark", type: "submit" }, "Importer")),
      h("p", { class: "note" }, "Relancez l'import régulièrement : les jours déjà stockés sont mis à jour, les anciens sont conservés (Google ne garde que 16 mois)."), err);
  }

  function viewPositions(flash) {
    loading();
    Promise.all([api("/api/positions"), api("/api/google/status")]).then(function (res) {
      var sites = res[0], g = res[1];
      var current = sessionStorage.getItem("pos-site") || sites[0] || "";
      var wrap = h("div"), head = h("section", { class: "hero", style: "margin-bottom:28px" }, h("p", { class: "eyebrow" }, "Search Console"), h("h1", {}, "Où apparaissent vos pages ?"),
        h("p", {}, "Clics, impressions et position moyenne de chaque requête, importés depuis la Search Console et conservés ici pour suivre leur évolution."));
      var sync = googleCard(g, sites, current, function () { viewPositions(); }, flash);

      var results = h("div");
      function load(site, nDays) {
        if (!site) { fill(results, ); return; }
        fill(results, h("div", { class: "empty" }, h("span", { class: "spinner" })));
        api("/api/positions/summary?site=" + encodeURIComponent(site) + "&days=" + nDays).then(function (d) { renderPositions(results, d); }).catch(function (e) { fill(results, h("div", { class: "error" }, e.message)); });
      }
      var picker = sites.length ? h("div", { class: "form-row", style: "margin:28px 0 20px" },
        h("div", { class: "field", style: "max-width:420px" }, h("label", { for: "ps-pick" }, "Propriété suivie"),
          h("select", { id: "ps-pick", onchange: function (e) { sessionStorage.setItem("pos-site", e.target.value); load(e.target.value, 28); } },
            sites.map(function (s) { return h("option", { value: s, selected: s === current }, s); })))) : null;
      wrap.append(sync, picker, results);
      fill(main, head, wrap);
      if (sites.length) load(current, 28);
      else fill(results, h("div", { style: "height:28px" }), emptyState("Aucune donnée importée", "Renseignez votre propriété Search Console ci-dessus pour importer vos requêtes.", null));
    }).catch(fail);
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
      f.sort(function (a, b) { var va = state.sort === "change" ? (change(a) || 0) : a[state.sort], vb = state.sort === "change" ? (change(b) || 0) : b[state.sort]; return ((va || 0) - (vb || 0)) * state.dir || a.query.localeCompare(b.query); });
      fill(tbody, f.slice(0, 150).map(function (x) {
        var c = change(x);
        return h("tr", { class: "click", tabindex: 0, onclick: function () { showHistory(x.query); }, onkeydown: function (e) { if (e.key === "Enter") showHistory(x.query); } },
          h("td", {}, x.query), h("td", { class: "num" }, fmt(x.clicks)), h("td", { class: "num" }, fmt(x.impressions)), h("td", { class: "num" }, (x.ctr * 100).toFixed(1).replace(".", ",") + " %"),
          h("td", { class: "num" }, h("b", {}, fmt(x.position, 1))), h("td", { class: "num" }, c == null ? h("span", { class: "muted" }, "nouveau") : delta(c) || h("span", { class: "muted" }, "=")));
      }));
      if (!f.length) tbody.append(h("tr", {}, h("td", { colspan: 6, class: "muted" }, "Aucune requête ne correspond.")));
    }
    function th(label, key, num) {
      return h("th", { class: num ? "num" : "", "aria-sort": state.sort === key ? (state.dir > 0 ? "ascending" : "descending") : "none" },
        h("button", { type: "button", class: "pill", style: "border:0;padding:0;background:none;font:inherit;text-transform:inherit;letter-spacing:inherit;color:inherit", onclick: function () { state.dir = state.sort === key ? -state.dir : -1; state.sort = key; render(); } }, label + (state.sort === key ? (state.dir > 0 ? " ↑" : " ↓") : "")));
    }
    function showHistory(query) {
      fill(hist, h("div", { class: "card" }, h("span", { class: "spinner" })));
      api("/api/positions/history?site=" + encodeURIComponent(d.site) + "&query=" + encodeURIComponent(query)).then(function (rows) {
        fill(hist, h("div", { class: "card blue" }, h("h3", { style: "margin-bottom:14px" }, "« " + query + " » — position moyenne"),
          lineChart(rows.map(function (r) { return { x: new Date(r.date), y: r.position }; }), { invert: true, color: "#0b0b0b", height: 200, label: "Position de " + query }),
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
        h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, th("Requête", "query"), th("Clics", "clicks", 1), th("Impr.", "impressions", 1), th("CTR", "ctr", 1), th("Position", "position", 1), th("Évolution", "change", 1))), tbody))));
  }

  /* ---------- vue : mots-clés ---------- */
  function viewKeywords() {
    var KIND = { suggestion: "Suggestions", question: "Questions", variante: "Variantes", search_console: "Vos requêtes réelles" };
    var out = h("div"), err = h("div"), seed = h("input", { type: "text", id: "kw-seed", placeholder: "ex. chaussures de randonnée", autocomplete: "off", style: "flex:1;min-width:240px" });
    var site = h("input", { type: "text", id: "kw-site", placeholder: "Propriété Search Console (facultatif)", value: sessionStorage.getItem("pos-site") || "" });
    var btn = h("button", { class: "btn btn-dark", type: "submit" }, "Trouver des idées");
    var form = h("form", { class: "form-row", onsubmit: function (e) {
      e.preventDefault(); btn.disabled = true; fill(err, ); fill(out, h("div", { class: "empty" }, h("span", { class: "spinner" }), h("p", { class: "muted" }, "Interrogation de l'autocomplétion… environ 15 secondes.")));
      api("/api/keywords", { seed: seed.value, site: site.value || null }).then(function (ideas) { renderIdeas(ideas); })
        .catch(function (e2) { fill(out, ); fill(err, h("div", { class: "error", role: "alert" }, e2.message)); }).then(function () { btn.disabled = false; });
    } }, h("div", { class: "field", style: "flex:2;min-width:260px" }, h("label", { for: "kw-seed" }, "Mot-clé de départ"), seed), h("div", { class: "field" }, h("label", { for: "kw-site" }, "Ajouter vos requêtes réelles"), site), btn);
    function renderIdeas(ideas) {
      var state = { kind: "" }, chips = h("div", { class: "chips" }), pills = h("div", { class: "pills", role: "group", "aria-label": "Type d'idée" });
      var counts = {}; ideas.forEach(function (i) { counts[i.kind] = (counts[i.kind] || 0) + 1; });
      function draw() {
        fill(pills, h("button", { class: "pill", type: "button", "aria-pressed": String(!state.kind), onclick: function () { state.kind = ""; draw(); } }, "Toutes", h("span", { class: "n" }, ideas.length)),
          Object.keys(counts).map(function (k) { return h("button", { class: "pill", type: "button", "aria-pressed": String(state.kind === k), onclick: function () { state.kind = k; draw(); } }, KIND[k] || k, h("span", { class: "n" }, counts[k])); }));
        fill(chips, ideas.filter(function (i) { return !state.kind || i.kind === state.kind; }).map(function (i) {
          return h("button", { class: "chip " + i.kind, type: "button", title: "Copier", onclick: function (e) { navigator.clipboard && navigator.clipboard.writeText(i.idea); e.currentTarget.style.outline = "2px solid var(--green)"; } }, i.idea);
        }));
      }
      draw();
      fill(out, ideas.length ? h("div", {}, h("header", { class: "section", style: "margin:36px 0 18px" }, h("h2", {}, ideas.length + " idées"),
        h("button", { class: "btn btn-small", type: "button", onclick: function () { csv([["idee", "type"]].concat(ideas.map(function (i) { return [i.idea, KIND[i.kind] || i.kind]; })), "idees-mots-cles.csv"); } }, "Exporter CSV")), pills, h("div", { style: "height:18px" }), chips)
        : emptyState("Aucune idée trouvée", "Essayez un mot-clé plus courant.", null));
    }
    fill(main, h("section", { class: "hero", style: "margin-bottom:28px" }, h("p", { class: "eyebrow" }, "Recherche de mots-clés"), h("h1", {}, "Ce que cherchent vraiment les internautes."),
      h("p", {}, "Idées issues de l'autocomplétion de Google, regroupées par type, et croisées avec vos propres requêtes Search Console.")),
      h("div", { class: "card" }, form, h("p", { class: "note", style: "margin-top:14px" }, "Outil gratuit : il propose des idées, sans volume de recherche. Cliquez sur une idée pour la copier."), err), out);
  }

  /* ---------- vue : concurrents ---------- */
  var METRICS = [
    ["pages", "Pages analysées", 0, 0], ["sitemap_urls", "URLs du sitemap", 0, 0], ["avg_title_length", "Longueur moyenne du title", 1, null],
    ["pct_with_description", "Pages avec meta description (%)", 1, 1], ["pct_single_h1", "Pages avec un seul H1 (%)", 1, 1],
    ["avg_word_count", "Mots par page (moyenne)", 1, 1], ["pct_with_schema", "Pages avec données structurées (%)", 1, 1],
    ["pct_with_hreflang", "Pages avec hreflang (%)", 1, 1], ["avg_ttfb_ms", "Temps de réponse serveur (ms)", 1, -1]];
  function viewCompetitors(key) {
    loading();
    api("/api/sites").then(function (sites) {
      if (!key) {
        fill(main, h("section", { class: "hero" }, h("p", { class: "eyebrow" }, "Concurrents"), h("h1", {}, "Où vous situez-vous face aux autres ?"), h("p", {}, "Choisissez un projet pour le comparer à des sites concurrents.")),
          sites.length ? h("div", { class: "grid" }, sites.map(function (s, i) { return h("a", { class: "card " + CARD_TINTS[i % 6], href: "#/concurrents/" + encodeURIComponent(s.key) }, h("h3", {}, s.key), h("p", { class: "muted" }, "Comparer →")); }))
            : emptyState("Lancez d'abord un audit", "La comparaison part de l'audit de votre propre site.", h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(); } }, "Lancer un audit")));
        return;
      }
      api("/api/competitors/" + encodeURIComponent(key)).then(function (d) {
        var err = h("div"), url = h("input", { type: "text", id: "cp-url", placeholder: "www.concurrent.fr" });
        var form = h("form", { class: "form-row", onsubmit: function (e) {
          e.preventDefault();
          startJob("/api/competitors", { site: key, url: url.value, max_pages: 30 }, "Analyse du concurrent", function () { viewCompetitors(key); }, err);
        } }, h("div", { class: "field" }, h("label", { for: "cp-url" }, "Ajouter un concurrent"), url), h("button", { class: "btn btn-dark", type: "submit" }, "Analyser"));
        // dernier instantané par concurrent
        var latest = {}; d.snapshots.forEach(function (s) { if (!latest[s.competitor]) latest[s.competitor] = s; });
        var cols = Object.values(latest);
        var table = null;
        if (d.own && cols.length) {
          var all = [{ name: key + " (vous)", p: d.own }].concat(cols.map(function (c) { return { name: c.competitor.replace(/^https?:\/\//, "").replace(/\/$/, ""), p: c.profile, date: c.created }; }));
          table = h("div", { class: "table-wrap cmp" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Indicateur"), all.map(function (c) { return h("th", { class: "num" }, c.name); }))),
            h("tbody", {}, METRICS.map(function (m) {
              var vals = all.map(function (c) { return c.p[m[0]]; }), best = null;
              if (m[3] != null && m[3] !== 0) { var ok = vals.filter(function (v) { return v != null; }); if (ok.length > 1 && Math.max.apply(null, ok) !== Math.min.apply(null, ok)) best = m[3] > 0 ? Math.max.apply(null, ok) : Math.min.apply(null, ok); }
              return h("tr", {}, h("td", {}, m[1]), vals.map(function (v) { return h("td", { class: "num" + (best != null && v === best ? " best" : "") }, fmt(v, 1)); }));
            }).concat(h("tr", {}, h("td", {}, "Types de données structurées"), all.map(function (c) { return h("td", { class: "num" }, Object.keys(c.p.schema_types || {}).slice(0, 5).join(", ") || "–"); }))))));
        }
        fill(main, h("a", { class: "crumb", href: "#/concurrents" }, "← Concurrents"),
          h("section", { class: "hero", style: "margin:16px 0 28px" }, h("p", { class: "eyebrow" }, "Comparaison"), h("h1", { style: "font-size:clamp(2rem,4vw,3rem)" }, key)),
          h("div", { class: "card" }, form, h("p", { class: "note", style: "margin-top:14px" }, "Exploration légère (30 pages au plus, 2 requêtes à la fois, robots.txt respecté). Seules les pages publiques sont lues."), err),
          h("div", { style: "height:24px" }),
          !d.own ? emptyState("Aucun audit pour ce projet", "Lancez un audit de votre site pour pouvoir le comparer.", h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(key); } }, "Lancer un audit"))
            : table || emptyState("Aucun concurrent analysé", "Ajoutez l'adresse d'un concurrent ci-dessus.", null),
          table ? h("p", { class: "note", style: "margin-top:14px" }, "Les cases vertes indiquent la meilleure valeur lorsqu'une comparaison a un sens.") : null);
      });
    }).catch(fail);
  }

  /* ---------- routage ---------- */
  function route() {
    var hash = location.hash.replace(/^#\/?/, ""), qs = new URLSearchParams(hash.split("?")[1] || "");
    var parts = hash.split("?")[0].split("/").map(decodeURIComponent), name = parts[0] || "home";
    document.querySelectorAll("#nav a").forEach(function (a) {
      var active = a.dataset.route === (name === "site" || name === "audit" ? "home" : name === "mots-cles" ? "keywords" : name === "concurrents" ? "competitors" : name);
      if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    window.scrollTo(0, 0);
    if (name === "site" && parts[1]) viewSite(parts[1]);
    else if (name === "audit" && parts[1]) viewAudit(parts[1]);
    else if (name === "positions") viewPositions(qs.get("google"));
    else if (name === "mots-cles") viewKeywords();
    else if (name === "concurrents") viewCompetitors(parts[1]);
    else viewHome();
    main.focus({ preventScroll: true });
  }
  window.addEventListener("hashchange", route);
  route();
})();
