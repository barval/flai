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
    '#save-chat-button, #save-document-button, a[download], a[href^="/api/"], a[target="_blank"]';

  document.documentElement.setAttribute("data-demo", "1");

  function ensureStyle() {
    if (document.getElementById("demo-style")) return;
    var style = document.createElement("style");
    style.id = "demo-style";
    style.textContent =
      "#demo-notice{position:fixed;left:50%;bottom:32px;transform:translateX(-50%) translateY(16px);" +
      "max-width:min(520px,90vw);padding:12px 18px;border-radius:10px;background:rgba(30,30,34,.94);" +
      "color:#fff;font:14px/1.45 system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.35);" +
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

  window.fetch = function (input, init) {
    var options = init || {};
    var method = String(options.method || (input && input.method) || "GET").toUpperCase();
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var path = url.split("?")[0];

    if (method === "GET") {
      for (var i = 0; i < DEMO.routes.length; i += 1) {
        if (DEMO.routes[i].m === "GET" && DEMO.routes[i].p === path) {
          return Promise.resolve(jsonResponse(DEMO.routes[i].b));
        }
      }
      return Promise.resolve(jsonResponse({}));
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
      if (target.closest("#theme-toggle") || target.closest("#theme-toggle-guest")) {
        themeByUser = true;
        window.setTimeout(function () {
          themeByUser = false;
        }, 1500);
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
      ensureStyle();
      var theme = savedTheme();
      if (theme) applyTheme(theme);
      var observer = new MutationObserver(function (records) {
        records.forEach(function (record) {
          rewrite(record.target);
        });
      });
      observer.observe(document.documentElement, {
        subtree: true,
        attributes: true,
        attributeFilter: ["src", "href"]
      });
      document.querySelectorAll('[src^="/api/"], [href^="/api/"]').forEach(rewrite);
    },
    { once: true }
  );
})();
