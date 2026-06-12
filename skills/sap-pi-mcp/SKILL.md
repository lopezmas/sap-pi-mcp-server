---
name: sap-pi-mcp
description: Use when Codex needs to inspect SAP PI/PO through the SAP PI MCP server, including ICO lookup, receiver routing conditions, Operation Mapping assignments, message mapping transformations, communication channels, value mappings, Value Mapping CSV export, or message monitoring. Applies to DEV, TEST, and PROD SAP PI MCP servers; PROD is read-only unless the user explicitly confirms changes twice.
---

# SAP PI MCP

## Safety

- Treat all SAP PI MCP operations as environment-specific.
- Never perform PROD changes unless the user explicitly asks, then asks again, and both confirmations are clear.
- Prefer read-only tools. The current MCP server is intended for discovery and validation.
- When answering user questions, say which PI environment was used if it is known.

## Choose The MCP Server

- Use the MCP server matching the requested environment: `sap-pi-dev`, `sap-pi-test`, or `sap-pi-prod`.
- If the user does not specify an environment, ask or use the most recent environment in the conversation.
- If no SAP PI MCP tool namespace is available, use the configured local server command only for read-only checks and ensure `SAP_PI_HOST`, `SAP_PI_USER`, and `SAP_PI_PASSWORD` are set for the intended environment.

## Operation Mapping Details

Use this tool when the user already knows the Operation Mapping name:

```python
get_operation_mapping_details(
    operation_mapping_name: str,
    namespace: str = "",
    software_component_version: str = ""
)
```

Minimum useful call:

```python
get_operation_mapping_details(
    operation_mapping_name="OM_Example",
    namespace="urn:example"
)
```

Report:

- Operation Mapping name and namespace.
- Transformation/message mapping names from `transformations`.
- `role`, usually `request` or `response`.
- `type_code`, usually `XI_TRAFO` for message mappings.
- Mention when no match is found and suggest checking namespace or environment.

## ICO To Operation Mapping Transformations

Use this tool when the user starts from an ICO, sender service interface, sender component, or asks "which transformations are assigned to this ICO?":

```python
get_ico_operation_mapping_transformations(
    sender_component: str,
    interface_name: str,
    interface_namespace: str,
    sender_party: str = "",
    receiver_party: str = "",
    receiver_component: str = ""
)
```

Workflow:

1. If the user gives only a service interface name, call `list_integrated_configurations_750()` and filter `InterfaceName`.
2. For the selected ICO, call `get_ico_operation_mapping_transformations(...)`.
3. Report each receiver component, Operation Mapping, and transformation.

Generic example:

- Sender component: `EXAMPLE_SENDER`
- Interface namespace: `urn:example:pi:outbound`
- Receiver component: `EXAMPLE_RECEIVER`
- ICO-assigned Operation Mapping: `OM_Example_Outbound_To_Target`

## Routing Conditions

Use ICO details when the user asks whether routing has conditions.

Preferred path:

1. Call `get_integrated_configuration_750_details(...)` for the ICO.
2. Inspect `ReceiverRule`, `ReceiverRules`, and `Condition` nodes.
3. Treat `<Condition />` as unconditional routing.
4. Report receiver routing separately from receiver interface rules.

Generic unconditional receiver routing example:

```xml
<ReceiverRule>
  <Condition />
  ...
  <CommunicationComponent>
    <Value>EXAMPLE_RECEIVER</Value>
  </CommunicationComponent>
</ReceiverRule>
```

This means routing is unconditional to `EXAMPLE_RECEIVER`.

## Value Mapping CSV Export

Use the Value Mapping export when the user needs mapping values in a spreadsheet-ready form:

```python
export_value_mappings_csv(
    value_mapping_id: str = "",
    group_name: str = "",
    representation: str = "",
    scheme_id: str = "",
    scheme_agency_id: str = ""
)
```

Workflow:

1. Choose the requested PI environment before exporting.
2. Use filters when the user gives a Value Mapping ID, group name, representation value, `schemeID`, or `schemeAgencyID`.
3. For a small preview, show the CSV header and a bounded sample of rows. Do not dump a large export into chat unless requested.
4. If the user needs a local file outside MCP, run the read-only CLI from this repo with the same environment variables:

```powershell
python export_value_mappings_csv.py value_mappings.csv
python export_value_mappings_csv.py value_mappings.csv --group-name "Example Codes"
python export_value_mappings_csv.py value_mappings.csv --scheme-id SAP --scheme-agency-id ERP
```

The export queries Value Mapping IDs and reads active Value Mapping details. Each CSV row is one `Representation`; mapping metadata repeats across rows. Columns are:

```text
value_mapping_id,group_name,master_language,description,responsible_user,last_change_user,last_change_datetime,folder_path,representation_value,scheme_id,scheme_agency_id
```

Use `list_value_mappings(...)` when the user only needs IDs or wants to narrow the export first. Use `get_value_mapping_details(value_mapping_id=...)` when raw SOAP XML is more useful than CSV.

## Message Mapping Definitions

SAP PI/PO does not expose Message Mapping definitions through a standard BASIS SOAP service like `MessageMappingInService`. The MCP server reads them with ESR SimpleQuery and the read-only ESR object endpoint behind SimpleQuery:

Discover Message Mappings first when the user does not provide an exact namespace:

```python
list_message_mappings(
    name: str = "",
    namespace: str = "",
    software_component_version: str = ""
)
```

Read the decoded definition summary:

```python
get_message_mapping_definition(
    message_mapping_name: str,
    namespace: str = "",
    software_component_version: str = "",
    include_source: bool = False
)
```

Use `include_source=True` only when the user needs the decoded mapping metadata/generated source. Otherwise report the identity, source/target links, decoded entry metadata, and `uses_value_mapping`.

Read only source entries:

```python
get_message_mapping_source(
    message_mapping_name: str,
    namespace: str = "",
    software_component_version: str = "",
    source_type: str = "metadata"
)
```

Valid `source_type` values are `metadata`, `generated_java`, `text`, and `all`. Prefer `metadata` unless the user asks for generated Java.

Read ESR dependencies:

```python
get_message_mapping_dependencies(
    message_mapping_name: str,
    namespace: str = "",
    software_component_version: str = ""
)
```

Use this for source/target messages, function libraries, imported archives, templates, IDocs, RFCs, and external definitions.

Use the consolidated analyzer when the user asks for all details:

```python
analyze_message_mapping(
    message_mapping_name: str,
    namespace: str = "",
    software_component_version: str = "",
    include_source: bool = False
)
```

Start from an ICO when the user gives a sender component/interface instead of a Message Mapping name:

```python
get_ico_message_mapping_definitions(
    sender_component: str,
    interface_name: str,
    interface_namespace: str,
    sender_party: str = "",
    receiver_party: str = "",
    receiver_component: str = "",
    include_source: bool = False
)
```

To find message mappings that call PI value-mapping APIs/functions:

```python
list_message_mappings_using_value_mapping(
    namespace: str = "",
    software_component_version: str = "",
    limit: int = 0
)
```

This scans decoded `XI_TRAFO` definitions for value-mapping function/API call patterns. It is more reliable than searching SimpleQuery dependency rows because dependency rows do not expose graphical mapping internals.

## Answer Format

- Keep answers concise and name the environment.
- For service interface questions, distinguish:
  - ICO-assigned Operation Mappings.
  - ESR Operation Mappings that reference the service interface.
  - Receiver routing conditions.
- Include namespaces when object names are not globally unique.
