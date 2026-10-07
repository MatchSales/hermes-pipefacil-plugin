// The bucket is private. Only approved commercial media is exposed through
// immutable, unguessable capability URLs; there is no public listing endpoint.
const MAX_BYTES = 16 * 1024 * 1024;
const encoder = new TextEncoder();
const office = new Set(['application/msword', 'application/vnd.ms-excel', 'application/vnd.ms-powerpoint']);
const xmlOffice = new Set([
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
]);
const allowed = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp',
  'application/pdf', 'text/plain', 'text/csv', ...office, ...xmlOffice]);
/** @param {ArrayBuffer} buffer */
const hex = buffer => Array.from(new Uint8Array(buffer), byte => byte.toString(16).padStart(2, '0')).join('');
/** @param {Uint8Array<ArrayBuffer>} bytes */
const sha256 = async bytes => hex(await crypto.subtle.digest('SHA-256', bytes));
/** @param {number} status @param {string} code */
const error = (status, code) => Response.json({error: code}, {status, headers: {'Cache-Control': 'no-store'}});

/** @param {Uint8Array<ArrayBuffer>} bytes @param {string} mime */
function validPrefix(bytes, mime) {
  /** @param {number[]} values */
  const starts = values => values.every((value, index) => bytes[index] === value);
  const text = new TextDecoder().decode(bytes.subarray(0, 64));
  if (mime === 'image/jpeg') return starts([255, 216, 255]);
  if (mime === 'image/png') return starts([137, 80, 78, 71, 13, 10, 26, 10]);
  if (mime === 'image/gif') return text.startsWith('GIF87a') || text.startsWith('GIF89a');
  if (mime === 'image/webp') return text.startsWith('RIFF') && text.substring(8, 12) === 'WEBP';
  if (mime === 'application/pdf') return text.startsWith('%PDF-');
  if (office.has(mime)) return starts([208, 207, 17, 224, 161, 177, 26, 225]);
  if (xmlOffice.has(mime)) return starts([80, 75, 3, 4]);
  return text.trim().length > 0;
}

/** @param {Request} request */
async function boundedBody(request) {
  const declared = request.headers.get('Content-Length');
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_BYTES)) return null;
  if (!request.body) return null;
  const reader = request.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  if (!size || (declared !== null && Number(declared) !== size)) return null;
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

