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

  window.addEventListener("message", function (event) {
    if (event.data && event.data.type === "demo-login") {
      frame.srcdoc = decode(appSource.textContent);
    }
  });
})();
