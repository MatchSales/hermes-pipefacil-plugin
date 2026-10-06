# Hermes Pipefacil Plugin

A standalone Hermes gateway plugin that connects Pipefacil `message.received` webhooks to a Hermes
profile. It loads recent conversation messages from Pipefacil, lets the profile respond to the lead,
sends the final answer through the Pipefacil API, and provides a narrowly scoped CRM deal update tool.

[Leia em português](README.pt-BR.md).

## Features

- Verifies Pipefacil AI-agent HMAC-SHA256 signatures, including gzip payloads; rejects historical replays.
- Loads recent conversation messages from every participant, not only messages sent by Hermes.
- Replies to the lead through the Pipefacil API.
- Registers `pipefacil_send_messages` to send up to two additional text, image, or document messages before Hermes sends its final answer automatically.
- Downloads current images/documents/audio and uses Hermes' native vision, document readers and transcription.
- Lists approved profile-local `media/` assets, uploads them through the existing CRM API and resolves fresh temporary URLs before sending.
- Durably queues admitted jobs, orders preparation/model execution per conversation and journals HTTP effects before attempting them.
- Falls back to the local Hermes conversation context if Pipefacil history cannot be loaded.
- Treats a standalone `/reset` message as a Hermes command, removes the finished local session transcript,
  and stops sending pre-reset Pipefacil history back to the model for that conversation.
- Registers `pipefacil_update_deal` for updating an explicitly identified deal.
- Reads API credentials from the owning Hermes profile; it does not accept a `workspaceId`.
- Historical attachments are shown as non-text content and are not downloaded.
- Local outbound files come from the profile's `media/` catalog. External links must be HTTPS and listed in that profile's `SOUL.md`.

## Requirements

- A Hermes installation with gateway plugins and the plugin platform registry.
- A Pipefacil API key with `API_ACCESS`, `ADVANCED_API`, conversation read/send access, and deal edit access.
- A public HTTPS endpoint or tunnel that forwards the callback to the Hermes gateway.

