/** Serve real production admin renders and packaged assets without external services. */
import { spawnSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';

const rendered = spawnSync(
  process.env.PLAYWRIGHT_PYTHON ?? '.venv/bin/python',
  ['-m', 'tests.browser.render_overview'],
  { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 },
);
if (rendered.status !== 0) throw new Error(rendered.stderr);

const pages = JSON.parse(rendered.stdout);
const assets = new Map(Object.entries(pages).map(([url, html]) => [url, [html, 'text/html']]));
const files = new Map([
  ['/configuration.html', ['demo/site/configuration.html', 'text/html']],
  ['/assets/demo.js', ['demo/site/assets/demo.js', 'text/javascript']],
  ['/assets/demo.css', ['demo/site/assets/demo.css', 'text/css']],
  ['/assets/admin.css', ['src/chronikwerk/web/static/admin/admin.css', 'text/css']],
  ['/assets/chronikwerk-mark.svg', ['src/chronikwerk/web/static/admin/chronikwerk-mark.svg', 'image/svg+xml']],
  ['/assets/atkinson-hyperlegible-next.woff2', ['src/chronikwerk/web/static/admin/atkinson-hyperlegible-next.woff2', 'font/woff2']],
  ['/assets/atkinson-hyperlegible-mono.woff2', ['src/chronikwerk/web/static/admin/atkinson-hyperlegible-mono.woff2', 'font/woff2']],
  ['/admin/static/admin.js', ['src/chronikwerk/web/static/admin/admin.js', 'text/javascript']],
  ['/admin/static/admin.css', ['src/chronikwerk/web/static/admin/admin.css', 'text/css']],
  ['/admin/static/chronikwerk-mark.svg', ['src/chronikwerk/web/static/admin/chronikwerk-mark.svg', 'image/svg+xml']],
  ['/admin/static/atkinson-hyperlegible-next.woff2', ['src/chronikwerk/web/static/admin/atkinson-hyperlegible-next.woff2', 'font/woff2']],
  ['/admin/static/atkinson-hyperlegible-mono.woff2', ['src/chronikwerk/web/static/admin/atkinson-hyperlegible-mono.woff2', 'font/woff2']],
]);
for (const [url, [path, type]] of files) assets.set(url, [await readFile(path), type]);

const status = { admission: { running: 1, pending: 2, max_running: 2, max_pending: 8 } };
const nestedOverlay = (values) => Object.entries(values).reduce((overlay, [path, value]) => {
  const parts = path.split('.');
  let cursor = overlay;
  for (const part of parts.slice(0, -1)) cursor = cursor[part] ??= {};
  cursor[parts.at(-1)] = value;
  return overlay;
}, {});
const beforeValues = {
  'admission.max_pending': 12,
  'admission.max_running': 2,
  'hardening.transport.trust_env': false,
};

const readJson = async (request) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
};

const sendJson = (response, body, statusCode = 200) => {
  response.writeHead(statusCode, {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-store',
  });
  response.end(JSON.stringify(body));
};

createServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
  if (pathname === '/admin/api/v1/status') {
    sendJson(response, status);
    return;
  }
  if (pathname === '/admin/api/v1/status/storage-check') {
    sendJson(response, { storage: { writable: true } });
    return;
  }
  if (pathname === '/admin/api/v1/config/validate' && request.method === 'POST') {
    const body = await readJson(request);
    const values = body.values ?? {};
    const securityChange = Object.keys(values).some((path) => path.startsWith('hardening.transport.'));
    if (securityChange && !body.security_acknowledged) {
      sendJson(response, {
        code: 'security_acknowledgement_required',
        message: 'Acknowledge the security effect before continuing.',
        errors: [],
      }, 422);
      return;
    }
    sendJson(response, {
      valid: true,
      overlay: nestedOverlay(values),
      diff: Object.entries(values).map(([path, after]) => ({
        path, before: beforeValues[path] ?? null, after,
      })),
      revision: 'fixture-current-revision',
    });
    return;
  }
  if (pathname === '/admin/api/v1/config/staged' && request.method === 'PUT') {
    sendJson(response, {
      revision: 'fixture-staged-revision',
      previous_revision: 'fixture-current-revision',
      restart_required: true,
    });
    return;
  }
  if (pathname === '/admin/api/v1/session' && request.method === 'POST') {
    sendJson(response, { authenticated: true });
    return;
  }
  const asset = assets.get(pathname);
  if (!asset) {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, { 'Content-Type': asset[1], 'Cache-Control': 'no-store' });
  response.end(asset[0]);
}).listen(4177, '127.0.0.1');
