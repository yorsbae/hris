/* Situs publik: hanya peningkatan opsional (situs tetap berfungsi tanpa JavaScript). */
(function () {
  // pita produk: tombol jeda/putar (WCAG 2.2.2) — hanya bila JS aktif; tanpa JS tetap bergeser dan berhenti saat disorot/fokus.
  Array.prototype.forEach.call(document.querySelectorAll(".marq"), function (m) {
    var b = document.createElement("button"); b.type = "button"; b.className = "mpause"; b.textContent = m.getAttribute("data-pause") || "Pause"; b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", function () { var p = m.classList.toggle("paused"); b.setAttribute("aria-pressed", p ? "true" : "false"); b.textContent = m.getAttribute(p ? "data-play" : "data-pause"); });
    m.appendChild(b);
  });

  var f = document.getElementById("applyform");
  if (f) {
    var key = "draft:" + f.getAttribute("data-job");
    var skip = { csrfmiddlewaretoken: 1, ts: 1, idem: 1, website: 1, cv: 1, consent: 1, talent_pool: 1, email: 1, phone: 1, birth_date: 1, expected_salary: 1 };
    var els = Array.prototype.filter.call(f.elements, function (e) { return e.name && !skip[e.name] && e.type !== "file" && e.type !== "submit"; });
    try {
      var saved = JSON.parse(localStorage.getItem(key) || "null");
      if (saved) els.forEach(function (e) { if (saved[e.name] && !e.value) e.value = saved[e.name]; });
      var save = function () { var o = {}; els.forEach(function (e) { if (e.value) o[e.name] = e.value; }); localStorage.setItem(key, JSON.stringify(o)); };
      f.addEventListener("input", save);
      var clr = document.getElementById("cleardraft");
      if (clr) clr.addEventListener("click", function (ev) { ev.preventDefault(); localStorage.removeItem(key); location.reload(); });
      f.addEventListener("submit", function () { var b = f.querySelector("button[type=submit]"); if (b) { b.disabled = true; b.textContent = "…"; } });
    } catch (e) { /* penyimpanan diblokir: abaikan */ }
  }
  if (document.getElementById("applydone")) {
    try { Object.keys(localStorage).forEach(function (k) { if (k.indexOf("draft:") === 0) localStorage.removeItem(k); }); } catch (e) { }
  }
})();

// pilihan warna: memilih warna otomatis beralih ke tampilan “Foto produk” (bila ada tombol Dipakai model | Foto produk). Tanpa JS tetap berfungsi lewat CSS.
(function () {
  Array.prototype.forEach.call(document.querySelectorAll(".cpick input.cr"), function (r) {
    r.addEventListener("change", function () { var pp = document.getElementById("pv-p"); if (pp && !pp.checked) pp.checked = true; });
  });
})();
