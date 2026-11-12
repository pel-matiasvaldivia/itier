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
