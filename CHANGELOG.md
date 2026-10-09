# Changelog

## Unreleased

## 0.5.2

- Suppress terminal Hermes failed-turn responses even during a live customer reply. Keep the entire provider diagnostic and retry guidance private, including failures after preliminary tool effects. Preserve a failed inbox/extension completion outcome without resending or clearing conversation state.
- Keep delivery failures out of customer chats, including errors during an active reply. Disable the gateway's generic resend, formatting banner and exhausted-retry notice; preserve the failed result, internal logs, effect journal and native delivery metrics where supported.
- Disable channel warning presentation independently of profile settings. Uncertain WhatsApp delivery remains an operator reconciliation task.

## 0.5.1

- Store approved outbound images/documents in private R2 and send stable HTTPS `mediaLink` URLs through the existing CRM API. No backend change or new CRM endpoint required.
- Include the media gateway, immutable capability URLs, profile-specific upload tokens, bounded validation, streamed reads, range/HEAD/ETag support and deployment instructions in this plugin repository.
- Isolate and validate upload receipts by profile, CRM/storage origins, credentials, content, filename and MIME; exclude legacy temporary receipts. Preserve effect journaling, ownership revalidation and ordered handoff.
- Validate approved external downloads with public DNS pinning, no redirects/proxies and a 16 MiB limit. Protect upload credentials from public text/captions.
- Require R2 gateway configuration for media sends; fail before the message POST with no expiring-link fallback. Report `mediaPersistenceRevision: 2`. Extension API revision 1, text and stored audio remain compatible.
- Cover eight profiles, receipt reuse/isolation, actual binary HTTP delivery, gateway access control and explicit/ambiguous failures. Does not rewrite or resend historical messages.

## 0.5.0

- Store all outbound images/documents as permanent conversation media assets, including approved external URLs, and send `mediaAssetId` instead of a temporary custom-field link. Applies to every profile using the shared adapter.
- Isolate persistent upload caches by profile, API origin, credential, content, filename and MIME; exclude legacy receipts. Preserve effect journaling, ownership revalidation and ordered handoff.
- Add bounded, validated downloads for approved external images/documents with public DNS pinning and no redirects/proxies.
- Require the backend `POST /api/v1/conversations/media` API before upgrading. Fail before sending media when unavailable, with no expiring-link fallback. Report `mediaPersistenceRevision: 1` in health. Existing revision 1 business extensions and stored audio remain compatible.
- Cover eight profiles, permanent-id reuse, actual multipart HTTP delivery and explicit/ambiguous failures. Historical CRM messages require a separate backend repair.

## 0.4.6

- Add revision 1 of profile-owned extensions through native scoped Hermes hooks: required dependencies, lifecycle callbacks, owned toolsets, optional disabling of common tools, and health metadata. Missing or incompatible extensions fail closed.
- Expose a scoped extension API so business plugins reuse shared routing, authorization, effects, media, and ordered handoff without importing loader namespaces or copying the platform core.
- Add journal-bound stored audio delivery; speech providers remain outside the shared plugin. Extract a shared terminal owner-PATCH primitive for separately reserved business outboxes.
- Document the boundary between the shared plugin, templates, profile settings, and private business code.

## 0.4.5

- Add `pipefacil_handoff`: verify final allowed fields/stage, send the optional closing text, then transfer responsibility as the last mutation. The target is configured per profile with `PIPEFACIL_HANDOFF_USER_ID` (or `extra.handoff_user_id`); the model cannot select another lead or responsible user.
- Confirm transfer from the terminal PATCH receipt without a GET after assignment. Persist the receipt, block subsequent CRM access/writes/messages in the turn, and suppress the automatic final answer after accepted or uncertain handoff. Ambiguous transfers require operator reconciliation, including HTTP errors at the assignment boundary.
- Preserve uncertain outcomes when field PATCH succeeds but readback fails. Refuse handoff while any earlier action in the turn is pending or uncertain.

## 0.4.4

- Silently ignore signed messages without an associated lead before queuing. Verify the lead and its contact through the API before any AI, history, media, admin or `/reset` handling, including jobs queued before the update.
- Suppress replies on missing leads, contact mismatches and failed verification. Log API failures separately from definite 404s. Expose `leadAdmissionRevision` in health for fleet verification.

## 0.4.1

- Decode compressed CRM API responses once. Preserve HTTP errors and the 2 MiB decompressed response limit. Fix gzip/deflate history and upload responses being incorrectly reported as network failures.
- Add response regression tests for GET, multipart POST, compressed errors and oversized/invalid envelopes. Existing uncertain upload outcomes still require operator review; the update does not automatically replay them.

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
