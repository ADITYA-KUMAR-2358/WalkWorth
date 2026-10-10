// The journal's one piece of motion: as you read, the route in the margin
// draws up to the voice note you're on, its pin lights, and the dot on the
// elevation strip moves there. Nothing loops.
(function () {
  var root = document.documentElement;

  var toggle = document.querySelector(".theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var dark = root.dataset.theme
        ? root.dataset.theme === "dark"
        : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("ww-theme", root.dataset.theme); } catch (e) {}
    });
  }

  var progress = document.querySelector(".route-progress");
  var stripDot = document.querySelector(".strip-wrap--margin .strip-dot");
  var pins = {};
  document.querySelectorAll(".route--margin [data-pin]").forEach(function (el) { pins[el.getAttribute("data-pin")] = el; });
  var entries = Array.prototype.slice.call(document.querySelectorAll("[data-entry]"));
  if (!progress || !entries.length || !("IntersectionObserver" in window)) return;

  function show(entry) {
    var frac = parseFloat(entry.getAttribute("data-frac")) || 0;
    progress.style.setProperty("--progress", String(1 - frac));
    var id = entry.getAttribute("data-entry");
    var passed = true;
    entries.forEach(function (e) {
      var pin = pins[e.getAttribute("data-entry")];
      if (!pin) return;
      pin.classList.toggle("is-here", e === entry);
      pin.classList.toggle("is-passed", passed);
      if (e.getAttribute("data-entry") === id) passed = false;
    });
    if (stripDot && entry.hasAttribute("data-sx")) {
      stripDot.hidden = false;
      stripDot.style.left = entry.getAttribute("data-sx") + "%";
      stripDot.style.top = entry.getAttribute("data-sy") + "%";
    }
  }

  var io = new IntersectionObserver(function (items) {
    items.forEach(function (item) { if (item.isIntersecting) show(item.target); });
  }, { rootMargin: "-40% 0px -55% 0px" });
  entries.forEach(function (e) { io.observe(e); });
})();