Conversation history and outbound replies require Pipefacil's `ADVANCED_API` feature. See the
[Pipefacil API documentation](https://developers.matchsales.com.br/api/).

## Install

Clone the repository into the target profile's plugin directory, then enable the plugin:

```bash
PROFILE=sdr
PLUGIN_DIR="$HOME/.hermes/profiles/$PROFILE/plugins/pipefacil_sdr"
mkdir -p "$(dirname "$PLUGIN_DIR")"
git clone https://github.com/MatchSales/hermes-pipefacil-plugin.git "$PLUGIN_DIR"
hermes -p "$PROFILE" plugins enable pipefacil-platform
```

The profile directory can differ if your Hermes installation uses a custom home. Store credentials
in that profile's `.env`; never commit them:

```dotenv
PIPEFACIL_API_KEY=pf_live_...
PIPEFACIL_WEBHOOK_SECRET=original_ai_agent_secret
```

Restart the gateway after changing plugin files, credentials, or profile configuration.

### Docker and dashboard installation

In the official image, persistent data lives under `/opt/data`. Confirm the exact profile name with
`hermes profile list` inside the container. The **Plugins** page installs into the profile hosting
the dashboard process; selecting another profile in the page header does not change the install
target in this Hermes version. Install explicitly into a secondary profile:

**If you only have the dashboard's Hermes Console:** select the customer-facing profile in the
page header, open **System > Open console**, and confirm the profile name shown in the console
header. Enter one command at a time, without `hermes`, `-p`, or shell commands:

```text
profile
profile list
plugins list --user --plain
```

If the plugin is absent, run
`plugins install https://github.com/MatchSales/hermes-pipefacil-plugin.git --enable`
(the repository must be accessible to this Hermes). If it is disabled, run
`plugins enable pipefacil-platform`. The Console asks for confirmation before changes. Then set
the API key under **Channels > Pipefacil > Configure** in the same profile. To control the
gateway, select the `default` profile in the dashboard header and use **System > Gateway > Start** if stopped, or
**Restart** if already running. Hermes Console does not expose the `gateway` command or the
`/run/service` filesystem.

**If you have a shell on the Docker host**, the equivalent is:

```bash
docker exec -u hermes -it <container> hermes -p <profile> plugins install \
  https://github.com/MatchSales/hermes-pipefacil-plugin.git --enable
docker exec -u hermes <container> hermes -p <profile> plugins list --user --plain
```

Use `-u hermes`: `docker exec` defaults to root in the official image and can leave files that the
gateway cannot modify. This uses Git and does not require `gh`. A warning that `PIPEFACIL_API_KEY`
is missing does not mean installation failed. Set it for the served profile in
**Channels > Pipefacil > Configure** or in that profile's `.env`. The Pipefacil card appears when
the plugin loads in that profile; the API key is required to connect it.

For Docker's shared gateway, start or restart the `default` profile's gateway, which serves
secondary profiles:

```bash
docker exec -u hermes <container> hermes -p default gateway status
docker exec -u hermes <container> hermes -p default gateway start   # if stopped
docker exec -u hermes <container> hermes -p default gateway restart # if already running
```

If the dashboard reports `no such gateway '<profile>'`, first check `hermes profile list` and the
`default` gateway's status. Do not recreate an existing profile. The s6 service under
`/run/service/gateway-<profile>` is temporary; container startup rebuilds it from persistent
profiles.

## Configure the profile

Enable only the restricted Pipefacil tools for this profile:

```yaml
platforms:
  pipefacil:
    enabled: true
    extra:
      host: 127.0.0.1 # used when this profile runs a standalone gateway
      port: 8645      # shared gateways use the default listener's port
      path: /events/message-received
      history_limit: 100 # from 1 to 200
      max_message_age_seconds: 300 # from 1 to 3600; original message time
      allowed_users:
        - "*"
      reset_allowed_users: [] # authorized test numbers with country and area code
      # Optional for a homologation or local Pipefacil server:
      # api_base_url: https://homolog.pipefacil.matchsales.com.br

platform_toolsets:
  pipefacil: [pipefacil]

agent:
  max_turns: 50
```

The wildcard allowlist permits lead identities from authenticated backend events. The backend selects
eligible agent/conversation events; the adapter verifies their origin. Use HTTPS at the public ingress.

Signed messages without a positive `data.deal.seq` are acknowledged with HTTP 200
and `status: ignored`, before admission to the queue. Before processing, the plugin
fetches that lead and verifies its contact ID and phone. A missing lead (404) or a
contact mismatch produces no reply, including for `/reset`, admin requests and
audio. Failed API verification also suppresses replies, with a separate operational
log; it is not classified as an internal contact. No model instruction can bypass
this check. Transient verification errors receive three bounded attempts; an
exhausted check fails the private job instead of marking it completed. Health
reports `leadAdmissionRevision: internal-contact-v2`.

### Replay protection

Messages must have an original `timestamp` with a timezone (or a numeric Unix timestamp in seconds
or milliseconds). By default, messages older than 300 seconds, missing/invalid timestamps, and times
more than 30 seconds in the future are ignored with HTTP 200. A recent delivery header does not make
an old message new. In mixed batches only recent messages are admitted. This also applies to `/reset`.

Admission receipts are stored in `<profile>/pipefacil-state/inbox.sqlite3` for seven days, scoped by
conversation and message identity. The private job queue also stores the webhook for recovery. Preserve the
profile volume across deployments. Reconnects, restarts, and `/reset` preserve these receipts. Storage
failure returns HTTP 503 before the agent runs. After admission, receipts survive processing errors
because a send's outcome may be ambiguous; the same webhook is not automatically run again. A customer
can send a new message to continue the conversation.

To change the five-minute window manually, edit the selected profile's YAML in the dashboard and set
`platforms.pipefacil.extra.max_message_age_seconds`, then restart that profile's gateway. Valid values
are 1–3600 seconds; zero does not disable the protection. Signature authentication is mandatory.

The `pipefacil` toolset contains response tools, an update tool bound to the current event's deal,
and `pipefacil_read_profile_file`. The send tool uses the
destination from the current event; the model cannot choose a phone number. It accepts one or two
messages, and Hermes sends its final answer automatically afterward. A successful result confirms that
the API accepted the request, not that WhatsApp delivery was confirmed. Reads are limited to current
turn attachments and files under this profile's `knowledge/` folder. Do not enable Hermes' `file`
toolset for a public-facing agent; it also exposes writing and patching tools.

For ordinary text replies, the agent should write its final answer directly; Hermes delivers it
automatically. Use `pipefacil_send_messages` only for preliminary split messages or approved media.
If the model repeats those exact accepted texts in its automatic final answer, the plugin reuses their
delivery result instead of sending a duplicate. This check is limited to that same live turn.
When split texts already contain the complete answer, the final answer should use those exact texts
in order. It should not narrate internal API acceptance or delivery-confirmation status to the lead.
To discover approved reference files, call `pipefacil_read_profile_file` with `path: knowledge/`,
then read an exact listed path. Wildcards and listing the profile root are not supported.

### Profile media library

List each HTTPS media URL the agent may share in that profile's `SOUL.md`, with a label and type (`image`
or `document`):

