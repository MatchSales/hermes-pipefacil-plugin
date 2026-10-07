import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import worker from './worker.mjs';
const token = 'a'.repeat(48), otherToken = 'b'.repeat(48);
const hash = value => createHash('sha256').update(value).digest('hex');
const bytes = Buffer.from('%PDF-1.7\ncommercial catalog');
function environment() {
  const objects = new Map();
  const env = {
    UPLOAD_TOKENS_JSON: JSON.stringify({[hash(token)]: 'sdr-first', [hash(otherToken)]: 'sdr-second'}),
    MEDIA_BUCKET: {
      async put(key, body, options) {
        assert.equal(options.onlyIf.etagDoesNotMatch, '*');
        assert.equal(options.sha256, hash(body));
        if (!objects.has(key)) objects.set(key, {bytes: body, options});
      },
      async head(key) {
        const item = objects.get(key);
        if (!item) return null;
        return {size: item.bytes.byteLength, httpEtag: '"etag"',
          writeHttpMetadata(headers) {
            headers.set('Content-Type', item.options.httpMetadata.contentType);
            headers.set('Content-Disposition', item.options.httpMetadata.contentDisposition);
            headers.set('Cache-Control', item.options.httpMetadata.cacheControl);
          }};
      },
      async get(key, options) {
        const item = objects.get(key);
        if (!item) return null;
        const range = options?.range;
        const result = range ? item.bytes.subarray(range.offset, range.offset + range.length) : item.bytes;
        return {body: new Blob([result]).stream()};
      },
    },
  };
  return {env, objects};
}
const upload = (env, options = {}) => worker.fetch(new Request('https://media.example/upload', {
  method: 'POST', body: options.body ?? bytes,
  headers: {Authorization: `Bearer ${options.token ?? token}`,
    'Content-Type': options.mime ?? 'application/pdf',
    'X-Media-Filename': options.filename ?? 'catalog.pdf', ...options.headers},
}), env);

test('immutable capability URLs, retry safety, profile isolation and stream delivery', async () => {
  const {env, objects} = environment();
  const first = await (await upload(env)).json();
  const repeated = await (await upload(env)).json();
  const other = await (await upload(env, {token: otherToken})).json();
  assert.deepEqual(first, repeated);
  assert.notEqual(first.data.key, other.data.key);
  assert.equal(objects.size, 2);
  assert.equal(first.data.sha256, hash(bytes));
  assert.match(first.data.url, /^https:\/\/media.example\/media\/[a-f0-9]{64}$/);
  const get = await worker.fetch(new Request(first.data.url), env);
  assert.equal(get.status, 200);
  assert.deepEqual(Buffer.from(await get.arrayBuffer()), bytes);
  assert.equal(get.headers.get('Content-Type'), 'application/pdf');
  assert.match(get.headers.get('Cache-Control'), /immutable/);
  assert.equal(get.headers.get('X-Content-Type-Options'), 'nosniff');
  const head = await worker.fetch(new Request(first.data.url, {method: 'HEAD'}), env);
  assert.equal(head.headers.get('Content-Length'), String(bytes.length));
  assert.equal(await head.text(), '');
  const range = await worker.fetch(new Request(first.data.url, {headers: {Range: 'bytes=0-4'}}), env);
  assert.equal(range.status, 206);
  assert.equal(await range.text(), '%PDF-');
  const suffix = await worker.fetch(new Request(first.data.url, {headers: {Range: 'bytes=-7'}}), env);
  assert.equal(await suffix.text(), 'catalog');
  const invalid = await worker.fetch(new Request(first.data.url, {headers: {Range: 'bytes=9999-'}}), env);
  assert.equal(invalid.status, 416);
  const cached = await worker.fetch(new Request(first.data.url, {headers: {'If-None-Match': 'W/"etag"'}}), env);
  assert.equal(cached.status, 304);
  const changed = await worker.fetch(new Request(first.data.url, {headers: {Range: 'bytes=0-4', 'If-Range': '"old"'}}), env);
  assert.equal(changed.status, 200);
});

for (const [name, options, status] of [
  ['missing token', {token: ''}, 401],
  ['unknown token', {token: 'c'.repeat(48)}, 403],
  ['executable MIME', {mime: 'text/html'}, 415],
  ['spoofed image', {mime: 'image/jpeg', body: Buffer.from('<html>bad</html>')}, 415],
  ['empty body', {body: Buffer.alloc(0)}, 413],
  ['declared oversized body', {headers: {'Content-Length': String(16 * 1024 * 1024 + 1)}}, 413],
  ['actual oversized body', {body: Buffer.alloc(16 * 1024 * 1024 + 1)}, 413],
  ['size mismatch', {headers: {'Content-Length': '1'}}, 413],
  ['filename traversal', {filename: '..%2Fcatalog.pdf'}, 400],
  ['filename header injection', {filename: 'catalog%0D%0A.pdf'}, 400],
]) {
  test(`${name} is rejected before storage`, async () => {
    const {env, objects} = environment();
    assert.equal((await upload(env, options)).status, status);
    assert.equal(objects.size, 0);
  });
}

test('no listing, arbitrary namespaces, public writes or secret disclosures', async () => {
  const {env} = environment();
  for (const path of ['/media/', '/media/../secrets', '/sdr-first/catalog.pdf', '/']) {
    assert.equal((await worker.fetch(new Request(`https://media.example${path}`), env)).status, 404);
  }
  const response = await worker.fetch(new Request(`https://media.example/media/${'f'.repeat(64)}`, {method: 'DELETE'}), env);
  assert.equal(response.status, 404);
  assert.equal((await upload({...env, UPLOAD_TOKENS_JSON: ''})).status, 503);
  assert.equal((await worker.fetch(new Request('https://media.example/health'), env)).status, 200);
});
