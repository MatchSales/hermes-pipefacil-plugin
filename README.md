# Hermes Pipefacil Plugin

A standalone Hermes gateway plugin that connects Pipefacil `message.received` webhooks to a Hermes
profile. It loads recent conversation messages from Pipefacil, lets the profile respond to the lead,
sends the final answer through the Pipefacil API, and provides a narrowly scoped CRM deal update tool.

[Leia em português](README.pt-BR.md).

## Features

- Verifies Pipefacil webhook signatures with HMAC-SHA256 and a five-minute replay window.
- Loads recent conversation messages from every participant, not only messages sent by Hermes.
- Replies to the lead through the Pipefacil API.
- Registers `pipefacil_send_messages` to send up to two additional text, image, or document messages before Hermes sends its final answer automatically.
- Downloads attachments only from current signed webhook messages and passes them to Hermes' native image/document handling.
- Falls back to the local Hermes conversation context if Pipefacil history cannot be loaded.
- Treats a standalone `/reset` message as a Hermes command, removes the finished local session transcript,
  and stops sending pre-reset Pipefacil history back to the model for that conversation.
- Registers `pipefacil_update_deal` for updating an explicitly identified deal.
- Reads API credentials from the owning Hermes profile; it does not accept a `workspaceId`.
- Historical attachments are shown as non-text content and are not downloaded.
- Outbound media links must be HTTPS and explicitly listed in the active profile's `SOUL.md`.

## Requirements

- A Hermes installation with gateway plugins and the plugin platform registry.
- A Pipefacil API key with `API_ACCESS`, `ADVANCED_API`, conversation read/send access, and deal edit access.
- The webhook signing secret configured for the Pipefacil callback.
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
PIPEFACIL_WEBHOOK_SECRET=...
```

Restart the gateway after changing plugin files, credentials, or profile configuration.

### Docker and dashboard installation

In the official image, persistent data lives under `/opt/data`. Confirm the exact profile name with
`hermes profile list` inside the container. The **Plugins** page installs into the profile hosting
the dashboard process; selecting another profile in the page header does not change the install
target in this Hermes version. Install explicitly into a secondary profile:

```bash
docker exec -u hermes -it <container> hermes -p <profile> plugins install \
  https://github.com/MatchSales/hermes-pipefacil-plugin.git --enable
docker exec -u hermes <container> hermes -p <profile> plugins list --user --plain
```

Use `-u hermes`: `docker exec` defaults to root in the official image and can leave files that the
gateway cannot modify. This uses Git and does not require `gh`. A warning that `PIPEFACIL_API_KEY` and
`PIPEFACIL_WEBHOOK_SECRET` are missing does not mean installation failed. Set both values for the
served profile in **Channels > Pipefacil > Configure** or in that profile's `.env`. The Pipefacil
card appears when the plugin loads in that profile; the credentials are required to connect it.

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

The wildcard allowlist permits lead identities from signed Pipefacil webhooks to reach the agent. The
plugin verifies the webhook signature before dispatching an event. Keep the webhook signing secret
private and do not expose the listener directly over plain HTTP.

The `pipefacil` toolset contains response tools, an update tool bound to the authenticated event's deal,
and `pipefacil_read_profile_file`. The send tool uses the
destination from the authenticated event; the model cannot choose a phone number. It accepts one or two
messages, and Hermes sends its final answer automatically afterward. A successful result confirms that
the API accepted the request, not that WhatsApp delivery was confirmed. Reads are limited to current
turn attachments and files under this profile's `knowledge/` folder. Do not enable Hermes' `file`
toolset for a public-facing agent; it also exposes writing and patching tools.

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

Pipefacil sends the signature in `X-Pipefacil-Signature-256` and the timestamp in
`X-Pipefacil-Timestamp`. Hermes validates HMAC-SHA256 over `<timestamp>.<JSON body>`. Timestamps
outside the five-minute window are rejected. The endpoint acknowledges webhook admission; the model
turn and outbound API reply run in the background.

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
the customer conversation.

## Compatibility note

When Hermes exposes the `notify_missing_home_channel` platform capability, this plugin disables the
personal `/sethome` onboarding notice for Pipefacil leads. Older Hermes hosts do not receive that
option; the plugin remains loadable, but the host may show its usual home-channel notice on a new
conversation.

## Inbound media

Only attachments in the current webhook messages are downloaded. Downloads require HTTPS, do not follow
redirects, and are limited to 25 MiB per file. Images enter Hermes' native vision path; supported
documents are available through `pipefacil_read_profile_file`. Expired links, unsupported types, empty
responses, and invalid files are surfaced in the model context; the agent must not claim to have read or
analyzed an unavailable attachment. Historical attachments are not downloaded. Scanned PDFs without a
text layer may not extract.

## Security and privacy

- Keep `PIPEFACIL_API_KEY` and `PIPEFACIL_WEBHOOK_SECRET` in the profile's secret file, outside Git.
- Use HTTPS between Pipefacil and the public ingress.
- Lead messages and recent conversation history are sent to the configured Hermes model as turn
  context. Treat the model provider and profile access policy as part of your data-handling setup.
- The plugin does not download historical attachments or transcribe audio.
- See [SECURITY.md](SECURITY.md) for responsible vulnerability reporting.

## License

No open-source license has been selected yet. See [LICENSE-NOTICE.md](LICENSE-NOTICE.md); public
visibility does not grant reuse rights.