```text
- label: Company overview | type: document | url: https://bucket.example.com/overview.pdf?signature=...
- label: Team photo | type: image | url: https://bucket.example.com/team.jpg?signature=...
```

The plugin accepts only an exact type and URL match from the active profile's `SOUL.md`. Signed links
must remain valid and accessible to WhatsApp when sent; an expired link is reported as an API failure.

## Configure the webhook

For a standalone profile gateway, configure Pipefacil with:

```text
https://<your-public-host>/events/message-received
```

For a secondary profile served by Hermes' default multiplexer, use:

```text
https://<your-public-host>/p/<profile>/events/message-received
```

The shared profile route requires the default gateway's HTTP listener to be enabled and reachable
from the public host. Run `hermes -p default gateway status` to find its actual address and port:
in a clean Docker image tested, the shared callback used port `8642` even though
`platforms.pipefacil.extra.port` was `8645`. The health check appends `/health` to the callback,
for example `http://127.0.0.1:8642/p/<profile>/events/message-received/health`. Port `8645`
applies to a standalone Pipefacil gateway.

The adapter checks `X-PipeFacil-Signature-256` and `X-PipeFacil-Timestamp`, using the literal original
secret and `timestamp + "." + original JSON bytes` before gzip. The endpoint acknowledges durable admission; the model turn and outbound
API reply run in the background. A `200` response does not confirm the agent replied successfully.

## Version 0.4 migration

Set the original `PIPEFACIL_WEBHOOK_SECRET` for every served profile; do not prefix the secret with
`sha256=` or decode it as hex/base64. That prefix belongs to the request signature header.
Set `PIPEFACIL_MEMBER_USER_ID` to the agent's responsible **userId** to enable mutations;
`PIPEFACIL_CUSTOM_FIELDS` and `PIPEFACIL_STAGE_IDS` are comma-separated allowed slugs/IDs.
`pipefacil_current_deal` reads authorized live CRM facts before updates, which require readback confirmation.
The adapter enforces the `pipefacil` toolset and blocks other tools at runtime.
Put reference files in `knowledge/`; put outgoing images/documents in `media/` and select them through
`pipefacil_list_media` and `fileId`. Symlinks/hardlinks/hidden files are excluded; uploads are limited to 16 MiB.
Profile state now includes private queued webhook contents, with 0700 directories and 0600 files.
Never-started jobs are recoverable; interrupted or ambiguous writes require operator reconciliation.
See [HTTP architecture, limits, schemas and operations](docs/http-runtime.md) and
[the Portuguese setup instructions](README.pt-BR.md). Native audio transcription requires a configured STT provider.

### Ordered handoff (0.4.5)

Set `PIPEFACIL_HANDOFF_USER_ID` (or `platforms.pipefacil.extra.handoff_user_id`) to this
profile's human responsible **userId**. Handoff also requires `PIPEFACIL_MEMBER_USER_ID`;
without a distinct configured target, the tool refuses to transfer.

Use `pipefacil_handoff` as the last operation. Its optional `properties` use the same field,
custom-field and stage allowlists as `pipefacil_update_deal`; won/lost stage changes are
not allowed in handoff. The plugin verifies these fields first, sends the optional `message`
to the current lead, then PATCHes only `responsibleUserId`. It confirms the returned deal's
identity, contact, pipeline, stage and new owner using the PATCH receipt, without a subsequent
GET that might fail after assignment revokes access.

Accepted or uncertain handoff closes CRM access and further writes/sends for this turn,
including the automatic final answer. Pass customer-facing closing text in `message`, or
omit it if already sent. Use prospective wording, such as “I'll forward your request”; do
not claim transfer before confirmation. A missing/invalid receipt or assignment HTTP failure
requires operator reconciliation and is never automatically retried. Pending or uncertain
prior actions prevent handoff. A repeated identical successful call returns its saved receipt.

The plugin owns this ordering and safety contract. Profiles own destination IDs, allowed fields
and stages, commercial criteria and wording. Client-specific business code can call the same
handoff contract from a separate extension; it must replace its old reassignment path rather
than wrapping the new tool with CRM writes after transfer. Existing custom forks require migration;
updating this repository alone does not change those profiles.

### Automatic tool guidance (0.4.2)

Each Pipefacil lead turn now carries a static, trusted `MessageEvent.channel_prompt` using
Hermes' native per-channel context. It complements the profile's instructions without editing
SOUL.md, and applies to existing conversations on their next turn after updating the plugin
and restarting the gateway. No customer text, filenames, links or secrets are interpolated
into this instruction block.

