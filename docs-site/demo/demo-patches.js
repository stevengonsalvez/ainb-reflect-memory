/*
 * Demo-only glue, loaded AFTER the real SPA script (see scripts/build-demo.mjs).
 *
 * The real UI fetches /api/graph and /api/stats once at boot, so after an
 * archive/restore/confidence edit those two views stay stale until a reload
 * (against a real server too). In the demo we want every view to reflect a
 * change immediately, so wrap the SPA's reloadMems() (called after each
 * successful mutation) to also refresh stats and the graph.
 *
 * It also fixes one cosmetic quirk: notes stamped by the drain carry a mapping
 * for `provenance`, which the stock drawer prints as "[object Object]".
 *
 * Classic scripts share one global scope, so the SPA's top-level functions and
 * `let` bindings (reloadMems, api, renderStats, initGraph, GRAPH, showTab) are
 * reachable here. Everything is guarded: if the frontend changes shape this
 * file does nothing and the demo simply behaves like the stock UI.
 */
(function () {
  "use strict";
  try {
    if (
      typeof reloadMems !== "function" || typeof api !== "function" ||
      typeof renderStats !== "function" || typeof initGraph !== "function" ||
      typeof showTab !== "function"
    ) return;
    var original = reloadMems;
    reloadMems = async function () {
      await original.apply(this, arguments);
      try {
        api("/api/stats").then(renderStats);
        var g = await api("/api/graph");
        GRAPH = initGraph(g);
        var wrap = document.getElementById("graphwrap");
        if (wrap && wrap.style.display === "block") showTab("graph");
      } catch (e) { /* keep the stock behaviour */ }
    };
  } catch (e) { /* frontend shape changed: no-op */ }
})();

(function () {
  "use strict";
  try {
    if (typeof openDetail !== "function" || typeof api !== "function") return;
    var originalOpen = openDetail;
    openDetail = async function (id) {
      await originalOpen.apply(this, arguments);
      try {
        var m = await api("/api/memories/" + encodeURIComponent(id));
        if (!m.provenance || typeof m.provenance !== "object") return;
        var text = Object.keys(m.provenance).map(function (k) { return k + ": " + m.provenance[k]; }).join(" \u00b7 ");
        document.querySelectorAll("#d-body dd").forEach(function (dd) {
          if (dd.textContent === "[object Object]") dd.textContent = text;
        });
      } catch (e) { /* cosmetic only */ }
    };
  } catch (e) { /* frontend shape changed: no-op */ }
})();