/** @param {Request} request @param {Env} env @param {string} origin */
async function upload(request, env, origin) {
  if (!env.UPLOAD_TOKENS_JSON) return error(503, 'storage_not_configured');
  const token = /^Bearer ([A-Za-z0-9_-]{32,128})$/.exec(request.headers.get('Authorization') || '')?.[1];
  if (!token) return error(401, 'unauthorized');
  // Hash lookups avoid comparing plaintext secrets. Namespace comes exclusively
  // from the operator's secret map, never from a caller-supplied path/header.
  const namespaces = JSON.parse(env.UPLOAD_TOKENS_JSON);
  const tokenHash = await sha256(encoder.encode(token));
  const namespace = Object.hasOwn(namespaces, tokenHash) ? namespaces[tokenHash] : null;
  if (typeof namespace !== 'string' || !/^[a-z0-9][a-z0-9-]{0,79}$/.test(namespace)) return error(403, 'unauthorized');
  const mime = (request.headers.get('Content-Type') || '').toLowerCase().split(';')[0].trim();
  if (!allowed.has(mime)) return error(415, 'unsupported_media');
  let filename;
  try { filename = decodeURIComponent(request.headers.get('X-Media-Filename') || ''); }
  catch { return error(400, 'invalid_filename'); }
  if (!filename || filename.length > 200 || /[\x00-\x1f\x7f/\\"]/.test(filename)) return error(400, 'invalid_filename');
  const body = await boundedBody(request);
  if (!body) return error(413, 'invalid_media_size');
  if (!validPrefix(body, mime)) return error(415, 'invalid_media_content');
  const contentHash = await sha256(body);
  const hmac = await crypto.subtle.importKey('raw', encoder.encode(token), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  const key = hex(await crypto.subtle.sign('HMAC', hmac, encoder.encode(JSON.stringify([namespace, contentHash, mime, filename]))));
  // Content + metadata + profile credential determine the immutable key.
  // Conditional PUT also makes a retry after a lost response safe.
  await env.MEDIA_BUCKET.put(key, body, {
    onlyIf: {etagDoesNotMatch: '*'}, sha256: contentHash,
    httpMetadata: {contentType: mime, contentDisposition: `inline; filename*=UTF-8''${encodeURIComponent(filename)}`,
      cacheControl: 'public, max-age=31536000, immutable'},
    customMetadata: {namespace, sha256: contentHash},
  });
  return Response.json({data: {key, url: `${origin}/media/${key}`, filename,
    contentType: mime, sizeBytes: body.byteLength, sha256: contentHash}}, {headers: {'Cache-Control': 'no-store'}});
}

/** @param {string} value @param {number} size */
function parseRange(value, size) {
  const match = /^bytes=(\d*)-(\d*)$/.exec(value);
  if (!match || (!match[1] && !match[2])) return null;
  let offset, end;
  if (!match[1]) {
    const suffix = Number(match[2]);
    if (!Number.isSafeInteger(suffix) || suffix <= 0) return null;
    offset = Math.max(0, size - suffix);
    end = size - 1;
  } else {
    offset = Number(match[1]);
    end = match[2] ? Math.min(Number(match[2]), size - 1) : size - 1;
  }
  if (!Number.isSafeInteger(offset) || !Number.isSafeInteger(end) || offset >= size || offset > end) return null;
  return {offset, length: end - offset + 1};
}

/** @param {Request} request @param {Env} env @param {string} key */
async function download(request, env, key) {
  const object = await env.MEDIA_BUCKET.head(key);
  if (!object) return error(404, 'not_found');
  const headers = new Headers({
    'X-Content-Type-Options': 'nosniff', 'Access-Control-Allow-Origin': '*',
    'Accept-Ranges': 'bytes', 'ETag': object.httpEtag,
  });
  object.writeHttpMetadata(headers);
  const tags = request.headers.get('If-None-Match')?.split(',').map(tag => tag.trim().replace(/^W\//, ''));
  if (tags?.includes('*') || tags?.includes(object.httpEtag)) return new Response(null, {status: 304, headers});
  if (request.method === 'HEAD') {
    headers.set('Content-Length', String(object.size));
    return new Response(null, {headers});
  }
  let range;
  const requested = request.headers.get('Range');
  const ifRange = request.headers.get('If-Range');
  if (requested && (!ifRange || ifRange === object.httpEtag)) {
    range = parseRange(requested, object.size);
    if (!range) {
      headers.set('Content-Range', `bytes */${object.size}`);
      return new Response(null, {status: 416, headers});
    }
    headers.set('Content-Range', `bytes ${range.offset}-${range.offset + range.length - 1}/${object.size}`);
  }
  const data = await env.MEDIA_BUCKET.get(key, range ? {range} : undefined);
  if (!data || !('body' in data)) return error(404, 'not_found');
  headers.set('Content-Length', String(range ? range.length : object.size));
  return new Response(data.body, {status: range ? 206 : 200, headers});
}

export default {
  /** @param {Request} request @param {Env} env */
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      if (url.pathname === '/upload' && request.method === 'POST') return await upload(request, env, url.origin);
      const key = /^\/media\/([0-9a-f]{64})$/.exec(url.pathname)?.[1];
      if (key && ['GET', 'HEAD'].includes(request.method)) return await download(request, env, key);
      if (url.pathname === '/health' && request.method === 'GET') {
        return Response.json({service: 'hermes-sdr-media', configured: Boolean(env.MEDIA_BUCKET && env.UPLOAD_TOKENS_JSON)});
      }
      return error(404, 'not_found');
    } catch {
      console.error(JSON.stringify({event: 'media_service_failure'}));
      return error(503, 'media_service_unavailable');
    }
  },
};
