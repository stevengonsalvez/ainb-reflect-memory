/*
 * reflect serve, in the browser.
 *
 * Replaces the `reflect serve` HTTP API for the docs-site demo. The real SPA
 * (src/reflect_kb/cli/serve_static/index.html) runs unmodified; this script
 * intercepts window.fetch for any URL whose path contains "/api/" (matched by
 * path SUFFIX, so it works whether the demo is hosted at / or under
 * /ainb-reflect-memory/demo/) and answers from a static snapshot
 * (api-snapshot.json, produced by scripts/snapshot-api.py from the real
 * serve.py). The SPA only uses fetch(), so XMLHttpRequest is not patched.
 *
 * Mutations (archive, restore, confidence, compress-queue) live in memory only
 * and every derived view (memories, detail.related, graph, stats, archived,
 * search index) is recomputed from one state object, so views stay consistent.
 * A page reload resets everything.
 *
 * The derivation functions are ports of src/reflect_kb/serve.py
 * (KnowledgeBase._load/_build_search_index/search/graph/stats/_related/
 * _browse_score). Keep them in lockstep; scripts/test-mock-search.mjs compares
 * their output with python-generated fixtures.
 *
 * Works as a classic <script> in the browser and, via `module.exports`, in
 * node (the test loads it through vm).
 */
