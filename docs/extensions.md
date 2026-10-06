# Profile-owned extensions (0.4.6, API revision 1)

Keep `pipefacil-platform` as an unmodified installation from its repository. Put
client code in a separate native Hermes plugin under the profile's `plugins/`.
Business facts, text and IDs belong in that plugin's settings or the profile's
SOUL/knowledge files. A template supplies starting configuration, not a private
copy of the platform transport.

```yaml
plugins:
  enabled: [pipefacil-platform, example-business]
  entries:
    example-business:
      settings:
        closing_message: Vou encaminhar seu atendimento.
platform_toolsets:
  pipefacil: [pipefacil, example_business]
platforms:
  pipefacil:
    extra:
      extension_plugins: [example-business]
      member_user_id: operator-configured-agent-user-id
      handoff_user_id: operator-configured-human-user-id
```

Every configured extension is required: missing, disabled, failed, duplicated,
wrong-profile or unsupported-revision descriptors make the adapter unready and
block its public tools. With no configured extensions, the existing behavior
and six common tools are retained.

## Discovery and API

An extension registers a synchronous native `gateway_platform_event` hook.
For `event_type=pipefacil.extension.describe.v1`, it returns a descriptor only
when `profile_home` equals the resolved home captured at registration:

```python
{
    "name": ctx.manifest.name,
    "version": ctx.manifest.version,
    "api_revision": 1,
    "profile_home": str(home),
    "tools": ["example_fixed_handoff"],
    "toolsets": ["example_business"],
    "disabled_tools": ["pipefacil_handoff"],
    "callbacks": {"prepare_event": prepare_event},
}
```

Register owned tools with `ctx.register_tool`, using the extension's own toolset.
The descriptor declares those names to the public-channel policy. It cannot
replace common names or disable names outside the common set. A business plugin
can hide the general update/handoff tools and expose its fixed constrained flow.

Invoke the same native hook with `event_type=pipefacil.extension.api.v1` and the
current resolved `profile_home` to obtain exactly one matching provider
`pipefacil-platform`, revision 1. The result exposes profile-scoped `api`,
`adapter`, `tools`, `security`, `state`, `network`, `handoff`, `library`, and
`stored_audio`. Resolve lazily after plugin registration; do not import another
plugin's guessed loader namespace. No changes to Hermes core are necessary.

These are trusted in-process plugin contracts, not a public webhook or a model
tool. Extension code is responsible for fixed business criteria and validated
operator settings. Shared routing, lead admission, CRM authorization, effect
journaling and delivery checks remain mandatory.

## Lifecycle callbacks

Callbacks accept these keyword arguments; use `**kwargs` for additive changes.
All may be sync or async except `before_send` and `format_text`, which are sync.

| Callback | Arguments and behavior |
| --- | --- |
| `connect` | `adapter`; prepare persistent private state/tasks; failure makes the profile unready |
| `disconnect` | `adapter`; stop tasks before the shared state closes |
| `before_event` | `adapter, payload, contact, channel, chat_id, seq`; after shared lead/reset/admin handling, before history/media/AI; `False` suppresses the conversation |
| `reset` | `adapter, chat_id`; after successful shared reset; retain CRM effect receipts |
| `prepare_event` | `adapter, event, context, history_text, current_text`; add private turn facts/instructions |
| `complete` | `adapter, event, outcome, context`; context is already inactive; cleanup/revocation continues if a callback fails |
| `before_send` | `adapter, context, message`; immediately before effect admission and again before message POST; `False` or error prevents delivery |
| `format_text` | `text, adapter, context`; must return text; errors prevent delivery |

Health reports `extensionApiRevision` and configured extension names/versions.
Private pause and outbox data can remain in their existing profile-local paths;
separating source code does not require moving or deleting durable state.

## Terminal handoff

A fixed business tool delegates to `handoff.execute(adapter, chat_id, context,
api_key, {"properties": final_properties, "message": closing_text}, active)`.
All fields/stages must satisfy the shared operator allowlists. A successful
same-argument repeat returns the saved receipt. An uncertain outcome blocks
retries, later CRM access and sends, including automatic final delivery.

The order is fixed: verify final fields/stage, send the closing text, then PATCH
**only** `responsibleUserId` last. Verify the transfer from that PATCH receipt;
never GET the lead after assignment revokes visibility. A private outbox can
use `handoff.transfer_responsibility` only after freshly authorizing the lead,
verifying its nonterminal writes, and durably reserving the attempt. An expired
transfer lease or ambiguous response needs operator review, not automatic replay.

## Generated audio

A private provider can generate/upload speech using the adapter's serialized
`effect` journal. `stored_audio(adapter, context, upload_receipt, digest)` only
accepts an upload/generation already journaled as accepted for that exact job.
Pass its opaque result as `_stored_audio` with `type=audio` to
`adapter.send_api_message`. The shared plugin checks profile/turn binding,
renews the storage URL, revalidates CRM access, and journals delivery. Provider
credentials, voice IDs and generation code stay in the business plugin.
