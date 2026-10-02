# Security policy

## Reporting a vulnerability

Please do not report security issues in public discussions. Use GitHub's private vulnerability
reporting for this repository when it is enabled. If private reporting is unavailable, contact the
maintainer privately before opening a public issue.

Do not include API keys, webhook signing secrets, lead names, phone numbers, or raw webhook payloads
in reports.

## Credential handling

The plugin reads `PIPEFACIL_API_KEY` from the active Hermes profile. Keep it out of source control
and rotate it if it is exposed. Version 0.3.0 does not verify inbound webhook signatures or
timestamps. Anyone who can reach the callback can submit events that trigger agent responses,
deal updates, or `/reset` when impersonating an allowed test number. Restrict callback access at
the ingress before using this version on a public host.
Use HTTPS for the public webhook; keep the local listener bound to loopback unless your deployment
requires another network topology.
