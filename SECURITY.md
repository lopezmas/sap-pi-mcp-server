# Security Policy

## Reporting Security Issues

Please report security issues privately to the project maintainer. Do not open a public issue for suspected credential exposure, authentication bypass, or data leakage.

## Credential Handling

- Do not commit real SAP PI/PO hosts, usernames, passwords, tokens, exported payloads, logs, or generated CSV/Markdown reports.
- Configure `SAP_PI_HOST`, `SAP_PI_SCHEME`, `SAP_PI_USER`, and `SAP_PI_PASSWORD` through environment variables or a private MCP client configuration.
- Use a least-privilege SAP PI/PO technical account. Set `SAP_PI_SCHEME=https` where available.

## Tool Scope

This MCP server is intended for read-only discovery, monitoring, export, and validation workflows. It should not expose SAP PI/PO create, change, delete, open-for-edit, or revert operations.
