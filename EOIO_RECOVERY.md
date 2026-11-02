# EOIO inspection and recovery

Pablo Lopez | 2026-11-02 | EOIO monitoring, verification, restart and cancellation.

## Tools

| Tool | Behavior |
| --- | --- |
| `inspect_eoio_messages` | Search by time, sender, receiver, status, sequence ID, serialization context and direction. Includes recovery eligibility. |
| `get_eoio_predecessor` | Return SAP's predecessor ID, which may be a logged parent rather than the blocking queue entry. |
| `get_pi_message_status` | Read one exact message key's status and eligibility without payload. |
| `restart_pi_message` | Preview or resend one message using SAP `resendMessages`. |
| `cancel_pi_message` | Preview or cancel one message using SAP `cancelMessages`, skipping delivery. |

Restart the MCP process to discover new tools. Inspection defaults to 24 hours
and 100 messages. Use `hours_back=0` to omit date restrictions when inspecting
older blockers. Search excludes archives. The maximum limit is 1,000;
`possibly_truncated=true` means the search may be incomplete. Narrow filters
before acting on an incomplete result.

Keep sequence ID, direction, sender, receiver and serialization context together.
Inspect the full context, including older messages outside the initial window.
`logVersion` records are logging artifacts, not retryable blockers. Sequence
numbers are comparable within a queue counter; do not assume they establish
order across different counters. Use persisted time and sequence metadata
together, and stop if ordering is ambiguous.

## Activation and approvals

Writes are disabled by default. Enable only in a deliberately selected server:

```env
SAP_PI_ENABLE_RECOVERY=true
SAP_PI_ENVIRONMENT=test
```

Environment values are `dev`, `test`, or `prod`. This operator-supplied setting
must match the connected tenant. Connection credentials are unchanged. Validate
changes in non-PROD first. No client configuration is changed automatically.

Both mutation tools default to `dry_run=true`; previews make no network calls.
For approved execution, use the exact SAP message key and `dry_run=false`:

| Action | `confirmation` | Additional PROD `second_confirmation` |
| --- | --- | --- |
| Restart | `RESTART <messageKey>` | `CONFIRM PROD RESTART <messageKey>` |
| Cancel | `CANCEL <messageKey>` | `CONFIRM PROD CANCEL <messageKey>` |

Obtain explicit approval for the targets and recovery policy. PROD requires
asking the user twice under AGENTS.md. Explicit batch authorization can cover
the batch; arguments must reflect the approved action and exact key.
Cancellation requires approval specifically allowing skipped delivery.
Arguments are workflow guards, not authentication or proof of consent.
SAP account permissions remain authoritative.

Before writing, tools read the exact message and require SAP `restartable` or
`cancelable` eligibility. Terminal, logged, delivering and queued-for-delivery
messages are refused. SAP can still reject a request if state changes between
lookup and submission. `submitted=true` means a response was received;
`accepted=true` additionally requires a matching key and successful per-message
result. Neither establishes business delivery. Inspect the subsequent status.

## Recovery procedure

1. Search held messages within the approved scope, including all-age inspection
   where necessary. Retrieve the full context to identify the oldest outstanding
   blocker; do not rely solely on predecessor lookup.
2. Preview and obtain required approvals, including the cancellation policy.
   Retry the blocker once and allow a processing interval before checking status.
   The verified recovery used 15–20 second intervals; these are observations,
   not delivery guarantees.
3. Let `delivering` and `toBeDelivered` messages progress. Do not resend or cancel
   them. Held successors may clear automatically when their blocker succeeds.
4. If the retried blocker fails or remains `waiting`, cancel only under the
   approved policy. Verify `canceled` before restarting its successor. If the
   blocker remains `holding`, investigate rather than assuming failure.
5. Restart the next eligible message and verify the context to terminal states.
   Check every original target and requery held messages within the approved
   scope. Report successful deliveries and cancellations separately.

Independent sequences can be processed in rounds with one action per sequence
before polling. Preserve ordering within each sequence. Stop the affected
sequence on unknown state, ambiguous ordering, rejection or timeout. Inspect
current state before another attempt. Writes are never automatically retried.
Keep exact keys, responses and verification records privately, outside Git.

No generic unlock operation exists in the inspected contract. SAP exposes
`failEoioMessage`, but this extension does not. No force-fail, deletion or
queue-lock manipulation is implemented.

## Validation and limits

The sanitized `wsdl/AdapterMessageMonitoring.interface.xml` records the contract
used to verify qualified request parameters and field order. SOAP faults are
detected even with HTTP 500 responses. Existing legacy search tools are unchanged.

An authorized live PROD recovery on 2 November 2026 verified the underlying SOAP
inspection, status, restart and cancellation operations, including successful
successor processing after cancellation. Session-specific scripts and customer
records stay local. The reusable MCP wrappers and guards have offline tests;
installation and tests need no live writes. The server does not autonomously
run bulk recovery or persist audit records.

Offline validation:

```powershell
python -m unittest test_pi_recovery test_message_mapping_definition test_operation_mapping_helpers test_value_mapping_export
```

SAP references:
- https://community.sap.com/t5/technology-blog-posts-by-sap/reading-messages-from-pi-system/ba-p/13144443
- https://userapps.support.sap.com/sap/support/knowledge/en/2135741
