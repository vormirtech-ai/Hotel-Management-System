// Small progressive enhancements. Every page works without JavaScript.
(function () {
  // Ask before destructive actions: <form data-confirm="Are you sure?">
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // Guest picker: show either the "existing guest" select or the new-guest fields.
  document.querySelectorAll("[data-guest-mode]").forEach(function (radio) {
    function sync() {
      var mode = document.querySelector("[data-guest-mode]:checked");
      if (!mode) return;
      document.querySelectorAll("[data-guest-panel]").forEach(function (panel) {
        var on = panel.getAttribute("data-guest-panel") === mode.value;
        panel.hidden = !on;
        panel.querySelectorAll("input, select, textarea").forEach(function (el) { el.disabled = !on; });
      });
    }
    radio.addEventListener("change", sync);
    sync();
  });

  // Filter a long <select> with a search box: <input data-filter-select="#id">
  document.querySelectorAll("[data-filter-select]").forEach(function (input) {
    var select = document.querySelector(input.getAttribute("data-filter-select"));
    if (!select) return;
    input.addEventListener("input", function () {
      var q = input.value.toLowerCase();
      var firstMatch = null;
      Array.prototype.forEach.call(select.options, function (opt) {
        var match = !opt.value || opt.text.toLowerCase().indexOf(q) !== -1;
        opt.hidden = !match;
        if (match && opt.value && !firstMatch) firstMatch = opt;
      });
      if (firstMatch && select.selectedOptions[0] && select.selectedOptions[0].hidden) firstMatch.selected = true;
    });
  });

  // Fill the rate when a room / venue / slot is chosen: elements with data-rate.
  function fillRate(value) {
    var rate = document.querySelector("[data-rate-target]");
    if (rate && value !== undefined && value !== null && value !== "") rate.value = value;
  }
  document.querySelectorAll("input[name=room_id]").forEach(function (r) {
    r.addEventListener("change", function () { fillRate(r.getAttribute("data-rate")); });
    var rate = document.querySelector("[data-rate-target]");
    if (r.checked && rate && !rate.value) fillRate(r.getAttribute("data-rate"));
  });
  var venue = document.querySelector("select[data-venue-select]");
  var slot = document.querySelector("select[data-slot-select]");
  function venueRate() {
    if (!venue || !slot) return;
    var opt = venue.selectedOptions[0];
    if (!opt) return;
    fillRate(opt.getAttribute(slot.value === "full" ? "data-full" : "data-half"));
  }
  if (venue && slot) {
    venue.addEventListener("change", venueRate);
    slot.addEventListener("change", venueRate);
  }

  // Close the mobile menu when navigating.
  document.querySelectorAll(".sidebar a").forEach(function (a) {
    a.addEventListener("click", function () { document.body.classList.remove("nav-open"); });
  });
})();
