#!/usr/bin/env python3
"""Add a visible startup diagnostic for the GTK WebKit wrapper."""

from pathlib import Path


index = Path(__file__).resolve().parents[1] / "dist" / "index.html"
html = index.read_text(encoding="utf-8")
marker = "<!-- Loops GTK startup diagnostics -->"
if marker in html:
    print("Diagnóstico de inicialização já presente")
    raise SystemExit(0)

diagnostics = r'''<!-- Loops GTK startup diagnostics -->
<style>
  html, body { background: #000 !important; color: #eee; }
  #loops-startup-diagnostic { position: fixed; inset: 0; z-index: 2147483647; display: flex;
    flex-direction: column; align-items: center; justify-content: center; gap: 14px;
    padding: 28px; box-sizing: border-box; background: #000; color: #eee;
    font: 16px system-ui, sans-serif; text-align: center; }
  #loops-startup-diagnostic[hidden] { display: none; }
  #loops-startup-diagnostic strong { font-size: 22px; }
  #loops-startup-diagnostic pre { max-width: 90vw; white-space: pre-wrap; overflow-wrap: anywhere;
    color: #ffb4ab; font: 13px monospace; }
  #loops-startup-diagnostic button { border: 0; border-radius: 20px; padding: 12px 24px;
    background: #d4c900; color: #000; font-weight: 700; }
</style>
<div id="loops-startup-diagnostic" role="alert">
  <strong>Iniciando o Loops…</strong>
  <span id="loops-startup-message">Aguarde enquanto a interface é carregada.</span>
  <pre id="loops-startup-error" hidden></pre>
  <button id="loops-startup-reload" type="button" hidden>Recarregar</button>
</div>
<script>
(() => {
  const overlay = document.getElementById('loops-startup-diagnostic');
  const message = document.getElementById('loops-startup-message');
  const detail = document.getElementById('loops-startup-error');
  const reload = document.getElementById('loops-startup-reload');
  const root = document.getElementById('root');
  let finished = false;
  const showError = (reason) => {
    if (finished) return;
    message.textContent = 'A interface não iniciou. Envie esta mensagem para corrigirmos:';
    detail.textContent = String(reason || 'O JavaScript não carregou ou não montou a tela.');
    detail.hidden = false;
    reload.hidden = false;
  };
  reload.addEventListener('click', () => window.location.reload());
  window.addEventListener('error', (event) => {
    const target = event.target;
    showError(event.message || (target && (target.src || target.href)) || 'Falha ao carregar um arquivo da interface.');
  }, true);
  window.addEventListener('unhandledrejection', (event) => showError(event.reason));
  const check = () => {
    if (root && root.childElementCount > 0) {
      finished = true;
      overlay.hidden = true;
      return true;
    }
    return false;
  };
  new MutationObserver(check).observe(root, { childList: true, subtree: true });
  setTimeout(() => { if (!check()) showError('A tela permaneceu vazia por 20 segundos. Verifique o bundle JavaScript e o processo WebKit.'); }, 20000);
})();
</script>
'''

html = html.replace("</body>", diagnostics + "\n</body>")
index.write_text(html, encoding="utf-8")
print(f"Diagnóstico de inicialização adicionado em {index}")
