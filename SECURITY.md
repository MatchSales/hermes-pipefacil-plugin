# Security policy

## Reporting a vulnerability

Please do not report security issues in public discussions. Use GitHub's private vulnerability
reporting for this repository when it is enabled. If private reporting is unavailable, contact the
maintainer privately before opening a public issue.

Do not include API keys, webhook signing secrets, lead names, phone numbers, or raw webhook payloads
in reports.

## Credential handling

The plugin reads `PIPEFACIL_API_KEY` from the active Hermes profile. Keep it out of source control
and rotate it if it is exposed. Version 0.4 requires the owning profile's original
`PIPEFACIL_WEBHOOK_SECRET`, verifies the AI-agent HMAC over timestamp and exact decompressed JSON,
and rejects replayed/stale message identities. Secret rotation accepts the backend's current/next headers.
Public conversations have a mandatory restricted toolset and runtime hook; ordinary lead input cannot
administer the profile, execute shell/browser/delegation tools, choose another recipient or select another deal.
Knowledge/current-attachment reads are scoped to the live turn; local outgoing media requires a catalog ID.
Media downloads disable proxies/redirects and pin public DNS addresses while preserving TLS verification.
Jobs and HTTP effects are journaled in profile-local private storage. Ambiguous writes and interrupted
model turns are never automatically retried; reconcile with independent delivery/CRM evidence.
CRM updates verify contact/assignment before PATCH and values after PATCH. The CRM must still enforce
authorization atomically on its own endpoints; a plugin-side read cannot provide server-side atomicity.
Use HTTPS for the public webhook; keep the local listener bound to loopback unless your deployment
requires another network topology.
