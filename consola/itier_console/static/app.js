// Mínimo y sin dependencias: copiar al portapapeles. La consola funciona sin JS;
// esto es solo una comodidad para el bundle de enrolamiento.
document.addEventListener("click", function (ev) {
  const btn = ev.target.closest("[data-copy]");
  if (!btn) return;
  const el = document.querySelector(btn.getAttribute("data-copy"));
  if (!el) return;
  const texto = el.innerText;
  const ok = () => {
    const orig = btn.textContent;
    btn.textContent = "¡Copiado!";
    setTimeout(() => (btn.textContent = orig), 1400);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(texto).then(ok).catch(fallback);
  } else {
    fallback();
  }
  function fallback() {
    const r = document.createRange();
    r.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
    try { document.execCommand("copy"); ok(); } catch (e) {}
    sel.removeAllRanges();
  }
});

// Feedback inmediato al navegar: barra de progreso arriba + cursor de espera. Las pantallas consultan
// GLPI/Zabbix/Mesh y pueden tardar un poco; sin esto parece que el clic no hizo nada.
(function () {
  var barra = document.createElement("div");
  barra.id = "nav-bar";
  document.addEventListener("DOMContentLoaded", function () { document.body.appendChild(barra); });
  function iniciar() { barra.className = "on"; document.documentElement.classList.add("cargando"); }
  function terminar() { barra.className = ""; document.documentElement.classList.remove("cargando"); }
  document.addEventListener("click", function (ev) {
    if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    var a = ev.target.closest("a[href]");
    if (!a || a.target === "_blank" || a.hasAttribute("download") || a.hasAttribute("data-copy")) return;
    var h = a.getAttribute("href") || "";
    if (h.charAt(0) === "#" || /^(mailto:|tel:|javascript:)/.test(h)) return;
    if (a.origin && a.origin !== location.origin) return;
    iniciar();
  });
  document.addEventListener("submit", function (ev) { if (!ev.defaultPrevented) iniciar(); });
  window.addEventListener("pageshow", terminar);   // al volver con «atrás» no debe quedar colgada
})();
