# Security Policy

## Reporting Security Issues

Please report security issues privately to the project maintainer. Do not open a public issue for suspected credential exposure, authentication bypass, or data leakage.

## Credential Handling

- Do not commit real SAP PI/PO hosts, usernames, passwords, tokens, exported payloads, logs, or generated CSV/Markdown reports.
- Configure `SAP_PI_HOST`, `SAP_PI_SCHEME`, `SAP_PI_USER`, and `SAP_PI_PASSWORD` through environment variables or a private MCP client configuration.
- Use a least-privilege SAP PI/PO technical account. Set `SAP_PI_SCHEME=https` where available.

## Tool Scope

Discovery, monitoring, export, and validation remain read-only. Optional message
recovery exposes only `resendMessages` and `cancelMessages`, with preview enabled
by default and writes disabled unless `SAP_PI_ENABLE_RECOVERY=true` and an explicit
`SAP_PI_ENVIRONMENT=dev|test|prod` classification is supplied. Match that setting
to the connected tenant; it is not detected independently.

Recovery requires explicit user approval. PROD requires two separate user
confirmations under AGENTS.md. Cancellation skips delivery and needs specific
authorization. Confirmation arguments are workflow guards, not authentication
or proof of consent. SAP account permissions remain authoritative. Keep recovery
disabled for read-only accounts and validate changes in non-PROD first.

No artifact modification, force-fail, queue-lock manipulation, or deletion is
exposed. Preserve recovery records privately; never commit customer execution
logs. See [EOIO_RECOVERY.md](EOIO_RECOVERY.md) for the recovery procedure.
