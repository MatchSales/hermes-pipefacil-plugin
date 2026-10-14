# Confirmed attendance context

Managed execution v2 uses a session lane per execution, within the profile and configuration revision. Each new turn loads the confirmed CRM history, including messages sent by humans or other devices. Hermes transcripts remain private audit records; generated output from a previous execution is not reused as conversation history.

If the CRM history cannot be loaded, a managed turn fails before generation or sending. Legacy integrations without execution v2 retain their existing local-context fallback. V2 external integrations receive the same per-execution session isolation.

Regression evidence: a native Hermes adapter receives two executions over loopback HTTP. The first model handler persists a synthetic assistant answer in the real SessionStore, then the CRM rejects its delivery as obsolete. The second handler sees a separate, clean transcript and the confirmed human response from CRM. Only the second answer is sent; the old transcript is retained in its original database for audit. A separate test proves that unavailable CRM history generates and sends nothing.

The test uses the dev container's actual Hermes Python/runtime, real native processing/delivery hooks, SQLite sessions and HTTP sockets, with deterministic model and provider substitutes. It does not send WhatsApp messages or consume model API credits.