(function () {
  "use strict";

  var CONF_WEIGHT = { high: 1.0, medium: 0.7, low: 0.4 };
  var HALF_LIFE_DAYS = 180.0;
  var VALID_CONFIDENCE = ["high", "medium", "low"];

  // ---------- small python-compat helpers ----------

  /** Python's round(x, 3): exact-decimal, ties to even (toFixed ties go up). */
  function round3(x) {
    var s = x.toFixed(30);
    var dot = s.indexOf(".");
    var frac = s.slice(dot + 1);
    if (/^50*$/.test(frac.slice(3))) {
      var floor = Number(s.slice(0, dot + 4));
      var lastDigit = Number(s.charAt(dot + 3));
      return lastDigit % 2 === 0 ? floor : Number((floor + 0.001).toFixed(3));
    }
    return Number(x.toFixed(3));
  }

  function tokenize(text) {
    return String(text).toLowerCase().match(/[a-z0-9_./-]{2,}/g) || [];
  }

  function cmpStr(a, b) {
    return a < b ? -1 : a > b ? 1 : 0;
  }

  function clone(o) {
    return JSON.parse(JSON.stringify(o));
  }

  /** Counter(...).most_common(n): count desc, ties by first insertion. */
  function mostCommon(counter, n) {
    var entries = Object.keys(counter).map(function (k, i) {
      return [k, counter[k], i];
    });
    entries.sort(function (a, b) {
      return b[1] - a[1] || a[2] - b[2];
    });
    var out = {};
    entries.slice(0, n === undefined ? entries.length : n).forEach(function (e) {
      out[e[0]] = e[1];
    });
    return out;
  }

  function count(values) {
    var c = {};
    values.forEach(function (v) {
      c[v] = (c[v] || 0) + 1;
    });
    return c;
  }

  // ---------- the model ----------

  function createMockApi(snapshot) {
    var nowMs = Date.parse(snapshot.meta.now);
    var ep = snapshot.endpoints;

    // Master doc table: every note that can ever be live, sorted like
    // sorted(documents_dir.glob("*.md")), with a live/archived flag.
    var docs = {};
    ep.memories.forEach(function (m) {
      var d = clone(m);
      delete d.browse_score;
      var det = ep.memory[m.id] || {};
      d.body = det.body || "";
      d.entities = det.entities || [];
      d.relationships = det.relationships || [];
      d.archived = false;
      docs[m.id] = d;
    });
    Object.keys(snapshot.restorable || {}).forEach(function (id) {
      var d = clone(snapshot.restorable[id]);
      d.archived = true;
      docs[id] = d;
    });

    var state = {
      gen: 0, // bumps whenever the live set changes (search index key)
      queue: clone(ep.compress_queue),
    };
    var index = null; // { gen, tokens, df, avgLen }

    function allSorted() {
      return Object.keys(docs)
        .map(function (id) {
          return docs[id];
        })
        .sort(function (a, b) {
          return cmpStr(a.file, b.file);
        });
    }
    function live() {
      return allSorted().filter(function (d) {
        return !d.archived;
      });
    }

    // ----- serve.py: _browse_score -----
    function browseScore(d, terms) {
      var conf = CONF_WEIGHT[d.confidence] !== undefined ? CONF_WEIGHT[d.confidence] : 0.55;
      var s = String(d.date).replace(" ", "T");
      if (s.length === 10) s += "T00:00:00";
      var t = Date.parse(s + "Z");
      var age = isNaN(t) ? 365 : Math.floor((nowMs - t) / 86400000);
      var recency = Math.exp(-Math.max(age, 0) / HALF_LIFE_DAYS);
      var overlap = 1.0;
      if (terms) {
        var tagset = {};
        d.tags.forEach(function (x) {
          tagset[String(x).toLowerCase()] = true;
        });
        var hits = terms.filter(function (x) {
          return tagset[x];
        }).length;
        overlap = 1.0 + 0.5 * hits;
      }
      return conf * recency * overlap;
    }

    function listItem(d) {
      // The doc dict as /api/memories returns it (no body/entities).
      var item = {};
      Object.keys(d).forEach(function (k) {
        if (k === "body" || k === "entities" || k === "relationships" || k === "archived") return;
        item[k] = d[k];
      });
      return item;
    }

    // ----- serve.py: _build_search_index -----
    function buildIndex() {
      if (index && index.gen === state.gen) return index;
      var tokens = {};
      var df = {};
      var total = 0;
      var ld = live();
      ld.forEach(function (d) {
        var head = d.title + " " + d.tags.join(" ");
        var toks = {};
        tokenize(head + " " + (d.body || "")).forEach(function (t) {
          toks[t] = (toks[t] || 0) + 1;
        });
        tokenize(head).forEach(function (t) {
          toks[t] = (toks[t] || 0) + 2;
        });
        tokens[d.id] = toks;
        Object.keys(toks).forEach(function (t) {
          df[t] = (df[t] || 0) + 1;
          total += toks[t];
        });
      });
      index = {
        gen: state.gen,
        tokens: tokens,
        df: df,
        avgLen: ld.length ? total / ld.length : 1.0,
      };
      return index;
    }

    // ----- serve.py: KnowledgeBase public API -----

    function memories() {
      return live().map(function (d) {
        var item = listItem(d);
        item.browse_score = round3(browseScore(d));
        return item;
      });
    }

    function related(d, ld) {
      var mineTags = {};
      d.tags.forEach(function (t) {
        mineTags[t] = true;
      });
      var mineEnts = {};
      d.entity_names.forEach(function (t) {
        mineEnts[t] = true;
      });
      var scored = [];
      ld.forEach(function (other) {
        if (other.id === d.id) return;
        var otherTags = {};
        other.tags.forEach(function (t) {
          otherTags[t] = true;
        });
        var otherEnts = {};
        other.entity_names.forEach(function (t) {
          otherEnts[t] = true;
        });
        var s =
          3 * Object.keys(mineTags).filter(function (t) { return otherTags[t]; }).length +
          Object.keys(mineEnts).filter(function (t) { return otherEnts[t]; }).length;
        if (other.superseded_by === d.id || d.superseded_by === other.id) s += 10;
        if (s > 0) scored.push([s, other]);
      });
      scored.sort(function (a, b) {
        return b[0] - a[0] || cmpStr(a[1].title, b[1].title);
      });
      return scored.slice(0, 6).map(function (p) {
        return { id: p[1].id, title: p[1].title, confidence: p[1].confidence, shared: p[0] };
      });
    }

    function memory(id) {
      var ld = live();
      var d = null;
      ld.forEach(function (x) {
        if (x.id === id) d = x;
      });
      if (!d) return null;
      var item = listItem(d);
      item.body = d.body || "";
      item.entities = d.entities;
      item.relationships = d.relationships;
      item.related = related(d, ld);
      item.browse_score = round3(browseScore(d));
      return item;
    }

    function search(query, limit) {
      limit = limit || 25;
      var terms = tokenize(query);
      if (!terms.length) return [];
      var idx = buildIndex();
      var ld = live();
      var nDocs = Math.max(ld.length, 1);
      var k1 = 1.4, b = 0.6;
      var scored = [];
      ld.forEach(function (d) {
        var toks = idx.tokens[d.id] || {};
        var dl = 0;
        Object.keys(toks).forEach(function (t) {
          dl += toks[t];
        });
        dl = dl || 1;
        var score = 0.0;
        terms.forEach(function (term) {
          var tf = toks[term] || 0;
          if (!tf) return;
          var dfv = idx.df[term];
          var idf = Math.log(1 + (nDocs - dfv + 0.5) / (dfv + 0.5));
          score += (idf * tf * (k1 + 1)) / (tf + k1 * (1 - b + (b * dl) / idx.avgLen));
        });
        if (score > 0) {
          var item = listItem(d);
          item.match_score = round3(score);
          item.browse_score = round3(browseScore(d, terms));
          scored.push([score, item]);
        }
      });
      scored.sort(function (a, b2) {
        return b2[0] - a[0];
      });
      return scored.slice(0, limit).map(function (p) {
        return p[1];
      });
    }

    function graph() {
      var ld = live();
      var nodes = {};
      var order = [];
      var edges = [];
      function put(id, node) {
        nodes[id] = node;
        order.push(id);
      }
      ld.forEach(function (d) {
        put("m:" + d.id, {
          id: "m:" + d.id, label: d.title, kind: "memory", confidence: d.confidence,
          type: d.type, doc: d.id, score: browseScore(d),
        });
      });
      ld.forEach(function (d) {
        d.entities.forEach(function (e) {
          var name = String(e.name === undefined || e.name === null ? "" : e.name).trim();
          if (!name) return;
          var key = "e:" + name.toLowerCase();
          if (!nodes[key]) {
            put(key, {
              id: key, label: name, kind: "entity",
              type: String(e.type === undefined || e.type === null ? "concept" : e.type),
            });
          }
          edges.push({ s: "m:" + d.id, t: key, w: 1.0, kind: "mention" });
        });
      });
      (snapshot.relations || []).forEach(function (r) {
        if (nodes[r.s] && nodes[r.t] && r.s !== r.t) {
          edges.push({ s: r.s, t: r.t, w: r.w, kind: "relation" });
        }
      });
      var degree = {};
      edges.forEach(function (e) {
        degree[e.s] = (degree[e.s] || 0) + 1;
        degree[e.t] = (degree[e.t] || 0) + 1;
      });
      return {
        nodes: order.map(function (id) {
          var n = nodes[id];
          n.degree = degree[id] || 0;
          return n;
        }),
        edges: edges,
      };
    }

    function stats() {
      var ld = live();
      return {
        documents: ld.length,
        repo: ep.stats.repo,
        confidence: count(ld.map(function (d) { return d.confidence; })),
        types: mostCommon(count(ld.map(function (d) { return d.type; }))),
        scopes: mostCommon(count(ld.map(function (d) { return d.scope; }))),
        top_tags: mostCommon(
          count([].concat.apply([], ld.map(function (d) { return d.tags; }))), 20),
        metrics_ops: clone(ep.stats.metrics_ops),
        metrics_errors: ep.stats.metrics_errors,
        with_sidecars: ld.filter(function (d) { return d.entity_count; }).length,
      };
    }

    function archivedList() {
      return allSorted()
        .filter(function (d) { return d.archived; })
        .map(function (d) {
          return { id: d.id, title: d.title, confidence: d.confidence, type: d.type, scope: d.scope };
        });
    }

    // ----- curation (mutations, in memory) -----

    function MutationError(msg) {
      this.message = msg;
    }

    function archive(id) {
      var d = docs[id];
      if (!d || d.archived) throw new MutationError("unknown memory: " + id);
      d.archived = true;
      state.gen++;
      // _dequeue_from_compress
      var changed = false;
      state.queue.groups.forEach(function (g) {
        if ((g.ids || []).indexOf(id) !== -1) {
          g.ids = g.ids.filter(function (i) { return i !== id; });
          changed = true;
        }
      });
      if (changed) {
        state.queue.groups = state.queue.groups.filter(function (g) { return g.ids.length >= 2; });
      }
      return { ok: true, id: id, archived: true, graph_index_stale: true };
    }

    function restore(id) {
      var d = docs[id];
      if (!d || !d.archived) throw new MutationError("not in archive: " + id);
      d.archived = false;
      state.gen++;
      return { ok: true, id: id, archived: false, graph_index_stale: true };
    }

    function setConfidence(id, value) {
      value = String(value === undefined || value === null ? "" : value).trim().toLowerCase();
      if (VALID_CONFIDENCE.indexOf(value) === -1) {
        throw new MutationError("confidence must be one of ('high', 'medium', 'low')");
      }
      var d = docs[id];
      if (!d || d.archived) throw new MutationError("unknown memory: " + id);
      d.confidence = value;
      return { ok: true, id: id, confidence: value, graph_index_stale: true };
    }

    function queueCompress(ids) {
      var liveIds = {};
      live().forEach(function (d) { liveIds[d.id] = true; });
      var seen = {};
      var picked = (Array.isArray(ids) ? ids : []).filter(function (i) {
        if (seen[i] || !liveIds[i]) return false;
        seen[i] = true;
        return true;
      });
      if (picked.length < 2) throw new MutationError("compress needs at least two live memories");
      state.queue.groups.push({ ids: picked, queued_at: new Date().toISOString(), status: "pending" });
      return { ok: true, queued: picked, groups: state.queue.groups.length };
    }

    // ----- HTTP-ish routing (mirrors serve.py do_GET / do_POST) -----

    function route(method, pathname, query, body, headers) {
      var after = pathname.slice(pathname.indexOf("/api/") + 1); // "api/..."
      var parts = after.split("/").filter(Boolean).map(function (p) {
        try { return decodeURIComponent(p); } catch (e) { return p; }
      });
      var ok = function (obj) { return { status: 200, body: obj }; };
      var err = function (code, msg) { return { status: code, body: { error: msg } }; };

      if (method === "GET") {
        if (parts.length === 2 && parts[1] === "memories") return ok(memories());
        if (parts.length === 3 && parts[1] === "memories") {
          var m = memory(parts[2]);
          return m ? ok(m) : err(404, "not found");
        }
        if (parts.length === 2 && parts[1] === "search") {
          return ok(search(query.get("q") || ""));
        }
        if (parts.length === 2 && parts[1] === "graph") return ok(graph());
        if (parts.length === 2 && parts[1] === "stats") return ok(stats());
        if (parts.length === 2 && parts[1] === "archived") return ok(archivedList());
        if (parts.length === 2 && parts[1] === "compress-queue") return ok(clone(state.queue));
        return err(404, "not found");
      }
      if (method === "POST") {
        var hasCsrf = false;
        Object.keys(headers || {}).forEach(function (k) {
          if (k.toLowerCase() === "x-reflect" && headers[k]) hasCsrf = true;
        });
        if (!hasCsrf) return err(403, "forbidden: missing X-Reflect header");
        try {
          if (parts.length === 4 && parts[1] === "memories") {
            if (parts[3] === "archive") return ok(archive(parts[2]));
            if (parts[3] === "restore") return ok(restore(parts[2]));
            if (parts[3] === "confidence") return ok(setConfidence(parts[2], (body || {}).value));
            return err(404, "unknown action");
          }
          if (parts.length === 2 && parts[1] === "compress-queue") {
            return ok(queueCompress((body || {}).ids));
          }
          return err(404, "not found");
        } catch (e) {
          if (e instanceof MutationError) return err(400, e.message);
          throw e;
        }
      }
      return err(404, "not found");
    }

    return {
      route: route,
      memories: memories,
      memory: memory,
      search: search,
      graph: graph,
      stats: stats,
      archived: archivedList,
      compressQueue: function () { return clone(state.queue); },
      archive: archive,
      restore: restore,
      setConfidence: setConfidence,
      queueCompress: queueCompress,
    };
  }

  // ---------- node export (tests) ----------
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { createMockApi: createMockApi, round3: round3, tokenize: tokenize };
    return;
  }

  // ---------- browser: fetch interceptor ----------
  if (typeof window === "undefined" || !window.fetch) return;

  var script = document.currentScript;
  var base = script && script.src ? script.src : location.href;
  var snapshotUrl = new URL("api-snapshot.json", base).href;
  var nativeFetch = window.fetch.bind(window);
  var apiPromise = null;
  function getApi() {
    if (!apiPromise) {
      apiPromise = nativeFetch(snapshotUrl)
        .then(function (r) {
          if (!r.ok) throw new Error("demo snapshot failed to load: HTTP " + r.status);
          return r.json();
        })
        .then(createMockApi);
    }
    return apiPromise;
  }
  getApi().catch(function () {}); // warm up; real errors surface on first use

  function headersToObject(h) {
    var out = {};
    if (!h) return out;
    if (typeof Headers !== "undefined" && h instanceof Headers) {
      h.forEach(function (v, k) { out[k] = v; });
    } else if (Array.isArray(h)) {
      h.forEach(function (p) { out[p[0]] = p[1]; });
    } else {
      Object.keys(h).forEach(function (k) { out[k] = h[k]; });
    }
    return out;
  }

  window.fetch = function (input, init) {
    var url;
    try {
      url = new URL(typeof input === "string" ? input : input.url, location.href);
    } catch (e) {
      return nativeFetch(input, init);
    }
    // Match by path suffix: ".../api/<something>", wherever the demo is hosted.
    if (url.origin !== location.origin || url.pathname.indexOf("/api/") === -1) {
      return nativeFetch(input, init);
    }
    var method = String((init && init.method) || (typeof input !== "string" && input.method) || "GET").toUpperCase();
    var headers = headersToObject((init && init.headers) || (typeof input !== "string" && input.headers));
    var body = {};
    if (init && typeof init.body === "string") {
      try { body = JSON.parse(init.body); } catch (e) { body = {}; }
    }
    return getApi().then(function (api) {
      var res = api.route(method, url.pathname, url.searchParams, body, headers);
      return new Response(JSON.stringify(res.body), {
        status: res.status,
        headers: { "Content-Type": "application/json" },
      });
    });
  };
  window.__reflectDemo = { mocked: true };
})();
