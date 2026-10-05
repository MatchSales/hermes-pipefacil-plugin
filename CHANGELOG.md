# Changelog

## 0.4.0

- Require the original AI-agent HMAC secret, support current/next signatures and verify the exact JSON before gzip. Handle both raw gzip and Hermes' already-decompressed shared-listener requests.
- Atomically persist message receipts and bounded per-conversation jobs. Wait for native background completion before advancing; recover only unstarted recent jobs and revoke effects on timeout/cancellation.
- Add profile-local `media/` catalogs and `fileId` sends. Use the existing public CRM upload API, cache receipts by content/profile/API credential, and renew temporary storage URLs before each send.
- Pass current audio to native Hermes STT; retain image/document support. Validate media signatures, pin public DNS connections, disable redirects/proxies and expire inbound cache files.
- Enforce the public toolset at runtime, including wrapper calls. Add `pipefacil_current_deal`, configured writable fields/stages, fresh contact/responsible checks and PATCH readback confirmation.
- Journal HTTP effects before writes; reuse confirmed receipts and stop automatic repeats after ambiguous outcomes. Add private operator reconciliation, real readiness/counters, API response bounds and strict JSON envelopes.
- Add CI against official/current unmodified Hermes, real HTTP/shared-ingress/native-lifecycle regression tests, and migration/operation documentation.

## 0.3.5

- Ignore messages older than five minutes, missing/invalid message timestamps, and timestamps more than 30 seconds in the future. Use the message's original time, including for `/reset`, rather than the webhook delivery header. Configure `platforms.pipefacil.extra.max_message_age_seconds` from 1 to 3600 seconds (default 300).
- Persist inbound message receipts per profile and conversation before dispatch. Replays, reconnects, and gateway restarts cannot readmit the same message; `/reset` preserves the receipts. Check writable storage before connecting; if it later fails, refuse admission with HTTP 503.
- Keep busy, interrupt, onboarding, internal errors, and delayed recovery notices out of public chats on older Hermes versions. Allow automatic final answers only within the exact live customer turn, plus explicit plugin `/reset` replies.
- Regression-test the historical 27-message burst, concurrent/restarted receipt storage, and Hermes 0.21.5's real busy and automatic final-answer paths without messaging clients.

## 0.3.4

- Keep final answers addressed to the lead after split sends, without narrating API or delivery status. Reuse the accepted texts as the final answer when they already answer the customer completely.

## 0.3.3

- Avoid an automatic final reply repeating exactly the text already API-accepted through `pipefacil_send_messages` in that same live turn. New final content and subsequent turns still send normally.

## 0.3.2

- Keep trusted webhook facts alive for Hermes' actual background-processing lifecycle, instead of the short admission call.
- Bind each tool worker to its exact live event; queued messages cannot replace its deal or attachment context. Revoke access on completion, error, and cancellation.
- Clarify separate local tool invocations and automatic final-message delivery.

## 0.3.1

- Resolve active tool sessions through the gateway's live routing store, including secondary profiles in a shared gateway; reject stale, cross-profile, and non-Pipefacil sessions.
- Fix `/reset` on Hermes 0.21.5 and hosts with newer deletion signatures. Delete only the finalized predecessor's transcript and request dumps; preserve the new session, other profiles, and observability.
- Keep gateway setup notices, including `/sethome`, out of public lead chats on older hosts.
- Clarify automatic delivery of ordinary final replies, and allow read-only discovery of approved files with `knowledge/`.
- Verify these paths against Hermes 0.21.5 (`749220ef`) with two multiplexed profiles.

## 0.3.0

- Accept Pipefacil webhooks without checking signature or timestamp; only the Pipefacil API key is required to connect. Restrict access at the ingress before exposing the callback.
- Support Hermes versions whose `MessageEvent` does not define `reply_expected`, including Hermes 0.21.5.

## 0.2.0

- Handle `/reset` in Pipefacil chats as a Hermes session reset, clearing the prior local transcript and excluding pre-reset CRM history from future model context.
- Add `pipefacil_send_messages` for up to two API-accepted text, image, or document messages before Hermes sends its final response.
- Restrict outbound media to exact HTTPS links listed in the active profile's `SOUL.md`.
- Download supported attachments from current signed webhook messages and pass local paths/MIME types into Hermes' native media handling.
- Add a profile-scoped, read-only file tool for operator-approved knowledge and current lead attachments.
- Bind deal updates to the current authenticated webhook and restrict `/reset` to configured test numbers.

## 0.1.0

- Receive and authenticate Pipefacil `message.received` webhooks.
- Load recent conversation history and reply through the Pipefacil API.
- Add a scoped tool for updating Pipefacil deals.
- Fall back to available Hermes conversation history when the Pipefacil history API is unavailable.
