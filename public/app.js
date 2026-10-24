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
  }
  var nf = new Intl.NumberFormat("fr-FR");
  function fmt(n, d) { return n == null || n === "" ? "–" : (d ? Number(n).toLocaleString("fr-FR", { maximumFractionDigits: d }) : nf.format(Math.round(n))); }
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
      svg("circle", { cx: 50, cy: 50, r: r, fill: "none", stroke: "rgba(0,0,0,.09)", "stroke-width": 10 }),
      svg("circle", { cx: 50, cy: 50, r: r, fill: "none", stroke: scoreColor(score), "stroke-width": 10, "stroke-linecap": "round",
        "stroke-dasharray": (c * score / 100) + " " + c })), h("span", { class: "v" }, score));
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
    return svg("svg", { width: W, height: H, viewBox: "0 0 " + W + " " + H, "aria-hidden": "true" }, svg("path", { d: d, fill: "none", stroke: "#0b0b0b", "stroke-width": 2.5, "stroke-linecap": "round", "stroke-linejoin": "round" }));
  }
  function kpi(value, label, tint) { return h("div", { class: "kpi " + (tint || "") }, h("span", { class: "v" }, value), h("span", { class: "l" }, label)); }
  function emptyState(title, text, cta) {
    return h("div", { class: "card empty" }, h("img", { src: "/img/vigie-mark.svg", alt: "", width: 96, height: 96 }), h("h2", {}, title), h("p", { class: "muted" }, text), cta);
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
        if (a.status === "done") { closeOverlay(); onDone(a); return; }
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
          h("li", {}, fmt(p.keywords_month) + " recherches de mots-clés par mois"), h("li", {}, fmt(p.competitors_month) + " analyses de concurrents par mois")),
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
        h("div", { class: "hero-art", "aria-hidden": "true" }, h("img", { src: "/img/vigie-mark.svg", alt: "" }),
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
          h("div", { class: "form-row" }, h("a", { class: "btn btn-dark", href: "#/audit/" + last.id }, "Voir les problèmes"),
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
                  api("/api/audits/" + a.id, undefined, "DELETE").then(function () { viewSite(key); }).catch(function (e) { alert(e.message); });
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
          h("button", { class: "btn btn-small", type: "button", onclick: function () { download("/api/audits/" + id + "/report.html", "rapport-" + slug + ".html").catch(function (e) { alert(e.message); }); } }, "Rapport HTML"),
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
            syncStatus.textContent = fmt(total) + " lignes importées.";
            viewPositions();
          }).catch(function (x) { syncBtn.disabled = false; syncStatus.textContent = ""; fill(err, errorBox(x)); });
        })(0);
      } }, h("div", { style: "display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center" },
          h("h3", {}, "Importer une propriété"),
          h("span", { class: "muted" }, "Connecté : " + (st.email || "compte Google") + " ",
            h("button", { class: "btn btn-small", type: "button", onclick: function () { api("/api/google/disconnect", {}).then(function () { viewPositions(); }); } }, "Déconnecter"))),
        h("div", { class: "form-row" }, h("div", { class: "field" }, h("label", { for: "ps-site" }, "Propriété"), sel),
          h("div", { class: "field", style: "flex:0 0 150px;min-width:130px" }, h("label", { for: "ps-days" }, "Historique"), days), syncBtn),
        h("p", { class: "note" }, "Ensuite, les propriétés importées sont mises à jour automatiquement chaque jour. Votre offre permet d'en suivre " + st.max_properties + "."), syncStatus, err);

      var results = h("div");
      function load(site, n) {
        fill(results, h("div", { class: "empty" }, h("span", { class: "spinner" })));
        api("/api/positions/summary?site=" + encodeURIComponent(site) + "&days=" + n).then(function (d) { renderPositions(results, d); }).catch(function (e) { fill(results, errorBox(e)); });
      }
      var picker = tracked.length ? h("div", { class: "form-row", style: "margin:28px 0 20px" },
        h("div", { class: "field", style: "max-width:420px" }, h("label", { for: "ps-pick" }, "Propriété suivie"),
          h("select", { id: "ps-pick", onchange: function (e) { sessionStorage.setItem("pos-site", e.target.value); load(e.target.value, 28); } },
            tracked.map(function (s) { return h("option", { value: s, selected: s === current }, s); })))) : null;
      fill(main, head, msg, form, picker, results);
      if (current) load(current, 28);
      else fill(results, h("div", { style: "height:28px" }), emptyState("Aucune donnée importée", "Choisissez une propriété ci-dessus et lancez l'import.", null));
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
  function viewKeywords() {
    var KIND = { suggestion: "Suggestions", question: "Questions", variante: "Variantes", search_console: "Vos requêtes réelles" };
    var out = h("div"), err = h("div"), seed = h("input", { type: "text", id: "kw-seed", placeholder: "ex. chaussures de randonnée", autocomplete: "off", required: true });
    var btn = h("button", { class: "btn btn-dark", type: "submit" }, "Trouver des idées");
    var form = h("form", { class: "form-row", onsubmit: function (e) {
      e.preventDefault(); btn.disabled = true; fill(err); fill(out, h("div", { class: "empty" }, h("span", { class: "spinner" }), h("p", { class: "muted" }, "Interrogation de l'autocomplétion… quelques secondes.")));
      api("/api/keywords", { seed: seed.value, site: sessionStorage.getItem("pos-site") || null }).then(function (ideas) { refreshMe(); renderIdeas(ideas); })
        .catch(function (x) { fill(out); fill(err, errorBox(x)); }).then(function () { btn.disabled = false; });
    } }, h("div", { class: "field", style: "flex:3;min-width:260px" }, h("label", { for: "kw-seed" }, "Mot-clé de départ"), seed), btn);
    function renderIdeas(ideas) {
      var state = { kind: "" }, chips = h("div", { class: "chips" }), pills = h("div", { class: "pills", role: "group", "aria-label": "Type d'idée" });
      var counts = {}; ideas.forEach(function (i) { counts[i.kind] = (counts[i.kind] || 0) + 1; });
      function draw() {
        fill(pills, h("button", { class: "pill", type: "button", "aria-pressed": String(!state.kind), onclick: function () { state.kind = ""; draw(); } }, "Toutes", h("span", { class: "n" }, ideas.length)),
          Object.keys(counts).map(function (k) { return h("button", { class: "pill", type: "button", "aria-pressed": String(state.kind === k), onclick: function () { state.kind = k; draw(); } }, KIND[k] || k, h("span", { class: "n" }, counts[k])); }));
        fill(chips, ideas.filter(function (i) { return !state.kind || i.kind === state.kind; }).map(function (i) {
          return h("button", { class: "chip " + i.kind, type: "button", title: "Copier", onclick: function (e) { if (navigator.clipboard) navigator.clipboard.writeText(i.idea); e.currentTarget.style.outline = "2px solid var(--green)"; } }, i.idea);
        }));
      }
      draw();
      fill(out, ideas.length ? h("div", {}, h("header", { class: "section", style: "margin:36px 0 18px;display:flex;justify-content:space-between;align-items:end;gap:16px;flex-wrap:wrap" }, h("h2", {}, ideas.length + " idées"),
        h("button", { class: "btn btn-small", type: "button", onclick: function () { csv([["idee", "type"]].concat(ideas.map(function (i) { return [i.idea, KIND[i.kind] || i.kind]; })), "idees-mots-cles.csv"); } }, "Exporter CSV")), pills, h("div", { style: "height:18px" }), chips)
        : emptyState("Aucune idée trouvée", "Essayez un mot-clé plus courant.", null));
    }
    fill(main, h("section", { class: "hero", style: "margin-bottom:28px" }, h("p", { class: "eyebrow" }, "Recherche de mots-clés"), h("h1", {}, "Ce que cherchent vraiment les internautes."),
      h("p", {}, "Idées issues de l'autocomplétion de Google, regroupées par type, et croisées avec vos requêtes Search Console.")),
      h("div", { class: "card" }, form, h("p", { class: "note", style: "margin-top:14px" }, "Des idées, sans volume de recherche. Cliquez sur une idée pour la copier." + (ME ? " " + fmt(ME.usage.keywords) + " / " + fmt(ME.limits.keywords_month) + " recherches ce mois-ci." : "")), err), out);
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
            meter("Audits", U.audit, L.audits_month), meter("Analyses de concurrents", U.competitor, L.competitors_month), meter("Recherches de mots-clés", U.keywords, L.keywords_month),
            h("p", { class: "note" }, "Jusqu'à " + fmt(L.max_pages) + " pages par audit et " + L.running + " audit(s) en parallèle.")),
          h("div", { class: "card blue", style: "display:grid;gap:14px;align-content:start;justify-items:start" }, h("h3", {}, "Search Console"),
            h("p", {}, ME.google ? "Connectée." : "Non connectée."), h("a", { class: "btn btn-small", href: "#/positions" }, ME.google ? "Gérer" : "Connecter"),
            h("button", { class: "btn btn-small", type: "button", onclick: function () { sb.auth.signOut(); } }, "Se déconnecter"))),
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
  function renderTop() {
    var nav = document.getElementById("nav"), actions = document.getElementById("top-actions");
    nav.hidden = !SESSION;
    if (SESSION) fill(actions, h("button", { class: "btn btn-dark", type: "button", onclick: function () { newAuditModal(); } }, "Nouvel audit"),
      h("a", { class: "btn btn-small", href: "#/compte", "aria-label": "Mon compte" }, "Compte"));
    else fill(actions, h("button", { class: "btn btn-dark", type: "button", onclick: signIn }, "Se connecter"));
  }
  function route() {
    var hash = location.hash.replace(/^#\/?/, ""), qs = new URLSearchParams(hash.split("?")[1] || "");
    var parts = hash.split("?")[0].split("/").map(decodeURIComponent), name = parts[0] || "home";
    closeOverlay();
    window.scrollTo(0, 0);
    if (!SESSION) { viewLanding(name === "offres"); main.focus({ preventScroll: true }); return; }
    document.querySelectorAll("#nav a").forEach(function (a) {
      var active = a.dataset.route === ({ site: "home", audit: "home", "mots-cles": "keywords", concurrents: "competitors" }[name] || name);
      if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    if (name === "site" && parts[1]) viewSite(parts[1]);
    else if (name === "audit" && parts[1]) viewAudit(parts[1]);
    else if (name === "positions") viewPositions(qs.get("google"));
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
