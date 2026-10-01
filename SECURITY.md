# Security policy

## Reporting a vulnerability

Please do not report security issues in public discussions. Use GitHub's private vulnerability
reporting for this repository when it is enabled. If private reporting is unavailable, contact the
maintainer privately before opening a public issue.

Do not include API keys, webhook signing secrets, lead names, phone numbers, or raw webhook payloads
in reports.

## Credential handling

The plugin reads `PIPEFACIL_API_KEY` and `PIPEFACIL_WEBHOOK_SECRET` from the active Hermes profile.
Keep both values out of source control and rotate them if they are exposed. The public webhook must
be reachable over HTTPS; the local listener should remain bound to loopback unless your deployment
requires another network topology.
