/*
 * Shell of the FLAI interface demo: it loads the login screen into the iframe,
 * swaps it for the interface on login and switches between desktop and phone.
 */
(function () {
  "use strict";

  var frame = document.getElementById("demo-screen");
  var loginSource = document.getElementById("demo-login");
  var appSource = document.getElementById("demo-app");
  var body = document.body;

  function decode(text) {
    return text.replace(/<\\\/script/g, "</script").replace(/<\\!--/g, "<!--");
  }

  function setDevice(name) {
    body.classList.toggle("demo-mobile", name === "mobile");
    body.classList.toggle("demo-desktop", name !== "mobile");
    try {
      window.localStorage.setItem("demo_device", name);
    } catch (e) {
      /* storage is optional */
    }
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-device]"), function (button) {
    button.addEventListener("click", function () {
      setDevice(button.getAttribute("data-device"));
    });
  });

  var saved = "desktop";
  try {
    saved = window.localStorage.getItem("demo_device") || "desktop";
  } catch (e) {
    /* storage is optional */
  }
  setDevice(saved);

  frame.srcdoc = decode(loginSource.textContent);

  function savedTheme() {
    var match = /(?:^|[#&])theme=(dark|light)/.exec(window.location.hash);
    if (match) return match[1];
    try {
      return window.localStorage.getItem("demo_theme") || "";
    } catch (e) {
      return "";
    }
  }

  function rememberTheme(theme) {
    try {
      window.localStorage.setItem("demo_theme", theme);
    } catch (e) {
      /* storage is optional */
    }
    try {
      window.history.replaceState(null, "", "#theme=" + theme);
    } catch (e) {
      window.location.hash = "theme=" + theme;
    }
  }

  function pushTheme() {
    var theme = savedTheme();
    if (!theme || !frame.contentWindow) return;
    frame.contentWindow.postMessage({ type: "demo-theme", theme: theme }, "*");
  }

  frame.addEventListener("load", pushTheme);

  window.addEventListener("message", function (event) {
    var data = event.data || {};
    if (data.type === "demo-login") {
      frame.srcdoc = decode(appSource.textContent);
    } else if (data.type === "demo-theme-set" && data.theme) {
      rememberTheme(data.theme);
    }
  });
})();