The guidance tells the agent to list approved media before answering requests such as
“send me the presentation”, choose a returned `fileId`, and send the actual file. Reference
files in `knowledge/` are distinct from the sending library in `media/`. Failed catalog calls
are not empty catalogs, and ambiguous choices require a short clarification. Operators must
still supply the approved files and commercial rules.

For deferred tools, describe the exact tool name, then invoke one local tool per `tool_call`.
If searching is needed, query only the exact name with underscores. Hermes uses lexical
search and can reject a query containing an intent word absent from all tool descriptions.
All tools share their registration and schema descriptions, with Portuguese and
English media/CRM terms to improve discovery.

This is model guidance, not proof that every natural request works. After deployment, ask
for an identifiable synthetic presentation without mentioning tools or IDs, verify the
received attachment, repeat the request to check storage-object reuse, and check an empty
library in a separate test conversation. API acceptance alone is not confirmed delivery.
Update the target profile explicitly and restart its gateway (the `default` gateway for
shared installations). No transcript reset or SOUL edit is required.

## Conversation context

Before each turn, the plugin loads up to `history_limit` recent messages by contact phone, scoped to
the Pipefacil channel when available. It includes inbound and outbound messages from all participants.
The newest webhook message is supplied separately in case it has not yet appeared in API history.

If the history request fails, the agent is instructed to use the local Hermes history available for
that conversation and to ask a short clarifying question if context is missing. That local history may
not contain messages exchanged outside Hermes, and its availability depends on the host's session
restoration support. The API key is still required for outbound replies and CRM updates.

An authorized number in `reset_allowed_users` can send `/reset` by itself to start from a clean context. Hermes opens a new session, the plugin removes
the previous local transcript, and future turns ignore Pipefacil history from before the reset message.
The original CRM messages remain in Pipefacil; this command clears the agent's context without deleting
the customer conversation. Observability records and other profiles' sessions are preserved.

## Compatibility note

Version 0.4 is checked against the official Hermes 0.21.5 image and current upstream source, including two secondary profiles in a shared
gateway. Tool routing uses the gateway's live session index; each profile keeps its own transcripts.
The plugin supports both the legacy and guarded session-deletion signatures.
Trusted tool facts follow the actual background-processing callbacks. Each worker retains its own
event context, and access is revoked when processing completes, fails, or is cancelled.

Pipefacil defaults to `notice_delivery: private` and suppresses gateway setup, busy/interrupt,
onboarding, and internal error notices, including legacy hosts that call `send()` directly. Automatic
answers require the exact live customer turn; delayed recovery sends are suppressed. Explicit plugin
reset replies remain available to configured test numbers. On hosts that expose
`notify_missing_home_channel`, the plugin also disables that notice at registration.
For public profiles, set `onboarding.profile_build: "off"` to disable personal-profile onboarding.

## Inbound media

Only attachments in the current webhook messages are downloaded. Downloads require HTTPS, do not follow
redirects, and are limited to 25 MiB per file. Images enter Hermes' native vision path; supported
documents are available through `pipefacil_read_profile_file`. Expired links, unsupported types, empty
responses, and invalid files are surfaced in the model context; the agent must not claim to have read or
analyzed an unavailable attachment. Historical attachments are not downloaded. Scanned PDFs without a
text layer may not extract.

## Security and privacy

- Keep `PIPEFACIL_API_KEY` in the profile's secret file, outside Git.
- The public callback requires the correct AI-agent HMAC signature. Keep the signing secret private.
- Use HTTPS between Pipefacil and the public ingress.
- Lead messages and recent conversation history are sent to the configured Hermes model as turn
  context. Treat the model provider and profile access policy as part of your data-handling setup.
- The plugin does not download historical attachments. Current audio uses the configured native STT provider.
- See [SECURITY.md](SECURITY.md) for responsible vulnerability reporting.

## License

No open-source license has been selected yet. See [LICENSE-NOTICE.md](LICENSE-NOTICE.md); public
visibility does not grant reuse rights.

## Compatibilidade HTTP/Kafka (0.4.3)

As orientações comuns têm revisão e fonte compartilhadas com o Kafka 0.1.1. Consulte
[o contrato de compatibilidade e atualização coordenada](docs/plugin-parity.md).
O health informa versão, revisão e capacidades. Os nomes e limites específicos de mídia permanecem próprios deste canal.

## Profile-owned business extensions (0.4.6)

Keep client code in a separate native plugin and declare it in `extra.extension_plugins`.
The shared core checks required dependencies and scoped tool/lifecycle contracts. See
[extension API revision 1](docs/extensions.md) for configuration, ordered handoff and stored audio.
