# Hermes Pipefacil Plugin

A standalone Hermes gateway plugin that connects Pipefacil `message.received` webhooks to a Hermes
profile. It loads recent conversation messages from Pipefacil, lets the profile respond to the lead,
sends the final answer through the Pipefacil API, and provides a narrowly scoped CRM deal update tool.

[Leia em português](README.pt-BR.md).

## Features

- Verifies Pipefacil webhook signatures with HMAC-SHA256 and a five-minute replay window.
- Loads recent conversation messages from every participant, not only messages sent by Hermes.
- Replies to the lead through the Pipefacil API.
- Falls back to the local Hermes conversation context if Pipefacil history cannot be loaded.
- Registers `pipefacil_update_deal` for updating an explicitly identified deal.
- Reads API credentials from the owning Hermes profile; it does not accept a `workspaceId`.
- Handles text messages. Attachments are marked in context but are not downloaded or transcribed.

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
git clone https://github.com/cardosolucass96/hermes-pipefacil-plugin.git "$PLUGIN_DIR"
hermes -p "$PROFILE" plugins enable pipefacil-platform
```

The profile directory can differ if your Hermes installation uses a custom home. Store credentials
in that profile's `.env`; never commit them:

```dotenv
PIPEFACIL_API_KEY=pf_live_...
PIPEFACIL_WEBHOOK_SECRET=...
```

Restart the gateway after changing plugin files, credentials, or profile configuration.

## Configure the profile

Enable the platform and expose only its CRM tool to this profile:

```yaml
platforms:
  pipefacil:
    enabled: true
    extra:
      host: 127.0.0.1
      port: 8645
      path: /events/message-received
      history_limit: 100 # from 1 to 200
      allowed_users:
        - "*"
      # Optional for a homologation or local Pipefacil server:
      # api_base_url: https://homolog.pipefacil.matchsales.com.br

platform_toolsets:
  pipefacil: [pipefacil]
```

The wildcard allowlist permits lead identities from signed Pipefacil webhooks to reach the agent. The
plugin verifies the webhook signature before dispatching an event. Keep the webhook signing secret
private and do not expose the listener directly over plain HTTP.

The `pipefacil` toolset contains only `pipefacil_update_deal`. The agent can update a deal only when
the webhook provides its `seq`; supported fields are validated before the Pipefacil API request.

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
from the public host. The local health check is the callback path plus `/health`, for example
`http://127.0.0.1:8645/events/message-received/health`.

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

## Compatibility note

When Hermes exposes the `notify_missing_home_channel` platform capability, this plugin disables the
personal `/sethome` onboarding notice for Pipefacil leads. Older Hermes hosts do not receive that
option; the plugin remains loadable, but the host may show its usual home-channel notice on a new
conversation.

## Security and privacy

- Keep `PIPEFACIL_API_KEY` and `PIPEFACIL_WEBHOOK_SECRET` in the profile's secret file, outside Git.
- Use HTTPS between Pipefacil and the public ingress.
- Lead messages and recent conversation history are sent to the configured Hermes model as turn
  context. Treat the model provider and profile access policy as part of your data-handling setup.
- The plugin does not download media or transcribe attachments in this release.
- See [SECURITY.md](SECURITY.md) for responsible vulnerability reporting.

## License

No open-source license has been selected yet. See [LICENSE-NOTICE.md](LICENSE-NOTICE.md); public
visibility does not grant reuse rights.
