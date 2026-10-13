/* HTTP Lab dashboard UI — vanilla JS, no dependencies.
 *
 * Simulator and auth cards are declared as data (CARDS below) and rendered by
 * one function, so every card works the same way: labeled inputs, a live
 * request line showing the exact endpoint, copy-as-URL / copy-as-curl, and a
 * result panel with status, timing, notable headers and body.
 */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  /* Small DOM builder: el("div", {class: "x"}, child, "text"). Text is always
     inserted as text nodes, so server-supplied strings can never become markup. */
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === false || value == null) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  /* ---------------- tabs (deep-linkable via #hash) ---------------- */
  /* The hash is either a tab name (#simulators) or a card id (#card-flaky);
     a card link opens the card's tab and scrolls to it. */
  function showTab(hash) {
    const tabs = $$(".tab");
    let name = hash;
    let target = null;
    if (!tabs.some((t) => t.dataset.tab === name)) {
      target = hash ? document.getElementById(hash) : null;
      const panel = target && target.closest(".panel");
      name = panel ? panel.id.replace(/^panel-/, "") : "inspector";
    }
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${name}`));
    if (target) {
      target.scrollIntoView({ block: "start" });
      target.classList.remove("flash");
      void target.offsetWidth; // restart the highlight animation
      target.classList.add("flash");
    }
  }

  /* ---------------- requests ---------------- */
  const qs = (params) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value === "" || value == null || value === false) continue;
      for (const v of Array.isArray(value) ? value : [value]) search.append(key, String(v));
    }
    // URLSearchParams escapes "," "/" ":" too; they are legal in a query and far
    // more readable in the request line people copy (codes=200,500, to=/anything).
    const text = search.toString().replace(/%2C/gi, ",").replace(/%2F/gi, "/").replace(/%3A/gi, ":");
    return text ? `?${text}` : "";
  };

  const shellQuote = (value) => `'${String(value).replace(/'/g, `'\\''`)}'`;

  function toCurl(req) {
    const parts = ["curl -i"];
    if (req.follow) parts.push("-L");
    if (req.method && req.method !== "GET") parts.push(`-X ${req.method}`);
    for (const [name, value] of Object.entries(req.headers || {})) {
      parts.push(`-H ${shellQuote(`${name}: ${value}`)}`);
    }
    if (req.body) parts.push(`--data ${shellQuote(req.body)}`);
    parts.push(shellQuote(location.origin + req.path));
    return parts.join(" ");
  }

  async function send(req) {
    const started = performance.now();
    let response;
    try {
      response = await fetch(req.path, {
        method: req.method || "GET",
        headers: req.headers || {},
        body: req.body || undefined,
        credentials: "same-origin",
      });
    } catch (err) {
      return { req, response: null, text: "", error: String(err && err.message ? err.message : err), ms: performance.now() - started };
    }
    let text = "";
    let error = null;
    try {
      text = await response.text();
    } catch (err) {
      error = `body could not be read completely (${err && err.message ? err.message : err})`;
    }
    return { req, response, text, error, ms: performance.now() - started };
  }

  /* ---------------- result rendering ---------------- */
  const NOTABLE_HEADERS = [
    "content-type", "content-length", "location", "retry-after", "x-request-id",
    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "set-cookie",
    "www-authenticate", "x-flaky-attempt", "x-redirect-hops", "x-http-lab-size-bytes",
  ];
  const BODY_PREVIEW_LIMIT = 20000;

  function statusClass(status) {
    if (status >= 500) return "err";
    if (status >= 400) return "warn";
    return "ok";
  }

  function statusLine({ req, response, error, ms }) {
    const line = el("div", { class: "result-line" },
      el("span", { class: "method" }, req.method || "GET"),
      el("code", {}, req.path),
      el("span", { class: "arrow" }, "→"));
    if (response) {
      line.append(
        el("span", { class: `status ${statusClass(response.status)}` }, `${response.status} ${response.statusText}`.trim()),
        el("span", { class: "muted" }, `${Math.round(ms)} ms`));
      if (response.redirected) line.append(el("span", { class: "muted" }, `followed redirects to ${new URL(response.url).pathname}`));
    } else {
      line.append(el("span", { class: "status err" }, "network error"), el("span", { class: "muted" }, error));
    }
    return line;
  }

  function prettyBody(text) {
    if (!text) return "";
    let body = text;
    try {
      body = JSON.stringify(JSON.parse(text), null, 2);
    } catch (_) {
      /* not JSON: show as-is */
    }
    return body.length > BODY_PREVIEW_LIMIT ? `${body.slice(0, BODY_PREVIEW_LIMIT)}\n…(truncated preview)` : body;
  }

  function renderResult(target, result, { bodyMode = "text" } = {}) {
    target.replaceChildren(statusLine(result));
    const { response, text, error } = result;
    if (!response) return;
    const headers = [];
    response.headers.forEach((value, name) => {
      if (NOTABLE_HEADERS.includes(name.toLowerCase())) headers.push(`${name}: ${value}`);
    });
    if (headers.length) target.append(el("div", { class: "section-label" }, "Headers"), el("pre", {}, headers.join("\n")));
    if (error) target.append(el("div", { class: "note warn" }, error));
    if (bodyMode === "size") {
      target.append(el("div", { class: "section-label" }, "Body"), el("pre", {}, `${new Blob([text]).size.toLocaleString()} bytes received (content not shown)`));
    } else if (text) {
      target.append(el("div", { class: "section-label" }, "Body"), el("pre", {}, prettyBody(text)));
    } else if (!error) {
      target.append(el("div", { class: "note" }, "(empty body)"));
    }
  }

  function renderBurst(target, results) {
    const rows = results.map((r, i) => {
      if (!r.response) return `#${i + 1}  network error  ${r.error}`;
      const extra = ["x-ratelimit-remaining", "retry-after", "x-flaky-attempt"]
        .map((h) => (r.response.headers.get(h) != null ? `${h}=${r.response.headers.get(h)}` : null))
        .filter(Boolean)
        .join("  ");
      return `#${i + 1}  ${r.response.status}  ${Math.round(r.ms)} ms  ${extra}`;
    });
    target.replaceChildren(
      el("div", { class: "result-line" }, el("span", { class: "method" }, results[0].req.method || "GET"), el("code", {}, results[0].req.path), el("span", { class: "muted" }, `× ${results.length}`)),
      el("pre", {}, rows.join("\n")));
  }

  /* ---------------- clipboard ---------------- */
  async function copy(text, button) {
    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch (_) {
      window.prompt("Copy to clipboard:", text);
    }
    setTimeout(() => { button.textContent = original; }, 1200);
  }

  /* ---------------- routes ---------------- */
  /* "/status/{code}" → code element with {code} highlighted as a parameter. */
  function routePath(path) {
    return el("code", { class: "route-path" },
      path.split(/(\{[^}]+\})/).filter(Boolean).map((part) =>
        part.startsWith("{") ? el("span", { class: "param" }, part) : part));
  }

  function routeList(routes) {
    return el("div", { class: "routes" }, routes.map((r) =>
      el("div", { class: "route" },
        el("span", { class: "method" }, r.method),
        routePath(r.path),
        r.query ? el("span", { class: "route-query" }, `?${r.query.join("&")}`) : null,
        r.header ? el("span", { class: "route-query" }, `header: ${r.header}`) : null)));
  }

  function endpointIndex(cards) {
    return el("nav", { class: "card endpoint-index", "aria-label": "Endpoints on this page" },
      el("h2", {}, "Endpoints"),
      el("table", {},
        el("thead", {}, el("tr", {},
          el("th", {}, "Method"), el("th", {}, "Path"), el("th", {}, "Query parameters"), el("th", { class: "index-title" }, "Card"))),
        el("tbody", {}, cards.flatMap((card) => card.routes.map((r, i) =>
          el("tr", {},
            el("td", { class: "method" }, r.method),
            el("td", {}, el("a", { href: `#card-${card.id}` }, routePath(r.path))),
            el("td", { class: "muted" }, r.query ? r.query.join(", ") : r.header ? `header ${r.header}` : "—"),
            el("td", { class: "muted index-title" }, i === 0 ? card.title : "")))))));
  }

  /* ---------------- card renderer ---------------- */
  function fieldInput(field) {
    const id = `f-${field.cardId}-${field.name}`;
    let input;
    if (field.type === "select") {
      input = el("select", { id }, field.options.map((o) => {
        const [value, label] = Array.isArray(o) ? o : [o, o];
        return el("option", { value, selected: String(value) === String(field.value) }, label);
      }));
    } else if (field.type === "checkbox") {
      input = el("input", { id, type: "checkbox", checked: Boolean(field.value) });
    } else if (field.type === "textarea") {
      input = el("textarea", { id, rows: field.rows || 2, placeholder: field.placeholder || "" });
      input.value = field.value ?? "";
    } else {
      input = el("input", { id, type: field.type || "text", min: field.min, max: field.max, placeholder: field.placeholder || "" });
      input.value = field.value ?? "";
    }
    const wrap = el("label", { class: `field ${field.type === "checkbox" ? "field-check" : ""} ${field.wide ? "field-wide" : ""}`, for: id },
      el("span", { class: "field-label" }, field.label),
      input);
    if (field.hint) wrap.append(el("span", { class: "field-hint" }, field.hint));
    return { wrap, input };
  }

  function readValues(inputs) {
    const values = {};
    for (const [name, input] of Object.entries(inputs)) {
      values[name] = input.type === "checkbox" ? input.checked : input.value.trim();
    }
    return values;
  }

  function renderCard(card) {
    const inputs = {};
    const fields = (card.fields || []).map((f) => {
      const { wrap, input } = fieldInput({ ...f, cardId: card.id });
      inputs[f.name] = input;
      return wrap;
    });

    const methodEl = el("span", { class: "method" });
    const pathEl = el("code", { class: "path" });
    const output = el("div", { class: "result", "aria-live": "polite" }, el("span", { class: "muted" }, "Send the request to see the response."));
    const current = () => card.request(readValues(inputs));

    const refresh = () => {
      const req = current();
      methodEl.textContent = req.method || "GET";
      pathEl.textContent = req.path;
    };

    const run = async () => {
      const req = current();
      output.replaceChildren(el("span", { class: "muted" }, `Sending ${req.method || "GET"} ${req.path}…`));
      renderResult(output, await send(req), card);
    };

    const actions = el("div", { class: "actions" },
      el("button", { class: "btn primary", type: "button", onclick: run }, "Send"));
    if (card.burst) {
      actions.append(el("button", {
        class: "btn", type: "button",
        onclick: async () => {
          const n = card.burst(readValues(inputs));
          const req = current();
          output.replaceChildren(el("span", { class: "muted" }, `Sending ${n} requests…`));
          const results = [];
          for (let i = 0; i < n; i++) results.push(await send(req));
          renderBurst(output, results);
        },
      }, card.burstLabel ? card.burstLabel(readValues(inputs)) : "Send several"));
    }
    actions.append(
      el("button", { class: "btn ghost", type: "button", onclick: (e) => copy(location.origin + current().path, e.currentTarget) }, "Copy URL"),
      el("button", { class: "btn ghost", type: "button", onclick: (e) => copy(toCurl(current()), e.currentTarget) }, "Copy curl"));

    const presets = card.presets
      ? el("div", { class: "presets" }, el("span", { class: "field-label" }, "Presets"), card.presets.map((p) =>
          el("button", {
            class: "chip", type: "button",
            onclick: () => {
              for (const [name, value] of Object.entries(p.values)) {
                const input = inputs[name];
                if (input.type === "checkbox") input.checked = Boolean(value);
                else input.value = value;
              }
              refresh();
              run();
            },
          }, p.label)))
      : null;

    const section = el("section", { class: "card", id: `card-${card.id}` },
      el("h2", {}, card.title),
      card.routes ? routeList(card.routes) : null,
      el("p", { class: "purpose" }, card.purpose),
      fields.length ? el("div", { class: "fields" }, fields) : null,
      presets,
      el("div", { class: "endpoint", title: "The exact request this card sends" }, methodEl, pathEl),
      card.note ? el("p", { class: "note" }, card.note) : null,
      actions,
      output);

    for (const input of Object.values(inputs)) {
      input.addEventListener("input", refresh);
      input.addEventListener("change", refresh);
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && input.tagName !== "TEXTAREA") run();
      });
    }
    refresh();
    return section;
  }

  /* ---------------- card definitions ---------------- */
  const enc = encodeURIComponent;
  const positive = (v) => (v && Number(v) > 0 ? v : "");

  const SIMULATOR_CARDS = [
    {
      id: "status",
      title: "Status codes",
      purpose: "Return any status from 200 to 599. Use it to test how your client handles errors, empty 204/304 bodies and retryable codes.",
      fields: [
        { name: "code", label: "Status", type: "number", value: "418", min: 200, max: 599 },
        { name: "method", label: "Method", type: "select", value: "GET", options: ["GET", "POST", "PUT", "PATCH", "DELETE"] },
        { name: "delay", label: "Delay (ms)", type: "number", value: "0", min: 0 },
        { name: "message", label: "Message", placeholder: "optional", wide: true },
      ],
      presets: [200, 204, 301, 404, 429, 500, 503].map((code) => ({ label: String(code), values: { code } })),
      request: (v) => ({ method: v.method, path: `/status/${enc(v.code)}${qs({ message: v.message, delay: positive(v.delay) })}` }),
    },
    {
      id: "delay",
      title: "Fixed delay",
      purpose: "Wait before responding. Use it to test client timeouts and loading states.",
      fields: [{ name: "ms", label: "Delay (ms)", type: "number", value: "1000", min: 0 }],
      presets: [100, 1000, 5000].map((ms) => ({ label: `${ms} ms`, values: { ms } })),
      request: (v) => ({ path: `/delay/${enc(v.ms || 0)}` }),
    },
    {
      id: "delay-random",
      title: "Random delay",
      purpose: "Wait a random time between min and max. Use it to test jitter, timeouts and percentile latency handling.",
      fields: [
        { name: "min", label: "Min (ms)", type: "number", value: "100", min: 0 },
        { name: "max", label: "Max (ms)", type: "number", value: "2000", min: 0 },
      ],
      request: (v) => ({ path: `/delay/random${qs({ min: v.min, max: v.max })}` }),
    },
    {
      id: "redirect",
      title: "Redirect chain",
      purpose: "Issue exactly N redirects before landing on the target. Use it to test redirect following, hop limits and method changes on 303.",
      fields: [
        { name: "count", label: "Hops", type: "number", value: "3", min: 0, max: 20 },
        { name: "status", label: "Status", type: "select", value: "302", options: ["301", "302", "303", "307", "308"] },
        { name: "to", label: "Final target", value: "/anything", hint: "same-origin path" },
      ],
      note: "Browsers follow redirects automatically, so the result shows only the final response. Use Copy curl (it adds -L) to see every hop.",
      request: (v) => ({ path: `/redirect/${enc(v.count || 0)}${qs({ status: v.status, to: v.to === "/anything" ? "" : v.to })}`, follow: true }),
    },
    {
      id: "content",
      title: "Content types",
      purpose: "Return a sample body in a specific format. Use it to test parsers and content negotiation.",
      fields: [
        { name: "fmt", label: "Format", type: "select", value: "json", options: ["json", "xml", "html", "text", "csv", "octet-stream", "problem+json"] },
        { name: "status", label: "Status", type: "number", value: "200", min: 200, max: 599 },
      ],
      request: (v) => ({ path: `/content/${enc(v.fmt)}${qs({ status: v.status === "200" ? "" : v.status })}` }),
    },
    {
      id: "size",
      title: "Response size",
      purpose: "Stream a body of an exact size. Use it to test buffering, memory limits and download progress.",
      fields: [{ name: "size", label: "Size", value: "256kb", hint: "e.g. 512, 64kb, 2mb" }],
      presets: ["1kb", "256kb", "1mb", "10mb"].map((size) => ({ label: size, values: { size } })),
      bodyMode: "size",
      request: (v) => ({ path: `/response-size/${enc(v.size || "1kb")}` }),
    },
    {
      id: "edge",
      title: "Malformed & edge responses",
      purpose: "Responses that break the usual assumptions. Use them to check that your client fails cleanly instead of crashing or hanging.",
      fields: [
        {
          name: "kind", label: "Response", type: "select", value: "malformed/json", wide: true,
          options: [
            ["malformed/json", "Invalid JSON with Content-Type: application/json"],
            ["empty", "Empty 200 body"],
            ["truncated", "Body shorter than its Content-Length"],
          ],
        },
      ],
      request: (v) => ({ path: `/${v.kind}` }),
    },
    {
      id: "flaky",
      title: "Flaky endpoint",
      purpose: "Fail intermittently. Use it to test retry and backoff logic. Give a fixed status sequence or a random error rate; sequences are tracked per client IP.",
      fields: [
        { name: "codes", label: "Status sequence", value: "200,200,500", hint: "cycles in order", wide: true },
        { name: "error_rate", label: "Or error rate (%)", type: "number", min: 0, max: 100, placeholder: "30" },
        { name: "seed", label: "Seed", type: "number", placeholder: "random", hint: "makes the rate reproducible" },
      ],
      request: (v) => ({ path: `/flaky${qs(v.codes ? { codes: v.codes } : { error_rate: v.error_rate, seed: v.seed })}` }),
      burst: () => 5,
      burstLabel: () => "Send 5×",
    },
    {
      id: "rate-limit",
      title: "Rate limit",
      purpose: "Allow N requests per window for each client, then return 429 with Retry-After and X-RateLimit-* headers. Use it to test throttling and backoff.",
      fields: [
        { name: "requests", label: "Requests allowed", type: "number", value: "3", min: 1 },
        { name: "window", label: "Window (s)", type: "number", value: "60", min: 1 },
      ],
      request: (v) => ({ path: `/rate-limit${qs({ requests: v.requests, window: v.window })}` }),
      burst: (v) => Math.min(Number(v.requests || 1) + 1, 50),
      burstLabel: (v) => `Send ${Math.min(Number(v.requests || 1) + 1, 50)}× (one over)`,
    },
    {
      id: "builder",
      title: "Response builder",
      purpose: "Build any response: status, body, content type and extra headers. Use it to mock a specific API reply.",
      fields: [
        { name: "status", label: "Status", type: "number", value: "429", min: 200, max: 599 },
        { name: "content_type", label: "Content-Type", value: "application/json" },
        { name: "delay", label: "Delay (ms)", type: "number", value: "0", min: 0 },
        { name: "body", label: "Body", type: "textarea", value: '{"error": "rate_limited"}', wide: true },
        { name: "headers", label: "Headers (one per line)", type: "textarea", value: "Retry-After: 30", wide: true },
      ],
      request: (v) => ({
        path: `/response${qs({
          status: v.status,
          content_type: v.content_type === "application/json" ? "" : v.content_type,
          delay: positive(v.delay),
          body: v.body,
          header: v.headers.split("\n").map((h) => h.trim()).filter(Boolean),
        })}`,
      }),
    },
  ];

  const AUTH_CARDS = [
    {
      id: "basic",
      title: "Basic auth",
      purpose: "The URL sets the expected username and password. Send matching or wrong credentials to test the 401 + WWW-Authenticate flow.",
      fields: [
        { name: "user", label: "Expected user", value: "user" },
        { name: "pass", label: "Expected password", value: "pass" },
        { name: "sendUser", label: "Send user", value: "user" },
        { name: "sendPass", label: "Send password", value: "pass" },
      ],
      presets: [
        { label: "Correct", values: { sendUser: "user", sendPass: "pass", user: "user", pass: "pass" } },
        { label: "Wrong password", values: { sendUser: "user", sendPass: "WRONG", user: "user", pass: "pass" } },
      ],
      request: (v) => ({
        path: `/basic-auth/${enc(v.user)}/${enc(v.pass)}`,
        headers: v.sendUser || v.sendPass ? { Authorization: `Basic ${btoa(`${v.sendUser}:${v.sendPass}`)}` } : {},
      }),
    },
    {
      id: "bearer",
      title: "Bearer token",
      purpose: "Requires Authorization: Bearer <token>. With an expected token, only that value is accepted; otherwise any token passes.",
      fields: [
        { name: "token", label: "Send token", value: "meowmeow", hint: "empty = no header" },
        { name: "expected", label: "Expected token", placeholder: "any" },
      ],
      request: (v) => ({ path: `/bearer-auth${qs({ token: v.expected })}`, headers: v.token ? { Authorization: `Bearer ${v.token}` } : {} }),
    },
    {
      id: "api-key",
      title: "API key",
      purpose: "Requires an X-API-Key header. With an expected key, only that value is accepted.",
      fields: [
        { name: "key", label: "Send X-API-Key", value: "lab-42", hint: "empty = no header" },
        { name: "expected", label: "Expected key", placeholder: "any" },
      ],
      request: (v) => ({ path: `/api-key${qs({ key: v.expected })}`, headers: v.key ? { "X-API-Key": v.key } : {} }),
    },
    {
      id: "set-cookie",
      title: "Set a cookie",
      purpose: "Responds with Set-Cookie using your attributes. Then use Read cookies to check what the browser sends back.",
      fields: [
        { name: "name", label: "Name", value: "session" },
        { name: "value", label: "Value", value: "abc123" },
        { name: "max_age", label: "Max-Age (s)", type: "number", value: "3600", min: 0 },
        { name: "samesite", label: "SameSite", type: "select", value: "lax", options: ["lax", "strict", "none"] },
        { name: "httponly", label: "HttpOnly", type: "checkbox" },
        { name: "secure", label: "Secure", type: "checkbox" },
      ],
      note: "SameSite=none is ignored by browsers unless Secure is also set, and Secure cookies are dropped over plain http (except on localhost).",
      request: (v) => ({ path: `/set-cookie${qs({ name: v.name, value: v.value, max_age: v.max_age, samesite: v.samesite === "lax" ? "" : v.samesite, httponly: v.httponly && "true", secure: v.secure && "true" })}` }),
    },
    {
      id: "cookies",
      title: "Read cookies",
      purpose: "Echo the cookies this browser sent. HttpOnly cookies appear here even though JavaScript can't read them.",
      request: () => ({ path: "/cookies" }),
    },
  ];

  /* The endpoint(s) behind each card and every parameter the API accepts,
     including ones the card does not expose as inputs. Keep in sync with app/api. */
  const ROUTES = {
    status: [{ method: "ANY", path: "/status/{code}", query: ["message", "body", "delay", "header"] }],
    delay: [{ method: "GET", path: "/delay/{ms}" }],
    "delay-random": [{ method: "GET", path: "/delay/random", query: ["min", "max"] }],
    redirect: [{ method: "GET", path: "/redirect/{count}", query: ["status", "to"] }],
    content: [{ method: "GET", path: "/content/{fmt}", query: ["status", "delay"] }],
    size: [{ method: "GET", path: "/response-size/{size}", query: ["status", "delay"] }],
    edge: [
      { method: "GET", path: "/malformed/json" },
      { method: "GET", path: "/empty" },
      { method: "GET", path: "/truncated" },
    ],
    flaky: [{ method: "GET", path: "/flaky", query: ["codes", "error_rate", "seed"] }],
    "rate-limit": [{ method: "GET", path: "/rate-limit", query: ["requests", "window"] }],
    builder: [{ method: "GET", path: "/response", query: ["status", "body", "content_type", "delay", "header"] }],
    basic: [{ method: "GET", path: "/basic-auth/{username}/{password}", header: "Authorization: Basic" }],
    bearer: [{ method: "GET", path: "/bearer-auth", query: ["token"], header: "Authorization: Bearer" }],
    "api-key": [{ method: "GET", path: "/api-key", query: ["key"], header: "X-API-Key" }],
    "set-cookie": [{ method: "GET", path: "/set-cookie", query: ["name", "value", "max_age", "expires", "path", "samesite", "httponly", "secure"] }],
    cookies: [{ method: "GET", path: "/cookies" }],
  };
  for (const card of [...SIMULATOR_CARDS, ...AUTH_CARDS]) card.routes = ROUTES[card.id];

  $("#simulators-grid").before(endpointIndex(SIMULATOR_CARDS));
  $("#simulators-grid").append(...SIMULATOR_CARDS.map(renderCard));
  $("#auth-grid").before(endpointIndex(AUTH_CARDS));
  $("#auth-grid").append(...AUTH_CARDS.map(renderCard));

  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
  showTab(location.hash.slice(1));

  /* ---------------- inspector ---------------- */
  const out = $("#inspector-output");

  $$("[data-request]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      renderResult(out, await send({ path: btn.dataset.request }));
    })
  );

  $("#any-send").addEventListener("click", async () => {
    const method = $("#any-method").value;
    let path = $("#any-path").value.trim() || "/anything";
    const query = $("#any-query").value.trim();
    if (query) path += query.startsWith("?") ? query : `?${query}`;
    const body = method === "GET" ? "" : $("#any-body").value;
    const headers = body && $("#any-json").checked ? { "Content-Type": "application/json" } : {};
    renderResult(out, await send({ method, path, headers, body }));
  });
})();
