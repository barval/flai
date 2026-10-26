/*
 * Offline shim of the FLAI interface demo.
 *
 * It runs before every application script inside the demo iframe: it answers
 * the API calls from the embedded snapshot, keeps the theme switch working and
 * turns every action that would change state into a demo notice.
 */
(function () {
  "use strict";

  var DEMO = window.__DEMO__ || { lang: "ru", notice: "Interface demo", routes: [], assets: {} };
  var NOTICE_ID = "demo-notice";
  var themeByUser = false;
  var BLOCKED_SELECTOR =
    '#save-chat-button, #save-document-button, a[download], a[href^="/api/"], ' +
    'a[href^="/admin/"], a[target="_blank"]';

  document.documentElement.setAttribute("data-demo", "1");
  document.documentElement.setAttribute("data-demo-asset-count", String(Object.keys(DEMO.assets || {}).length));

  function ensureStyle() {
    if (document.getElementById("demo-style")) return;
    var style = document.createElement("style");
    style.id = "demo-style";
    style.textContent =
      "#demo-notice{position:fixed;left:50%;bottom:32px;transform:translateX(-50%) translateY(16px);" +
      "max-width:min(560px,90vw);padding:14px 22px;border-radius:10px;background:#fff3b0;" +
      "border:1px solid #e3c454;color:#3b2f00;font:600 14.5px/1.5 system-ui,sans-serif;" +
      "box-shadow:0 10px 30px rgba(0,0,0,.35);" +
      "opacity:0;pointer-events:none;transition:opacity .18s ease,transform .18s ease;z-index:2147483647}" +
      "#demo-notice.demo-visible{opacity:1;transform:translateX(-50%) translateY(0)}";
    document.head.appendChild(style);
  }

  function showNotice() {
    ensureStyle();
    var el = document.getElementById(NOTICE_ID);
    if (!el) {
      el = document.createElement("div");
      el.id = NOTICE_ID;
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = DEMO.notice;
    el.classList.add("demo-visible");
    window.clearTimeout(showNotice.timer);
    showNotice.timer = window.setTimeout(function () {
      el.classList.remove("demo-visible");
    }, 3000);
  }

  function jsonResponse(body) {
    return new Response(JSON.stringify(body === undefined ? {} : body), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  }

  function applyTheme(theme) {
    document.body.classList.toggle("dark-theme", theme === "dark");
    document.documentElement.classList.toggle("demo-dark", theme === "dark");
    ["theme-toggle", "theme-toggle-guest"].forEach(function (id) {
      var button = document.getElementById(id);
      if (button) button.textContent = theme === "dark" ? "\u2600\ufe0f" : "\ud83c\udf19";
    });
    try {
      window.localStorage.setItem("demo_theme", theme);
    } catch (e) {
      /* storage is unavailable under file:// in some browsers */
    }
  }

  function savedTheme() {
    try {
      return window.localStorage.getItem("demo_theme");
    } catch (e) {
      return null;
    }
  }

  function queryMatches(routeQuery, search) {
    var params = routeQuery.split("&");
    var haystack = "&" + search + "&";
    for (var i = 0; i < params.length; i += 1) {
      if (haystack.indexOf("&" + params[i] + "&") < 0) return false;
    }
    return true;
  }

  window.fetch = function (input, init) {
    var options = init || {};
    var method = String(options.method || (input && input.method) || "GET").toUpperCase();
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var path = url.split("?")[0];

    if (method === "GET") {
      var search = url.indexOf("?") >= 0 ? url.split("?")[1] : "";
      var bare = null;
      for (var i = 0; i < DEMO.routes.length; i += 1) {
        var route = DEMO.routes[i];
        if (route.m !== "GET" || route.p !== path) continue;
        if (route.q && queryMatches(route.q, search)) return Promise.resolve(jsonResponse(route.b));
        if (!route.q && bare === null) bare = route;
      }
      if (path === "/api/sessions/demo-new/messages") {
        return Promise.resolve(jsonResponse({ messages: [], limit: 100, offset: 0, has_more: false }));
      }
      return Promise.resolve(jsonResponse(bare ? bare.b : {}));
    }
    if (path.indexOf("/set-theme/") === 0) {
      // Only a click may change the theme: product scripts ask for a theme on
      // load (the guest page does) and would otherwise cancel the visitor's choice.
      if (themeByUser) {
        var requested = path.split("/").pop();
        applyTheme(requested);
        try {
          window.parent.postMessage({ type: "demo-theme-set", theme: requested }, "*");
        } catch (e) {
          /* the shell is always the parent in the built page */
        }
      }
      return Promise.resolve(jsonResponse({ status: "ok" }));
    }
    if (path === "/api/sessions/new") {
      // A fresh conversation must look like one, not like an empty object.
      return Promise.resolve(jsonResponse({ id: "demo-new", title: "" }));
    }
    if (isNavigationRequest(path)) {
      return Promise.resolve(jsonResponse({ status: "ok" }));
    }
    showNotice();
    return Promise.resolve(jsonResponse({ demo: true }));
  };

  function InertEventSource(url) {
    this.url = url;
    this.readyState = 0;
  }
  InertEventSource.prototype.addEventListener = function () {};
  InertEventSource.prototype.removeEventListener = function () {};
  InertEventSource.prototype.close = function () {};
  InertEventSource.CONNECTING = 0;
  InertEventSource.OPEN = 1;
  InertEventSource.CLOSED = 2;
  window.EventSource = InertEventSource;

  window.alert = function () {
    showNotice();
  };
  window.confirm = function () {
    showNotice();
    return false;
  };
  window.prompt = function () {
    showNotice();
    return null;
  };
  window.open = function () {
    showNotice();
    return null;
  };

  // Recording would ask for a microphone and capture silence.
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    navigator.mediaDevices.getUserMedia = function () {
      showNotice();
      return Promise.reject(new Error("demo"));
    };
  }

  // Opening a session is navigation, not an action.
  function isNavigationRequest(path) {
    return /^\/api\/sessions\/[^/]+\/(switch|visit)$/.test(path);
  }

  // A file dialog would open over the demo and accept nothing useful.
  var nativeInputClick = HTMLInputElement.prototype.click;
  HTMLInputElement.prototype.click = function () {
    if (this.type === "file") {
      showNotice();
      return;
    }
    return nativeInputClick.apply(this, arguments);
  };

  document.addEventListener(
    "submit",
    function (event) {
      event.preventDefault();
      event.stopPropagation();
      var form = event.target;
      if (form && form.hasAttribute && form.hasAttribute("data-demo-login")) {
        try {
          window.parent.postMessage({ type: "demo-login" }, "*");
        } catch (e) {
          /* the shell is always the parent in the built page */
        }
        return;
      }
      showNotice();
    },
    true
  );

  document.addEventListener(
    "change",
    function (event) {
      var target = event.target;
      if (target && target.id === "language-select") {
        event.stopImmediatePropagation();
        event.preventDefault();
        showNotice();
      }
    },
    true
  );

  document.addEventListener(
    "click",
    function (event) {
      var target = event.target;
      if (!target || !target.closest) return;
      if (target.closest("#theme-toggle-guest")) {
        // The guest page keeps its own theme in localStorage, which an embedded
        // document does not get; the shim owns the switch there.
        event.preventDefault();
        event.stopImmediatePropagation();
        var next = document.body.classList.contains("dark-theme") ? "light" : "dark";
        themeByUser = true;
        applyTheme(next);
        try {
          window.parent.postMessage({ type: "demo-theme-set", theme: next }, "*");
        } catch (e) {
          /* the shell is always the parent in the built page */
        }
        window.setTimeout(function () {
          themeByUser = false;
        }, 1500);
        return;
      }
      if (target.closest("#theme-toggle")) {
        themeByUser = true;
        window.setTimeout(function () {
          themeByUser = false;
        }, 1500);
      }
      if (target.matches && target.matches('input[type="file"]')) {
        event.preventDefault();
        event.stopImmediatePropagation();
        showNotice();
        return;
      }
      if (target.closest(".lang-option") || target.closest(BLOCKED_SELECTOR)) {
        event.preventDefault();
        event.stopImmediatePropagation();
        showNotice();
      }
    },
    true
  );

  function asset(url) {
    if (typeof url !== "string") return url;
    var path = url.split("?")[0];
    return DEMO.assets[path] || url;
  }

  function enhancePdfPreview() {
    // The desktop preview loads the PDF itself in an iframe. A file:// page has
    // no server to answer that URL, so the frame renders a broken-document icon.
    // The capture stores rendered page images; show those in the frame's place.
    var box = document.getElementById("document-preview");
    if (!box || box.querySelector(".demo-page")) return;
    var link = box.querySelector(".document-preview-open");
    var frame = box.querySelector(".document-preview-frame");
    var source = (link && link.getAttribute("href")) || (frame && frame.getAttribute("src")) || "";
    var match = /\/api\/documents\/([^/]+)\/preview\//.exec(source);
    if (!match) return;
    var pages = [];
    for (var n = 1; n <= 30; n += 1) {
      var src = DEMO.assets["/api/documents/" + match[1] + "/preview/pdf-page/" + n];
      if (!src) break;
      pages.push(src);
    }
    if (!pages.length) return;
    var container = document.createElement("div");
    container.className = "document-preview-pages demo-page";
    pages.forEach(function (pageSrc, index) {
      var image = document.createElement("img");
      image.className = "document-preview-page";
      image.src = pageSrc;
      image.alt = (link ? link.textContent.trim() : "") + " " + (index + 1);
      container.appendChild(image);
    });
    if (frame) box.replaceChild(container, frame);
    else box.appendChild(container);
  }

  function rewrite(element) {
    ["src", "href"].forEach(function (attr) {
      var value = element.getAttribute && element.getAttribute(attr);
      if (!value || value.indexOf("/api/") !== 0) return;
      var replacement = asset(value);
      if (replacement !== value) element.setAttribute(attr, replacement);
    });
  }

  window.addEventListener("message", function (event) {
    var data = event.data || {};
    if (data.type === "demo-theme" && (data.theme === "dark" || data.theme === "light")) {
      applyTheme(data.theme);
      document.documentElement.setAttribute("data-demo-theme", data.theme);
    }
  });

  document.addEventListener(
    "DOMContentLoaded",
    function () {
      document.documentElement.setAttribute("data-demo-ready", "1");
      ensureStyle();
      var theme = savedTheme();
      if (theme) applyTheme(theme);
      var observer = new MutationObserver(function (records) {
        records.forEach(function (record) {
          if (record.type === "attributes") {
            rewrite(record.target);
            return;
          }
          record.addedNodes.forEach(function (node) {
            if (node.nodeType !== 1) return;
            rewrite(node);
            node.querySelectorAll('[src^="/api/"], [href^="/api/"]').forEach(rewrite);
          });
          enhancePdfPreview();
        });
      });
      window.__demoObserver = observer;
      observer.observe(document.documentElement, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["src", "href"]
      });
      document.querySelectorAll('[src^="/api/"], [href^="/api/"]').forEach(rewrite);
    },
    { once: true }
  );
})();
