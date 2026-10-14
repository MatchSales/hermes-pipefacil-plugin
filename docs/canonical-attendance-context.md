# Confirmed attendance context and reusable sessions

Execution v2 uses one native session lane per profile, conversation/channel, agent,
assignment generation and published revision. A new execution does **not** create a
new session. Native session expiration/compaction and real configuration/assignment
boundaries can still rotate sessions. The `context-v1` namespace avoids adopting
older lanes that were written without these recovery guards.

Every turn loads confirmed CRM history, including human/device messages. Unavailable
history fails before generation or delivery. Successful turns retain their native
context and model cache. Legacy integrations without execution v2 keep their existing
behavior; v2 external integrations use the same safeguards as managed agents.

## Cancellation and exact-turn recovery

While generation/delivery is active, the plugin checks the existing authenticated
execution context endpoint every two seconds (three-second HTTP timeout). Only a
confirmed 401/403/404/409/410 or expiration invalidates the turn. Network failures,
429 and 5xx are not cached as revocation. No polling runs for idle conversations or
after a terminal handoff. The CRM's final send-side authorization remains mandatory;
cancellation is an optimization, not the delivery safety boundary.

On invalidation, local tool/delivery authorization is revoked immediately, the native
runner receives a hard interrupt and the plugin waits up to ten seconds for its
actual turn to finish. Cancelling an asyncio waiter alone is not proof that a model
or HTTP thread stopped. A timeout leaves a durable recovery checkpoint; no following
turn may reuse that context while a native write lease or uncertain effect remains.

Before dispatch, `turn_checkpoints` records the exact session, job, native input ID
and active-row prefix. Under the existing per-deal serialization lock, failed or
interrupted turns use Hermes' native `rewind_to_message` with its write-lease and
compare-and-swap guards. The first added row must carry this execution's native
input ID; a later unrelated user input blocks rollback. The successful prefix stays
active. Discarded rows become inactive, remain available for audit, and the warm
agent cache is evicted so it cannot reintroduce discarded output.

This is a context rollback, **not** an undo of effects. Confirmed partial messages,
notes and other CRM writes remain committed. A later turn reads the confirmed
partial delivery from CRM. Original provider message IDs and timestamps stay intact
in the CRM envelope; only the internal Hermes input marker identifies the execution.

## Failure and recovery

A lost provider response without a confirmed receipt remains uncertain: there is no
automatic resend, rollback or new generation for that deal until the existing effect
journal is reconciled. Other deals can continue. Definitively rejected effects and
`NOT_APPLIED` receipts are safe to rewind. A restart reloads pending checkpoints
before allowing another turn; no in-memory flag is relied upon.

`pending_context_recoveries` in the plugin status counts non-running checkpoints.
Inspect the private journal and native session for the affected job. Use the existing
operator effect reconciliation procedure with verified evidence; the next new event
retries context recovery, never the old send. A changed prefix/compression continuation
or a foreign input is deliberately refused for operator review. Do not delete a
checkpoint just to bypass an uncertain effect. Never apply a generic `/undo 1` while
another turn is active.

The runtime must provide guarded native rewind and profile-local session storage.
An unsupported runtime fails closed for v2; the pinned dev runtime is verified in
this change. No Hermes upstream files are patched. Native transcripts and confirmed
conversation history still grow with actual usage; this change removes per-message
session proliferation, not the need for an eventual audit retention policy.

## Validation

Tests use the dev container's actual Python/Hermes runtime, native adapter lifecycle,
SessionStore/SQLite and loopback HTTP, with deterministic model/provider substitutes:

- Twenty turns, including an obsolete response, reuse one session; the confirmed
  prefix remains and the discarded answer never reaches subsequent context.
- Revocation during generation invokes the interrupt path and waits for completion.
- Confirmed partial delivery is preserved and supplied by CRM to the next turn.
- Unknown delivery blocks that deal, allows another deal, and resumes only after
  verified reconciliation, without repeating the old send.
- Reopened journals recover exact checkpoints; live worker leases, concurrent
  compaction and unrelated inputs refuse unsafe rollback.
- Unavailable CRM history produces no generation or sending.

These tests do not publish to Kafka, call a paid model API or send WhatsApp messages.
They validate protocol, context persistence and concurrency boundaries; interruption
latency of a real model provider remains provider-dependent.
