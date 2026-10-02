# Changelog

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
