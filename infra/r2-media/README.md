# Dedicated R2 storage for shared SDR media

This gateway belongs to the Hermes plugin repository. It uses the existing CRM
`mediaLink` API; it requires no changes to the PipeFácil backend. Deploy it once,
then give each SDR a separate upload credential. The bucket stays private.

POST `/upload` accepts a binary commercial asset (maximum 16 MiB), a bearer token,
its MIME and a percent-encoded `X-Media-Filename`. The token's SHA-256 hash maps to
an operator-assigned profile namespace in the Worker secret `UPLOAD_TOKENS_JSON`.
The client cannot choose a namespace. Bytes/signatures, MIME and filename are
validated before storage; redirects, object listing and anonymous writes are absent.

The immutable key is HMAC-SHA-256 of namespace, content hash, MIME and filename,
keyed with that profile's token. Retries reuse the key and conditional PUT prevents
replacement. GET/HEAD `/media/<key>` streams the object with MIME, ETag, range and
cache headers. Anyone holding this unguessable capability link can read the asset;
there is no expiry. This storage is only for approved outbound commercial assets,
not confidential customer attachments. A revoked upload token leaves historical
links intact. Keep objects and the gateway available while CRM history needs them;
never configure a short expiry lifecycle. Removing an object breaks its history link.

## Provision and deploy

Use Wrangler 4.148.0 or newer. Authenticate to the intended account and set its
`CLOUDFLARE_ACCOUNT_ID`. Do not put Cloudflare admin credentials in Hermes profiles.
Run these commands from this directory, with that account selected:

```sh
wrangler r2 bucket create hermes-sdr-media  # once; reuse it if already provisioned
python3 provision.py --directory .media-provision sdr-first sdr-second
wrangler types
wrangler deploy --dry-run
node --test worker.test.mjs
wrangler deploy --secrets-file .media-provision/worker-secrets.json
```

The helper writes a new directory with mode 0700 and files with mode 0600; it
refuses to replace an existing credential set. Store `credentials.json` in your
secret manager before removing the temporary directory. Configure the corresponding
profile token as `PIPEFACIL_MEDIA_UPLOAD_TOKEN` and the deployed Worker origin as
`PIPEFACIL_MEDIA_BASE_URL` in each SDR's `.env`. Never expose tokens through model
instructions, business tools, logs or version control. Keep every currently authorized
profile in `UPLOAD_TOKENS_JSON` when updating the map; omitted entries are revoked.
Do not rerun credential generation during ordinary code-only deployments.

`wrangler.jsonc` intentionally contains no account ID, allowing independent plugin
installations to use their own account. Do not enable the bucket's `r2.dev` URL.
The Worker serves the URLs used by the CRM. A custom domain may be configured
before first use; retain any previously published hostnames for historical links.

## Verification

Local tests check retries, isolation, GET/HEAD/ranges/ETags, access control and
actual/declared upload bounds. For platform validation, run `wrangler dev` with a
local private secret map and local R2 state. The generated `worker-configuration.d.ts`
comes from `wrangler types` and must not be hand-written. Type-check with current
TypeScript and Node types:

```sh
npx tsc --allowJs --checkJs --noEmit --skipLibCheck --target ES2022 \
  --module ESNext --moduleResolution bundler worker.mjs worker-configuration.d.ts
```

For a deployed smoke test, upload a synthetic JPEG/PDF using a provisioned token,
fetch its receipt URL, repeat the upload and check the same URL is returned. Test
HEAD/ranges and an invalid token. This verification does not send a CRM/WhatsApp
message. Remove only the synthetic smoke object's exact key afterward using
`wrangler r2 object delete hermes-sdr-media/<key> --remote`.
