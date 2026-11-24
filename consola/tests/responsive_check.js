// Recorre las pantallas de la consola (modo demo) a un ancho dado y avisa si alguna desborda horizontalmente.
// Uso (desde consola/): NODE_PATH=$(npm root -g) node tests/responsive_check.js <carpeta-capturas> <ancho> <alto> [pantallas]
const { chromium } = require('playwright');
const { spawn, execFileSync } = require('child_process');
const fs = require('fs'), os = require('os'), path = require('path');
const [OUT, W, H, ONLY] = [process.argv[2], +process.argv[3], +process.argv[4], process.argv[5]];
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'itier-'));
const env = { ...process.env, ITIER_CONSOLE_DEMO: '1', ITIER_CONSOLE_COOKIE_SECURE: '0', ITIER_CONSOLE_DB: path.join(tmp, 'c.sqlite3'),
  ITIER_CONSOLE_SECRET: 'x'.repeat(40), ITIER_LOG_LEVEL: 'WARNING' };
const srv = spawn('.venv/bin/python', ['-m', 'uvicorn', 'itier_console.main:app', '--host', '127.0.0.1', '--port', '8765'], { env, stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  for (let i = 0; i < 80; i++) { try { const r = await fetch('http://127.0.0.1:8765/healthz'); if (r.ok) break; } catch (e) {} await sleep(500); }
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  const pg = await ctx.newPage();
  const base = 'http://127.0.0.1:8765';
  await pg.goto(base + '/login');
  await pg.fill('input[name=username]', 'admin'); await pg.fill('input[name=password]', 'itier-demo-admin'); await pg.click('button[type=submit]');
  await pg.waitForURL('**/login/2fa/enrolar');
  const sec = (await pg.innerText('.codebox code')).trim();
  const code = execFileSync('.venv/bin/python', ['-c', 'import pyotp,sys;print(pyotp.TOTP(sys.argv[1]).now())', sec]).toString().trim();
  await pg.fill('input[name=code]', code); await pg.click('button[type=submit]'); await pg.waitForLoadState(); console.log('tras 2FA:', pg.url());
  if (pg.url().includes('cambiar-clave')) { await pg.fill('input[name=nueva]', 'Clave-Demo-12345'); await pg.fill('input[name=repetir]', 'Clave-Demo-12345'); await pg.click('button[type=submit]'); await pg.waitForLoadState(); }
  console.log('antes de cliente:', pg.url()); await pg.goto(base + '/clientes/nuevo'); await pg.fill('input[name=nombre]', 'Diario Los Andes'); await pg.click('.content form button[type=submit], .content form .u-btn'); await pg.waitForLoadState();
  let pages = { tablero: '/', clientes: '/clientes', ficha: '/clientes/diario-los-andes', equipos: '/equipos', tickets: '/tickets', activos: '/clientes/diario-los-andes/activos',
    canales: '/canales', usuarios: '/usuarios', personas: '/clientes/diario-los-andes/personas', horas: '/reportes/horas', bandeja: '/bandeja', manual: '/manual', auditoria: '/auditoria',
    ticket: '/tickets/1042', activo_nuevo: '/clientes/diario-los-andes/activos/nuevo', m365: '/m365/aplicacion' };
  if (ONLY) { const k = ONLY.split(','); pages = Object.fromEntries(Object.entries(pages).filter(([n]) => k.includes(n))); }
  for (const [name, p] of Object.entries(pages)) {
    await pg.goto(base + p); await pg.waitForLoadState('networkidle');
    const m = await pg.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth, document.documentElement.scrollHeight]);
    console.log(name.padEnd(12), 'scrollW', m[0], 'clientW', m[1], m[0] > m[1] + 1 ? 'OVERFLOW-X' : 'ok', 'alto', m[2]);
    if (m[0] > m[1] + 1) { const w = await pg.evaluate(() => { const cw = document.documentElement.clientWidth; return [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > cw + 1).slice(0, 8).map(e => e.tagName + '.' + (e.className && e.className.baseVal === undefined ? e.className : '') + ' ' + Math.round(e.getBoundingClientRect().right)); }); console.log('  anchos:', w.join(' | ')); }
    await pg.screenshot({ path: path.join(OUT, name + '.png'), fullPage: process.env.FULL === '1' });
  }
  await b.close(); srv.kill('SIGTERM');
})().catch(e => { console.error(e); srv.kill('SIGTERM'); process.exit(1); });
