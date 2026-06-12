# SAP PI MCP Server

Local MCP server for inspecting SAP PI / PO monitoring and Integration Directory metadata.

## Configuration

The server reads SAP PI connection details from environment variables:

```env
SAP_PI_HOST=your-pi-host:50000
SAP_PI_SCHEME=https
SAP_PI_USER=your-user
SAP_PI_PASSWORD=your-password
```

The MCP server itself does not choose an environment. The MCP client injects the environment variables when it starts the server. `SAP_PI_SCHEME` defaults to `http` for older PI/PO systems, but `https` is preferred where available. You can also set `SAP_PI_HOST` to a full `https://...` URL. Do not commit real connection values or credentials.

## Codex MCP Config

Add one server per environment to `C:\Users\<you>\.codex\config.toml`:

```toml
[mcp_servers.sap-pi-dev]
command = "python"
args = ["C:\\path\\to\\sap-pi-mcp-server\\get_pi_channel_status.py"]

[mcp_servers.sap-pi-dev.env]
SAP_PI_HOST = "dev-pi-host:50000"
SAP_PI_SCHEME = "https"
SAP_PI_USER = "dev-user"
SAP_PI_PASSWORD = "dev-password"

[mcp_servers.sap-pi-test]
command = "python"
args = ["C:\\path\\to\\sap-pi-mcp-server\\get_pi_channel_status.py"]

[mcp_servers.sap-pi-test.env]
SAP_PI_HOST = "test-pi-host:50000"
SAP_PI_SCHEME = "https"
SAP_PI_USER = "test-user"
SAP_PI_PASSWORD = "test-password"

[mcp_servers.sap-pi-prod]
command = "python"
args = ["C:\\path\\to\\sap-pi-mcp-server\\get_pi_channel_status.py"]

[mcp_servers.sap-pi-prod.env]
SAP_PI_HOST = "prod-pi-host:50000"
SAP_PI_SCHEME = "https"
SAP_PI_USER = "prod-user"
SAP_PI_PASSWORD = "prod-password"
```

Restart Codex after changing the MCP config.

## Security Notes

- Configure credentials through environment variables or your MCP client's private config only.
- Keep `.env`, generated CSV/Markdown exports, logs, and local cache files out of git.
- The exposed MCP tools are intended for read-only discovery, monitoring, export, and validation. They do not expose create, change, delete, open-for-edit, or revert operations.
- Use least-privilege SAP PI/PO accounts. Prefer HTTPS SAP endpoints where your PI/PO system supports them.

## Run Locally

```powershell
python get_pi_channel_status.py
```

The server runs over MCP `stdio`.

## Agent Skill

Consumer guidance for AI agents is available in `skills/sap-pi-mcp/SKILL.md`. Use it to teach clients how to retrieve ICO Operation Mappings, transformations, and routing conditions safely.

## Example Prompts

Use prompts like these with an agent that has this MCP server configured:

```text
Generate an SAP PI/PO interface inventory report with sender, receiver, interface ID, namespace, channels, routing, and Operation Mapping details.
```

```text
Export the SAP PI/PO Value Mappings for this group as CSV so I can review them for SAP CPI migration.
```

```text
Review this Message Mapping and explain its source fields, target fields, functions, dependencies, and value-mapping usage.
```

```text
Describe how this interface works end-to-end, from ICO routing through Operation Mapping, Message Mapping, and communication channels.
```

```text
Find failed messages for this sender component and interface in the last 4 hours.
```

```text
Validate this communication channel and summarize any SAP PI/PO check messages.
```

## Tools

The MCP server intentionally exposes read-only discovery and validation tools for PI-to-CPI migration analysis.

- `get_pi_channel_status`
- `search_failed_messages`
- `search_processed_messages`
- `search_messages_by_sender_component`
- `search_messages_by_interface`
- `search_messages_by_interface_and_sender`
- `get_service_interface_details`
- `get_integrated_configuration_details`
- `list_integrated_configurations`
- `list_integrated_configurations_750`
- `get_integrated_configuration_750_details`
- `check_integrated_configuration_750`
- `get_operation_mapping_details`
- `get_ico_operation_mapping_transformations`
- `list_message_mappings`
- `get_message_mapping_definition`
- `get_message_mapping_source`
- `get_message_mapping_dependencies`
- `analyze_message_mapping`
- `get_ico_message_mapping_definitions`
- `list_message_mappings_using_value_mapping`
- `list_communication_channels`
- `get_communication_channel_details`
- `check_communication_channel`
- `list_value_mappings`
- `get_value_mapping_details`
- `export_value_mappings_csv`
- `check_value_mapping`
- `list_configuration_scenarios`
- `get_configuration_scenario_details`
- `check_configuration_scenario`

The server does not expose mutating Directory API operations such as create, change, delete, open-for-edit, or revert.

## Message Mapping Definitions

The PI/PO SOAP BASIS APIs do not expose a `MessageMappingInService` read service. The MCP server reads Message Mapping definitions through ESR SimpleQuery plus the ESR read endpoint linked by SimpleQuery:

```text
/rep/support/SimpleQuery
/rep/read/ext?method=PLAIN&TYPE=XI_TRAFO&...
```

Use these read-only tools:

- `list_message_mappings` discovers `XI_TRAFO` objects and their ESR read paths.
- `get_message_mapping_definition` decodes an `XI_TRAFO` object's embedded metadata/source blobs.
- `get_message_mapping_source` returns decoded metadata, generated Java, text, or all source entries.
- `get_message_mapping_dependencies` returns ESR link dependencies such as source/target messages, function libraries, imported archives, templates, IDocs, RFCs, and external definitions.
- `analyze_message_mapping` combines definition, dependencies, field path extraction, value-mapping usage, function libraries, and imported archives.
- `get_ico_message_mapping_definitions` starts from an ICO and analyzes all assigned Message Mappings.
- `list_message_mappings_using_value_mapping` scans decoded mapping definitions for value-mapping API/function calls.

## Export Value Mappings to CSV

The `export_value_mappings_csv` MCP tool exports SAP PI/PO Value Mappings in a normalized CSV shape with one row per representation.

You can also run the standalone CLI:

```powershell
python export_value_mappings_csv.py value_mappings.csv
python export_value_mappings_csv.py value_mappings.csv --group-name "Example Codes"
python export_value_mappings_csv.py value_mappings.csv --scheme-id SAP --scheme-agency-id ERP
```

## License

This project is licensed under the MIT License. See `LICENSE` for details.
