import asyncio
import base64
import csv
import os
import requests
from mcp.server.fastmcp import FastMCP
import datetime
import html
import io
import json
import re
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr
from pi_recovery import register as register_recovery_tools

# Initialize FastMCP - this handles the heavy lifting
mcp = FastMCP("SAP-PI-Monitor")

def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError("Missing required environment variable: {}".format(name))
    return value


PI_HOST = required_env("SAP_PI_HOST")
PI_USER = required_env("SAP_PI_USER")
PI_PWD = required_env("SAP_PI_PASSWORD")
PI_SCHEME = os.environ.get("SAP_PI_SCHEME", "http").strip().lower()

SOAP_HEADERS = {'Content-Type': 'text/xml;charset=UTF-8', 'SOAPAction': 'http://sap.com/xi/WebService/soap1.1'}


def pi_base_url():
    host = PI_HOST.rstrip("/")
    if host.startswith(("http://", "https://")):
        return host
    if PI_SCHEME not in ("http", "https"):
        raise RuntimeError("SAP_PI_SCHEME must be either http or https")
    return "{}://{}".format(PI_SCHEME, host)


def pi_url(service_path):
    return "{}/{}".format(pi_base_url(), service_path.lstrip("/"))


def soap_envelope(body):
    return """
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:bas="http://sap.com/xi/BASIS">
       <soapenv:Header/>
       <soapenv:Body>
          {}
       </soapenv:Body>
    </soapenv:Envelope>
    """.format(body)


def post_soap(service_path, body, timeout=30):
    response = requests.post(
        pi_url(service_path),
        data=soap_envelope(body),
        headers=SOAP_HEADERS,
        auth=(PI_USER, PI_PWD),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def simple_query_url():
    return pi_url("rep/support/SimpleQuery")


def operation_mapping_simple_query_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<generalQueryRequest>
  <types xmlns="">
    <type id="MAPPING"/>
  </types>
  <qc xmlns="" qcType="S" delMode="N" useSyncT="true" clCtxOnly="false">
    <clCxt consider="L"/>
    <swcListDef def="W"/>
  </qc>
  <condition xmlns="">
    <complex/>
  </condition>
    <result xmlns="">
    <attrib>NAME</attrib>
    <attrib>NAMESPACE</attrib>
    <attrib>KIND</attrib>
    <attrib>RA_LINK_TARGET_REFERENCE</attrib>
    <attrib>RA_LINK_TARGET_TYPE</attrib>
    <attrib>RA_LINK_ROLE</attrib>
  </result>
</generalQueryRequest>"""


def message_mapping_simple_query_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<generalQueryRequest>
  <types xmlns="">
    <type id="XI_TRAFO"/>
  </types>
  <qc xmlns="" qcType="S" delMode="N" useSyncT="true" clCtxOnly="false">
    <clCxt consider="L"/>
    <swcListDef def="W"/>
  </qc>
  <condition xmlns="">
    <complex/>
  </condition>
  <result xmlns="">
    <attrib>NAME</attrib>
    <attrib>NAMESPACE</attrib>
    <attrib>KIND</attrib>
    <attrib>RA_XILINK</attrib>
    <attrib>RA_LINK_TARGET_REFERENCE</attrib>
    <attrib>RA_LINK_TARGET_TYPE</attrib>
    <attrib>RA_LINK_ROLE</attrib>
  </result>
</generalQueryRequest>"""


def post_simple_query(query_xml, timeout=60):
    response = requests.post(
        simple_query_url(),
        data={
            "action": "Start query",
            "useXMLL": "true",
            "responseXMLL": "true",
            "queryRequestXMLL": query_xml,
        },
        auth=(PI_USER, PI_PWD),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def xml_value(value):
    return escape(value or "")


def optional_xml(name, value):
    if not value:
        return ""
    return "<{0}>{1}</{0}>".format(name, xml_value(value))


def xml_attr(value):
    return quoteattr(value or "")


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def find_by_local_name(root, name):
    return [elem for elem in root.iter() if local_name(elem.tag) == name]


def child_text(elem, name):
    for child in list(elem):
        if local_name(child.tag) == name:
            return child.text or ""
    return ""


def first_child_by_local_name(elem, name):
    for child in list(elem):
        if local_name(child.tag) == name:
            return child
    return None


def children_by_local_name(elem, name):
    return [child for child in list(elem) if local_name(child.tag) == name]


def first_text_by_local_names(elem, names):
    for name in names:
        value = child_text(elem, name)
        if value:
            return value
    return ""


def parse_response_ids(xml_text, element_name, fields):
    root = ET.fromstring(xml_text)
    rows = []
    for elem in find_by_local_name(root, element_name):
        rows.append({field: child_text(elem, field) for field in fields})
    return rows


def json_result(items_name, items):
    return json.dumps({"count": len(items), items_name: items}, indent=2)


def extract_simple_query_xml(response_text):
    text = (response_text or "").strip()
    if not text:
        return ""

    try:
        ET.fromstring(text)
        return text
    except ET.ParseError:
        pass

    textarea_matches = re.finditer(
        r"<textarea\b([^>]*)>(.*?)</textarea>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for textarea_match in textarea_matches:
        attrs = textarea_match.group(1)
        content = html.unescape(textarea_match.group(2)).strip()
        if "name=\"response\"" not in attrs and "name=response" not in attrs:
            continue
        try:
            ET.fromstring(content)
            return content
        except ET.ParseError:
            pass

    unescaped_text = html.unescape(text).strip()
    for root_name in ("queryResult", "generalQueryResult", "objectList"):
        match = re.search(r"(<{0}\b.*?</{0}>)".format(root_name), unescaped_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            candidate = match.group(1)
            try:
                ET.fromstring(candidate)
                return candidate
            except ET.ParseError:
                continue

    textarea_match = re.search(r"<textarea\b[^>]*>(.*?)</textarea>", unescaped_text, flags=re.IGNORECASE | re.DOTALL)
    if textarea_match:
        candidate = textarea_match.group(1).strip()
        try:
            ET.fromstring(candidate)
            return candidate
        except ET.ParseError:
            pass

    return unescaped_text


def append_row_value(row, key, value):
    if not key or value is None:
        return
    key = key.strip()
    value = str(value).strip()
    if not key or not value:
        return
    if key in row:
        if isinstance(row[key], list):
            row[key].append(value)
        else:
            row[key] = [row[key], value]
    else:
        row[key] = value


def element_direct_text(elem):
    parts = []
    if elem.text and elem.text.strip():
        parts.append(elem.text.strip())
    for child in list(elem):
        if child.tail and child.tail.strip():
            parts.append(child.tail.strip())
    return " ".join(parts).strip()


def collect_simple_query_row(elem):
    row = {}
    for attr_name in ("name", "id", "type", "kind", "value", "ref"):
        if attr_name in elem.attrib:
            append_row_value(row, attr_name.upper(), elem.attrib.get(attr_name))

    for child in list(elem):
        key = (
            child.attrib.get("name")
            or child.attrib.get("id")
            or child.attrib.get("column")
            or child.attrib.get("label")
            or local_name(child.tag)
        )
        value = (
            child.attrib.get("value")
            or child.attrib.get("ref")
            or child.attrib.get("reference")
            or element_direct_text(child)
        )

        if not value:
            value_child = first_child_by_local_name(child, "value")
            if value_child is not None:
                value = value_child.attrib.get("value") or element_direct_text(value_child)

        append_row_value(row, key.upper(), value)

        for grandchild in list(child):
            nested_key = (
                grandchild.attrib.get("name")
                or grandchild.attrib.get("id")
                or grandchild.attrib.get("column")
                or grandchild.attrib.get("label")
            )
            nested_value = (
                grandchild.attrib.get("value")
                or grandchild.attrib.get("ref")
                or grandchild.attrib.get("reference")
                or element_direct_text(grandchild)
            )
            append_row_value(row, (nested_key or key).upper(), nested_value)

    return row


def parse_simple_query_matrix(root):
    header_info = None
    matrix = None
    for elem in root.iter():
        if header_info is None and local_name(elem.tag) == "headerInfo":
            header_info = elem
        elif matrix is None and local_name(elem.tag) == "matrix":
            matrix = elem
        if header_info is not None and matrix is not None:
            break

    if header_info is None or matrix is None:
        return []

    columns = {}
    for col_def in find_by_local_name(header_info, "def"):
        col_type = col_def.attrib.get("type", "")
        pos = col_def.attrib.get("pos", "")
        if not col_type or not pos:
            continue
        try:
            columns[int(pos)] = col_type
        except ValueError:
            continue

    rows = []
    for row_elem in list(matrix):
        if local_name(row_elem.tag) != "r":
            continue
        row = {}
        for index, value_elem in enumerate(list(row_elem)):
            key = columns.get(index)
            if not key:
                continue
            value = value_elem.attrib.get("value") or simple_query_cell_value(value_elem)
            append_row_value(row, key, value)
        if row:
            rows.append(row)
    return rows


def simple_query_cell_value(elem):
    key_elem = None
    for child in elem.iter():
        if local_name(child.tag) == "key":
            key_elem = child
            break

    if key_elem is not None:
        parts = []
        for child in list(key_elem):
            if local_name(child.tag) == "elem":
                parts.append(child.text or "")
        if parts:
            return "|".join(parts)

    for child in elem.iter():
        if local_name(child.tag) in ("strg", "value"):
            value = child.text or ""
            if value:
                return value

    return element_direct_text(elem)


def parse_simple_query_rows(xml_text):
    root = ET.fromstring(extract_simple_query_xml(xml_text))
    matrix_rows = parse_simple_query_matrix(root)
    if matrix_rows:
        return matrix_rows

    row_tags = ("row", "object", "resultLine", "queryResultLine", "item")
    rows = []

    for elem in root.iter():
        if local_name(elem.tag) in row_tags:
            row = collect_simple_query_row(elem)
            if row:
                rows.append(row)

    if rows:
        return rows

    row = collect_simple_query_row(root)
    return [row] if row else []


def first_descendant_by_local_name(elem, name):
    for child in elem.iter():
        if local_name(child.tag) == name:
            return child
    return None


def qref_from_cell(elem):
    ref_elem = first_descendant_by_local_name(elem, "ref")
    if ref_elem is None:
        return {}

    key_elem = first_descendant_by_local_name(ref_elem, "key")
    if key_elem is None:
        return {}

    key_parts = [child.text or "" for child in list(key_elem) if local_name(child.tag) == "elem"]
    vc_elem = first_descendant_by_local_name(ref_elem, "vc")
    vc_type = vc_elem.attrib.get("vcType", "") if vc_elem is not None else ""
    swc_guid = vc_elem.attrib.get("swcGuid", "") if vc_elem is not None else ""
    sp = vc_elem.attrib.get("sp", "-1") if vc_elem is not None else "-1"
    caption = vc_elem.attrib.get("caption", "") if vc_elem is not None else ""

    return {
        "type": key_elem.attrib.get("typeID", ""),
        "oid": key_elem.attrib.get("oid", ""),
        "name": key_parts[0] if key_parts else "",
        "namespace": key_parts[1] if len(key_parts) > 1 else "",
        "key": "|".join(key_parts),
        "vc_type": vc_type,
        "swc_guid": swc_guid,
        "sp": sp,
        "software_component_version": caption,
    }


def parse_simple_query_qref_rows(xml_text, qref_columns=("RA_XILINK",)):
    root = ET.fromstring(extract_simple_query_xml(xml_text))
    header_info = first_descendant_by_local_name(root, "headerInfo")
    matrix = first_descendant_by_local_name(root, "matrix")
    if header_info is None or matrix is None:
        return []

    columns = {}
    for col_def in find_by_local_name(header_info, "def"):
        col_type = col_def.attrib.get("type", "")
        pos = col_def.attrib.get("pos", "")
        if not col_type or not pos:
            continue
        try:
            columns[int(pos)] = col_type
        except ValueError:
            continue

    rows = []
    for row_elem in list(matrix):
        if local_name(row_elem.tag) != "r":
            continue
        row = {}
        for index, value_elem in enumerate(list(row_elem)):
            key = columns.get(index)
            if not key:
                continue
            append_row_value(row, key, value_elem.attrib.get("value") or simple_query_cell_value(value_elem))
            if key in qref_columns:
                qref = qref_from_cell(value_elem)
                if qref:
                    row["{}_REF".format(key)] = qref
        if row:
            rows.append(row)
    return rows


def value_list(row, keys):
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            return value
        return [value]
    return []


def first_row_value(row, keys):
    values = value_list(row, keys)
    return values[0] if values else ""


def normalize_role(type_code, raw_role):
    source = "{} {}".format(type_code or "", raw_role or "").upper()
    if "REQUEST" in source:
        return "request"
    if "RESPONSE" in source:
        return "response"
    if "FAULT" in source:
        return "fault"
    return ""


def split_design_object_reference(reference):
    reference = (reference or "").strip()
    if not reference:
        return {"name": "", "namespace": "", "software_component_version": ""}

    parts = [part.strip() for part in re.split(r"\s*[|;]\s*", reference) if part.strip()]
    if len(parts) >= 3:
        return {
            "name": parts[0],
            "namespace": parts[1],
            "software_component_version": parts[2],
        }
    if len(parts) == 2:
        return {
            "name": parts[0],
            "namespace": parts[1],
            "software_component_version": "",
        }

    name_match = re.search(r"(?:^|[,\s])(?:name|Name|NAME)=([^,]+)", reference)
    namespace_match = re.search(r"(?:^|[,\s])(?:namespace|Namespace|NAMESPACE)=([^,]+)", reference)
    swcv_match = re.search(r"(?:^|[,\s])(?:swcv|SWCV|softwareComponentVersion|SOFTWARE_COMPONENT_VERSION)=([^,]+)", reference)
    if name_match or namespace_match or swcv_match:
        return {
            "name": name_match.group(1).strip() if name_match else reference,
            "namespace": namespace_match.group(1).strip() if namespace_match else "",
            "software_component_version": swcv_match.group(1).strip() if swcv_match else "",
        }

    return {"name": reference, "namespace": "", "software_component_version": ""}


def operation_mapping_matches(row, operation_mapping_name, namespace="", software_component_version=""):
    name = first_row_value(row, ("NAME", "OBJECT_NAME"))
    row_namespace = first_row_value(row, ("NAMESPACE", "OBJECT_NAMESPACE"))
    row_swcv = first_row_value(row, ("SOFTWARE_COMPONENT_VERSION", "SWCV", "SWCV_NAME"))

    if operation_mapping_name and name != operation_mapping_name:
        return False
    if namespace and row_namespace != namespace:
        return False
    if software_component_version and row_swcv != software_component_version:
        return False
    return True


def row_transformations(row):
    references = value_list(row, ("RA_LINK_TARGET_REFERENCE", "TARGET_REFERENCE", "TARGET"))
    type_codes = value_list(row, ("RA_LINK_TARGET_TYPE", "TARGET_TYPE", "TYPE_CODE"))
    raw_roles = value_list(row, ("RA_LINK_ROLE", "ROLE"))
    sequences = value_list(row, ("RA_LINK_TARGET_SEQUENCE", "SEQUENCE"))
    max_count = max(len(references), len(type_codes), len(raw_roles), len(sequences), 0)
    transformations = []

    for index in range(max_count):
        reference = references[index] if index < len(references) else ""
        type_code = type_codes[index] if index < len(type_codes) else ""
        raw_role = raw_roles[index] if index < len(raw_roles) else ""
        sequence = sequences[index] if index < len(sequences) else str(index + 1)

        if type_code and not type_code.upper().endswith("_TRAFO"):
            continue
        if not reference and not type_code:
            continue

        parsed_reference = split_design_object_reference(reference)
        transformations.append(
            {
                "name": parsed_reference["name"],
                "namespace": parsed_reference["namespace"],
                "software_component_version": parsed_reference["software_component_version"],
                "role": normalize_role(type_code, raw_role),
                "type_code": type_code,
                "raw_role": raw_role,
                "sequence": sequence,
            }
        )

    return transformations


def operation_mapping_details_from_rows(rows, operation_mapping_name, namespace="", software_component_version=""):
    matches = [row for row in rows if operation_mapping_matches(row, operation_mapping_name, namespace, software_component_version)]
    grouped = {}

    for row in matches:
        name = first_row_value(row, ("NAME", "OBJECT_NAME"))
        row_namespace = first_row_value(row, ("NAMESPACE", "OBJECT_NAMESPACE"))
        row_swcv = first_row_value(row, ("SOFTWARE_COMPONENT_VERSION", "SWCV", "SWCV_NAME"))
        group_key = (name, row_namespace, row_swcv)

        if group_key not in grouped:
            grouped[group_key] = {
                "name": name,
                "namespace": row_namespace,
                "software_component_version": row_swcv,
                "kind": first_row_value(row, ("KIND",)),
                "transformations": [],
                "raw": [],
            }

        grouped[group_key]["transformations"].extend(row_transformations(row))
        grouped[group_key]["raw"].append(row)

    operation_mappings = list(grouped.values())
    for operation_mapping in operation_mappings:
        operation_mapping["transformations"].sort(
            key=lambda item: int(item["sequence"]) if str(item.get("sequence", "")).isdigit() else 0
        )

    return {
        "count": len(operation_mappings),
        "operation_mappings": operation_mappings,
    }


def descendant_text(elem, name):
    for child in elem.iter():
        if local_name(child.tag) == name:
            return child.text or ""
    return ""


def direct_descendant_by_path(elem, path):
    current = elem
    for part in path:
        next_elem = first_child_by_local_name(current, part)
        if next_elem is None:
            return None
        current = next_elem
    return current


def design_object_from_elem(elem):
    if elem is None:
        return {"name": "", "namespace": "", "software_component_version": ""}
    return {
        "name": first_text_by_local_names(elem, ("Name", "NAME")),
        "namespace": first_text_by_local_names(elem, ("Namespace", "NAMESPACE")),
        "software_component_version": first_text_by_local_names(
            elem,
            ("SoftwareComponentVersionID", "SoftwareComponentVersion", "SWCV", "SWCVName"),
        ),
    }


def operation_mappings_from_ico_xml(xml_text):
    root = ET.fromstring(xml_text)
    operation_mappings = []

    for receiver_interfaces in find_by_local_name(root, "ReceiverInterfaces"):
        receiver_elem = first_child_by_local_name(receiver_interfaces, "Receiver")
        receiver_component = ""
        if receiver_elem is not None:
            receiver_component = first_text_by_local_names(receiver_elem, ("ComponentID", "Component"))

        receiver_interface_elem = first_child_by_local_name(receiver_interfaces, "ReceiverInterface")
        receiver_interface = design_object_from_elem(receiver_interface_elem)

        rule_elem = first_child_by_local_name(receiver_interfaces, "ReceiverInterfaceRule")
        mapping_elem = None
        if rule_elem is not None:
            mapping_elem = first_child_by_local_name(rule_elem, "Mapping")
        if mapping_elem is None:
            mapping_elem = direct_descendant_by_path(receiver_interfaces, ("ReceiverInterfaceRule", "Mapping"))

        mapping = design_object_from_elem(mapping_elem)
        if not mapping["name"]:
            continue

        operation_mappings.append(
            {
                "receiver_component": receiver_component,
                "receiver_interface_name": receiver_interface["name"],
                "receiver_interface_namespace": receiver_interface["namespace"],
                "operation_mapping": mapping,
            }
        )

    return operation_mappings


def operation_mapping_details(operation_mapping_name, namespace="", software_component_version=""):
    response_xml = post_simple_query(operation_mapping_simple_query_xml())
    rows = parse_simple_query_rows(response_xml)
    return operation_mapping_details_from_rows(
        rows,
        operation_mapping_name,
        namespace=namespace,
        software_component_version=software_component_version,
    )


VALUE_MAPPING_USAGE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bvalueMapping\s*\(",
        r"\bvalueMappingWithContext\s*\(",
        r"\bgetMappedValue\s*\(",
        r"\bValueMappingFactory\b",
        r"\bValueMappingApi\b",
        r"\bcom\.sap\.aii\.mapping\.value\.api\b",
    )
]


def message_mapping_matches(row, message_mapping_name="", namespace="", software_component_version=""):
    if message_mapping_name and first_row_value(row, ("NAME",)) != message_mapping_name:
        return False
    if namespace and first_row_value(row, ("NAMESPACE",)) != namespace:
        return False
    if software_component_version:
        qref = row.get("RA_XILINK_REF") or {}
        caption = qref.get("software_component_version", "")
        if caption != software_component_version and software_component_version not in caption:
            return False
    return True


def message_mapping_read_path(qref):
    params = {
        "method": "PLAIN",
        "TYPE": qref.get("type") or "XI_TRAFO",
        "KEY": qref.get("key") or "|".join(part for part in (qref.get("name", ""), qref.get("namespace", "")) if part),
        "VC": "SWC" if qref.get("vc_type") == "S" else (qref.get("vc_type") or "SWC"),
        "SWCGUID": qref.get("swc_guid", ""),
        "SP": qref.get("sp", "-1"),
        "UC": "false",
        "release": "7.0",
    }
    return "rep/read/ext?{}".format(urllib.parse.urlencode(params))


def query_message_mapping_refs(message_mapping_name="", namespace="", software_component_version=""):
    response_xml = post_simple_query(message_mapping_simple_query_xml())
    rows = parse_simple_query_qref_rows(response_xml)
    refs = []
    seen = set()
    for row in rows:
        if not message_mapping_matches(row, message_mapping_name, namespace, software_component_version):
            continue
        qref = row.get("RA_XILINK_REF") or {}
        if not qref:
            continue
        read_path = message_mapping_read_path(qref)
        if read_path in seen:
            continue
        seen.add(read_path)
        item = {
            "name": first_row_value(row, ("NAME",)) or qref.get("name", ""),
            "namespace": first_row_value(row, ("NAMESPACE",)) or qref.get("namespace", ""),
            "software_component_version": qref.get("software_component_version", ""),
            "kind": first_row_value(row, ("KIND",)),
            "read_path": read_path,
            "qref": qref,
            "raw": row,
        }
        refs.append(item)
    return refs


def public_message_mapping_ref(ref):
    qref = ref.get("qref") or {}
    return {
        "name": ref.get("name", ""),
        "namespace": ref.get("namespace", ""),
        "software_component_version": ref.get("software_component_version", ""),
        "kind": ref.get("kind", ""),
        "type": qref.get("type", ""),
        "oid": qref.get("oid", ""),
        "read_path": ref.get("read_path", ""),
    }


def list_message_mapping_refs(message_mapping_name="", namespace="", software_component_version=""):
    refs = query_message_mapping_refs(
        message_mapping_name=message_mapping_name,
        namespace=namespace,
        software_component_version=software_component_version,
    )
    return {
        "count": len(refs),
        "message_mappings": [public_message_mapping_ref(ref) for ref in refs],
    }


def decode_sap_blob_text(text):
    text = (text or "").strip()
    if len(text) < 6 or text[0] != "!":
        return "", b""
    marker_end = text.find("!", 1)
    if marker_end == -1:
        return "", b""
    marker = text[1:marker_end]
    payload = text[marker_end + 1 :]
    try:
        return marker, base64.b64decode(payload)
    except Exception:
        return marker, b""


def decode_text(data):
    if data.startswith(b"\xca\xfe\xba\xbe"):
        return "", ""
    for encoding in ("utf-8", "utf-16", "iso-8859-1"):
        try:
            value = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if not value:
            return encoding, value
        sample = value[:1000]
        printable = sum(1 for char in sample if char in "\r\n\t" or 32 <= ord(char) < 127)
        if printable >= max(1, int(len(sample) * 0.7)):
            return encoding, value
    return "", ""


def expand_zip_payload(data, path, max_depth=5):
    if max_depth > 0 and data.startswith(b"PK\x03\x04"):
        try:
            entries = []
            with zipfile.ZipFile(io.BytesIO(data)) as zip_file:
                for name in zip_file.namelist():
                    nested_path = "{}/{}".format(path, name) if path else name
                    entries.extend(expand_zip_payload(zip_file.read(name), nested_path, max_depth=max_depth - 1))
            return entries
        except zipfile.BadZipFile:
            pass

    encoding, text = decode_text(data)
    return [
        {
            "path": path,
            "size": len(data),
            "encoding": encoding,
            "text": text,
            "is_text": bool(text),
        }
    ]


def decoded_message_mapping_entries(xml_text):
    root = ET.fromstring(xml_text)
    entries = []
    for index, blob in enumerate(find_by_local_name(root, "blob")):
        marker, data = decode_sap_blob_text(blob.text or "")
        if not data:
            continue
        blob_type = blob.attrib.get("type", marker or "blob")
        path = "{}_{}".format(index, blob_type)
        expanded_entries = expand_zip_payload(data, path)
        for entry in expanded_entries:
            entry["blob_type"] = blob_type
        entries.extend(expanded_entries)
    return entries


def value_mapping_usage_hits(entries):
    hits = []
    for entry in entries:
        text = entry.get("text") or ""
        if not text:
            continue
        for pattern in VALUE_MAPPING_USAGE_PATTERNS:
            match = pattern.search(text)
            if match:
                hits.append(
                    {
                        "path": entry.get("path", ""),
                        "pattern": pattern.pattern,
                        "match": match.group(0),
                    }
                )
                break
    return hits


def message_mapping_source_kind(entry):
    path = (entry.get("path") or "").lower()
    text = entry.get("text") or ""
    if path.endswith("/metadata") or "<mappingtool" in text:
        return "metadata"
    if text.lstrip().startswith("package com.sap.xi.tf") or " extends AMappingProgram" in text:
        return "generated_java"
    if entry.get("is_text"):
        return "text"
    return "binary"


def entry_matches_source_type(entry, source_type):
    source_type = (source_type or "metadata").lower()
    if source_type == "all":
        return bool(entry.get("text"))
    if source_type in ("metadata", "generated_java"):
        return message_mapping_source_kind(entry) == source_type
    if source_type == "text":
        return bool(entry.get("text"))
    return message_mapping_source_kind(entry) == source_type


def source_entries_from_xml(xml_text, source_type="metadata"):
    entries = []
    for entry in decoded_message_mapping_entries(xml_text):
        if not entry_matches_source_type(entry, source_type):
            continue
        entries.append(
            {
                "path": entry.get("path", ""),
                "source_type": message_mapping_source_kind(entry),
                "blob_type": entry.get("blob_type", ""),
                "size": entry.get("size", 0),
                "encoding": entry.get("encoding", ""),
                "text": entry.get("text", ""),
            }
        )
    return entries


def read_message_mapping_xmls(message_mapping_name, namespace="", software_component_version=""):
    refs = query_message_mapping_refs(message_mapping_name, namespace, software_component_version)
    results = []
    for ref in refs:
        response = requests.get(pi_url(ref["read_path"]), auth=(PI_USER, PI_PWD), timeout=60)
        response.raise_for_status()
        results.append((ref, response.text))
    return results


def get_message_mapping_sources(message_mapping_name, namespace="", software_component_version="", source_type="metadata"):
    mappings = []
    for ref, xml_text in read_message_mapping_xmls(message_mapping_name, namespace, software_component_version):
        identity = message_mapping_identity(xml_text)
        if not identity.get("name"):
            identity = public_message_mapping_ref(ref)
        mappings.append(
            {
                "name": identity.get("name", ""),
                "namespace": identity.get("namespace", ""),
                "software_component_version": identity.get("software_component_version", ""),
                "type": identity.get("type", "XI_TRAFO"),
                "oid": identity.get("oid", ""),
                "read_path": ref["read_path"],
                "source_type": source_type,
                "sources": source_entries_from_xml(xml_text, source_type=source_type),
            }
        )
    return {"count": len(mappings), "message_mappings": mappings}


def message_mapping_links(xml_text):
    root = ET.fromstring(xml_text)
    links = []
    for role_elem in find_by_local_name(root, "lnkRole"):
        role = role_elem.attrib.get("role", "")
        for link_elem in find_by_local_name(role_elem, "lnk"):
            key_elem = first_descendant_by_local_name(link_elem, "key")
            if key_elem is None:
                continue
            key_parts = [child.text or "" for child in list(key_elem) if local_name(child.tag) == "elem"]
            links.append(
                {
                    "role": role,
                    "type": key_elem.attrib.get("typeID", ""),
                    "name": key_parts[0] if key_parts else "",
                    "namespace": key_parts[1] if len(key_parts) > 1 else "",
                    "key": "|".join(key_parts),
                }
            )
    return links


def message_mapping_identity(xml_text):
    root = ET.fromstring(xml_text)
    id_info = first_descendant_by_local_name(root, "idInfo")
    key_elem = first_descendant_by_local_name(id_info, "key") if id_info is not None else None
    key_parts = [child.text or "" for child in list(key_elem) if local_name(child.tag) == "elem"] if key_elem is not None else []
    vc_elem = first_descendant_by_local_name(id_info, "vc") if id_info is not None else None
    return {
        "name": key_parts[0] if key_parts else "",
        "namespace": key_parts[1] if len(key_parts) > 1 else "",
        "software_component_version": vc_elem.attrib.get("caption", "") if vc_elem is not None else "",
        "type": key_elem.attrib.get("typeID", "") if key_elem is not None else "",
        "oid": key_elem.attrib.get("oid", "") if key_elem is not None else "",
    }


def summarize_message_mapping_definition(xml_text, include_source=False):
    entries = decoded_message_mapping_entries(xml_text)
    hits = value_mapping_usage_hits(entries)
    entry_summaries = []
    for entry in entries:
        summary = {
            "path": entry.get("path", ""),
            "blob_type": entry.get("blob_type", ""),
            "size": entry.get("size", 0),
            "is_text": entry.get("is_text", False),
            "encoding": entry.get("encoding", ""),
        }
        if include_source and entry.get("text"):
            summary["text"] = entry["text"]
        elif entry.get("text"):
            summary["preview"] = entry["text"][:500]
        entry_summaries.append(summary)

    return {
        "identity": message_mapping_identity(xml_text),
        "links": message_mapping_links(xml_text),
        "uses_value_mapping": bool(hits),
        "value_mapping_hits": hits,
        "decoded_entries": entry_summaries,
    }


def read_message_mapping_definition(message_mapping_name, namespace="", software_component_version="", include_source=False):
    refs = query_message_mapping_refs(message_mapping_name, namespace, software_component_version)
    mappings = []
    for ref in refs:
        response = requests.get(
            pi_url(ref["read_path"]),
            auth=(PI_USER, PI_PWD),
            timeout=60,
        )
        response.raise_for_status()
        summary = summarize_message_mapping_definition(response.text, include_source=include_source)
        identity = summary.get("identity") or {}
        if not identity.get("name"):
            identity = {
                "name": ref.get("name", ""),
                "namespace": ref.get("namespace", ""),
                "software_component_version": ref.get("software_component_version", ""),
                "type": "XI_TRAFO",
                "oid": ref.get("qref", {}).get("oid", ""),
            }
        mapping = dict(identity)
        mapping.update(
            {
                "read_path": ref["read_path"],
                "links": summary["links"],
                "uses_value_mapping": summary["uses_value_mapping"],
                "value_mapping_hits": summary["value_mapping_hits"],
                "decoded_entries": summary["decoded_entries"],
            }
        )
        mappings.append(mapping)
    return {"count": len(mappings), "message_mappings": mappings}


def list_message_mappings_with_value_mapping(namespace="", software_component_version="", limit=0):
    refs = query_message_mapping_refs(namespace=namespace, software_component_version=software_component_version)
    matches = []
    inspected = 0
    for ref in refs:
        if limit and inspected >= limit:
            break
        inspected += 1
        response = requests.get(pi_url(ref["read_path"]), auth=(PI_USER, PI_PWD), timeout=60)
        response.raise_for_status()
        summary = summarize_message_mapping_definition(response.text, include_source=False)
        if not summary["uses_value_mapping"]:
            continue
        identity = summary.get("identity") or {}
        matches.append(
            {
                "name": identity.get("name") or ref.get("name", ""),
                "namespace": identity.get("namespace") or ref.get("namespace", ""),
                "software_component_version": identity.get("software_component_version") or ref.get("software_component_version", ""),
                "value_mapping_hits": summary["value_mapping_hits"],
                "read_path": ref["read_path"],
            }
        )
    return {
        "count": len(matches),
        "inspected": inspected,
        "available": len(refs),
        "message_mappings": matches,
    }


def dependency_category(type_code, role):
    source = "{} {}".format(type_code or "", role or "").upper()
    if "SOURCE" in source:
        return "source"
    if "TARGET" in source:
        return "target"
    if type_code == "FUNC_LIB":
        return "function_library"
    if type_code in ("TRAFO_JAR", "MAP_ARCHIVE_PRG"):
        return "imported_archive"
    if type_code == "MAP_TEMPLATE":
        return "mapping_template"
    if type_code in ("idoc", "rfc", "rfcmsg", "ifmextmes", "ifmfaultm", "ifmmessage"):
        return "message_dependency"
    return "other"


def message_mapping_dependencies_from_rows(rows, message_mapping_name="", namespace="", software_component_version=""):
    grouped = {}
    for row in rows:
        if not message_mapping_matches(row, message_mapping_name, namespace, software_component_version):
            continue
        name = first_row_value(row, ("NAME",))
        row_namespace = first_row_value(row, ("NAMESPACE",))
        group_key = (name, row_namespace)
        if group_key not in grouped:
            grouped[group_key] = {
                "name": name,
                "namespace": row_namespace,
                "software_component_version": "",
                "dependencies": [],
                "raw": [],
            }

        references = value_list(row, ("RA_LINK_TARGET_REFERENCE", "TARGET_REFERENCE", "TARGET"))
        type_codes = value_list(row, ("RA_LINK_TARGET_TYPE", "TARGET_TYPE", "TYPE_CODE"))
        raw_roles = value_list(row, ("RA_LINK_ROLE", "ROLE"))
        max_count = max(len(references), len(type_codes), len(raw_roles), 0)
        for index in range(max_count):
            reference = references[index] if index < len(references) else ""
            type_code = type_codes[index] if index < len(type_codes) else ""
            raw_role = raw_roles[index] if index < len(raw_roles) else ""
            if not reference and not type_code and not raw_role:
                continue
            parsed_reference = split_design_object_reference(reference)
            dependency = {
                "name": parsed_reference["name"],
                "namespace": parsed_reference["namespace"],
                "software_component_version": parsed_reference["software_component_version"],
                "type_code": type_code,
                "raw_role": raw_role,
                "category": dependency_category(type_code, raw_role),
                "raw_reference": reference,
            }
            if dependency not in grouped[group_key]["dependencies"]:
                grouped[group_key]["dependencies"].append(dependency)
        grouped[group_key]["raw"].append(row)

    return {"count": len(grouped), "message_mappings": list(grouped.values())}


def get_message_mapping_dependency_details(message_mapping_name, namespace="", software_component_version=""):
    response_xml = post_simple_query(message_mapping_simple_query_xml())
    rows = parse_simple_query_rows(response_xml)
    details = message_mapping_dependencies_from_rows(
        rows,
        message_mapping_name=message_mapping_name,
        namespace=namespace,
        software_component_version=software_component_version,
    )
    if details["count"] == 0 and software_component_version:
        details = message_mapping_dependencies_from_rows(
            rows,
            message_mapping_name=message_mapping_name,
            namespace=namespace,
        )
    refs = {
        (ref["name"], ref["namespace"]): ref
        for ref in query_message_mapping_refs(message_mapping_name, namespace, software_component_version)
    }
    for mapping in details["message_mappings"]:
        ref = refs.get((mapping["name"], mapping["namespace"]))
        if ref:
            mapping["software_component_version"] = ref.get("software_component_version", "")
            mapping["read_path"] = ref.get("read_path", "")
            mapping["oid"] = (ref.get("qref") or {}).get("oid", "")
    return details


def extract_mapping_paths_from_metadata(text):
    if not text:
        return {"source_paths": [], "target_paths": []}
    source_paths = sorted(set(re.findall(r'<brick\b[^>]*\btype="Src"[^>]*\bpath="([^"]+)"', text)))
    target_paths = sorted(set(re.findall(r'<brick\b[^>]*\btype="Dst"[^>]*\bpath="([^"]+)"', text)))
    return {"source_paths": source_paths, "target_paths": target_paths}


def text_entries_by_kind(xml_text):
    entries = decoded_message_mapping_entries(xml_text)
    grouped = {"metadata": [], "generated_java": [], "text": []}
    for entry in entries:
        if not entry.get("text"):
            continue
        kind = message_mapping_source_kind(entry)
        if kind in grouped:
            grouped[kind].append(entry)
        else:
            grouped["text"].append(entry)
    return grouped


def analyze_message_mapping_details(message_mapping_name, namespace="", software_component_version="", include_source=False):
    dependency_result = get_message_mapping_dependency_details(message_mapping_name, namespace, software_component_version)
    dependency_by_key = {
        (item["name"], item["namespace"]): item
        for item in dependency_result.get("message_mappings", [])
    }
    mappings = []
    for ref, xml_text in read_message_mapping_xmls(message_mapping_name, namespace, software_component_version):
        summary = summarize_message_mapping_definition(xml_text, include_source=include_source)
        identity = summary.get("identity") or {}
        if not identity.get("name"):
            identity = public_message_mapping_ref(ref)
        text_entries = text_entries_by_kind(xml_text)
        metadata_text = "\n".join(entry.get("text", "") for entry in text_entries["metadata"])
        generated_java_text = "\n".join(entry.get("text", "") for entry in text_entries["generated_java"])
        path_summary = extract_mapping_paths_from_metadata(metadata_text)
        dependencies = dependency_by_key.get((identity.get("name", ""), identity.get("namespace", "")), {}).get("dependencies", [])
        mapping = {
            "name": identity.get("name", ""),
            "namespace": identity.get("namespace", ""),
            "software_component_version": identity.get("software_component_version", ""),
            "type": identity.get("type", "XI_TRAFO"),
            "oid": identity.get("oid", ""),
            "read_path": ref["read_path"],
            "links": summary["links"],
            "dependencies": dependencies,
            "uses_value_mapping": summary["uses_value_mapping"],
            "value_mapping_hits": summary["value_mapping_hits"],
            "uses_function_libraries": any(dep.get("category") == "function_library" for dep in dependencies),
            "function_libraries": [dep for dep in dependencies if dep.get("category") == "function_library"],
            "uses_imported_archives": any(dep.get("category") == "imported_archive" for dep in dependencies),
            "imported_archives": [dep for dep in dependencies if dep.get("category") == "imported_archive"],
            "source_paths": path_summary["source_paths"],
            "target_paths": path_summary["target_paths"],
            "decoded_entries": summary["decoded_entries"],
            "generated_java_available": bool(generated_java_text),
        }
        mappings.append(mapping)
    return {"count": len(mappings), "message_mappings": mappings}


def ico_message_mapping_definitions(sender_component, interface_name, interface_namespace, sender_party="", receiver_party="", receiver_component="", include_source=False):
    body = (
        "<bas:IntegratedConfigurationReadRequest>"
        "<ReadContext>Active</ReadContext>"
        + integrated_configuration_id_xml(sender_component, interface_name, interface_namespace, sender_party, receiver_party, receiver_component)
        + "</bas:IntegratedConfigurationReadRequest>"
    )
    ico_xml = post_soap("IntegratedConfiguration750InService/IntegratedConfiguration750InImplBean", body)
    assigned_mappings = operation_mappings_from_ico_xml(ico_xml)
    simple_query_xml = post_simple_query(operation_mapping_simple_query_xml())
    simple_query_rows = parse_simple_query_rows(simple_query_xml)
    results = []

    for assigned_mapping in assigned_mappings:
        operation_mapping = assigned_mapping["operation_mapping"]
        details = operation_mapping_details_from_rows(
            simple_query_rows,
            operation_mapping["name"],
            namespace=operation_mapping["namespace"],
            software_component_version=operation_mapping["software_component_version"],
        )
        if details["count"] == 0 and operation_mapping["software_component_version"]:
            details = operation_mapping_details_from_rows(
                simple_query_rows,
                operation_mapping["name"],
                namespace=operation_mapping["namespace"],
            )

        message_mappings = []
        for detail in details.get("operation_mappings", []):
            for transformation in detail.get("transformations", []):
                if transformation.get("type_code") and not transformation["type_code"].upper().endswith("_TRAFO"):
                    continue
                if not transformation.get("name"):
                    continue
                definition = analyze_message_mapping_details(
                    transformation["name"],
                    namespace=transformation.get("namespace", ""),
                    software_component_version=transformation.get("software_component_version", ""),
                    include_source=include_source,
                )
                message_mappings.extend(definition.get("message_mappings", []))

        result = dict(assigned_mapping)
        result["operation_mapping"] = operation_mapping
        result["message_mapping_count"] = len(message_mappings)
        result["message_mappings"] = message_mappings
        results.append(result)

    return {
        "count": len(results),
        "ico": {
            "sender_party": sender_party,
            "sender_component": sender_component,
            "interface_name": interface_name,
            "interface_namespace": interface_namespace,
            "receiver_party": receiver_party,
            "receiver_component": receiver_component,
        },
        "operation_mappings": results,
    }


VALUE_MAPPING_CSV_COLUMNS = [
    "value_mapping_id",
    "group_name",
    "master_language",
    "description",
    "responsible_user",
    "last_change_user",
    "last_change_datetime",
    "folder_path",
    "representation_value",
    "scheme_id",
    "scheme_agency_id",
]


def value_mapping_representation_xml(representation="", scheme_id="", scheme_agency_id=""):
    if not (representation or scheme_id or scheme_agency_id):
        return ""
    attrs = ""
    if scheme_id:
        attrs += " schemeID={}".format(xml_attr(scheme_id))
    if scheme_agency_id:
        attrs += " schemeAgencyID={}".format(xml_attr(scheme_agency_id))
    return "<Representation{}>{}</Representation>".format(attrs, xml_value(representation))


def value_mapping_query_body(value_mapping_id="", group_name="", representation="", scheme_id="", scheme_agency_id=""):
    filters = optional_xml("ValueMappingID", value_mapping_id) + optional_xml("GroupName", group_name)
    filters += value_mapping_representation_xml(representation, scheme_id, scheme_agency_id)
    return "<bas:ValueMappingQueryRequest>{}</bas:ValueMappingQueryRequest>".format(filters)


def value_mapping_read_body(value_mapping_ids):
    ids_xml = "".join(optional_xml("ValueMappingID", value_mapping_id) for value_mapping_id in value_mapping_ids)
    return "<bas:ValueMappingReadRequest><ReadContext>Active</ReadContext>{}</bas:ValueMappingReadRequest>".format(ids_xml)


def query_value_mapping_ids(value_mapping_id="", group_name="", representation="", scheme_id="", scheme_agency_id=""):
    response_xml = post_soap(
        "ValueMappingInService/ValueMappingInImplBean",
        value_mapping_query_body(value_mapping_id, group_name, representation, scheme_id, scheme_agency_id),
    )
    return [elem.text or "" for elem in find_by_local_name(ET.fromstring(response_xml), "ValueMappingID") if elem.text]


def read_value_mappings_xml(value_mapping_ids):
    if not value_mapping_ids:
        return ""
    return post_soap("ValueMappingInService/ValueMappingInImplBean", value_mapping_read_body(value_mapping_ids), timeout=60)


def value_mapping_description(mapping_elem):
    descriptions = []
    for description_elem in children_by_local_name(mapping_elem, "Description"):
        description = element_direct_text(description_elem)
        if description:
            descriptions.append(description)
    return " | ".join(descriptions)


def administrative_data_row(mapping_elem):
    admin_elem = first_child_by_local_name(mapping_elem, "AdministrativeData")
    if admin_elem is None:
        return {
            "responsible_user": "",
            "last_change_user": "",
            "last_change_datetime": "",
            "folder_path": "",
        }
    return {
        "responsible_user": child_text(admin_elem, "ResponsibleUserAccountID"),
        "last_change_user": child_text(admin_elem, "LastChangeUserAccountID"),
        "last_change_datetime": child_text(admin_elem, "LastChangeDateTime"),
        "folder_path": child_text(admin_elem, "FolderPathID"),
    }


def parse_value_mapping_rows(xml_text):
    if not (xml_text or "").strip():
        return []

    root = ET.fromstring(xml_text)
    rows = []
    for mapping_elem in find_by_local_name(root, "ValueMapping"):
        base_row = {
            "value_mapping_id": child_text(mapping_elem, "ValueMappingID"),
            "group_name": child_text(mapping_elem, "GroupName"),
            "master_language": child_text(mapping_elem, "MasterLanguage"),
            "description": value_mapping_description(mapping_elem),
        }
        base_row.update(administrative_data_row(mapping_elem))

        for representation_elem in children_by_local_name(mapping_elem, "Representation"):
            row = dict(base_row)
            row.update(
                {
                    "representation_value": element_direct_text(representation_elem),
                    "scheme_id": representation_elem.attrib.get("schemeID", ""),
                    "scheme_agency_id": representation_elem.attrib.get("schemeAgencyID", ""),
                }
            )
            rows.append(row)
    return rows


def value_mapping_rows_to_csv(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=VALUE_MAPPING_CSV_COLUMNS, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in VALUE_MAPPING_CSV_COLUMNS})
    return output.getvalue()


def export_value_mappings_csv_text(value_mapping_id="", group_name="", representation="", scheme_id="", scheme_agency_id=""):
    value_mapping_ids = query_value_mapping_ids(
        value_mapping_id=value_mapping_id,
        group_name=group_name,
        representation=representation,
        scheme_id=scheme_id,
        scheme_agency_id=scheme_agency_id,
    )
    xml_text = read_value_mappings_xml(value_mapping_ids)
    return value_mapping_rows_to_csv(parse_value_mapping_rows(xml_text))


def communication_channel_id_xml(component_id, channel_id, party_id=""):
    return (
        "<CommunicationChannelID>"
        + optional_xml("PartyID", party_id)
        + optional_xml("ComponentID", component_id)
        + optional_xml("ChannelID", channel_id)
        + "</CommunicationChannelID>"
    )


def integrated_configuration_id_xml(sender_component, interface_name, interface_namespace, sender_party="", receiver_party="", receiver_component=""):
    return (
        "<IntegratedConfigurationID>"
        + optional_xml("SenderPartyID", sender_party)
        + optional_xml("SenderComponentID", sender_component)
        + optional_xml("InterfaceName", interface_name)
        + optional_xml("InterfaceNamespace", interface_namespace)
        + optional_xml("ReceiverPartyID", receiver_party)
        + optional_xml("ReceiverComponentID", receiver_component)
        + "</IntegratedConfigurationID>"
    )


def message_monitoring_time_range(hours_back):
    now = datetime.datetime.utcnow()
    start_time = (now - datetime.timedelta(hours=hours_back)).isoformat() + "Z"
    end_time = now.isoformat() + "Z"
    return start_time, end_time


def message_monitoring_filter_xml(
    start_time,
    end_time,
    status="",
    only_faulty_messages=False,
    sender_component="",
    interface_name="",
    interface_namespace="",
):
    interface_filter = ""
    if interface_name or interface_namespace:
        interface_filter = (
            "<urn1:senderInterface>"
            + optional_xml("dat:name", interface_name)
            + optional_xml("dat:namespace", interface_namespace)
            + "</urn1:senderInterface>"
        )

    return (
        "<filter>"
        "<urn1:archive>false</urn1:archive>"
        "<urn1:dateType>0</urn1:dateType>"
        + optional_xml("urn1:fromTime", start_time)
        + "<urn1:nodeId>0</urn1:nodeId>"
        + "<urn1:onlyFaultyMessages>{}</urn1:onlyFaultyMessages>".format(str(only_faulty_messages).lower())
        + "<urn1:retries>0</urn1:retries>"
        + "<urn1:retryInterval>0</urn1:retryInterval>"
        + interface_filter
        + optional_xml("urn1:senderName", sender_component)
        + optional_xml("urn1:status", status)
        + "<urn1:timesFailed>0</urn1:timesFailed>"
        + optional_xml("urn1:toTime", end_time)
        + "<urn1:wasEdited>false</urn1:wasEdited>"
        + "</filter>"
    )


def adapter_message_rows(xml_text):
    root = ET.fromstring(xml_text)
    rows = []
    for elem in find_by_local_name(root, "AdapterFrameworkData"):
        row = {}
        for child in list(elem):
            name = local_name(child.tag)
            if name in ("messageID", "messageKey", "senderName", "receiverName", "status", "startTime", "endTime", "direction", "qualityOfService"):
                row[name] = child.text or ""
            elif name in ("senderInterface", "receiverInterface"):
                for interface_child in list(child):
                    child_name = local_name(interface_child.tag)
                    if child_name == "name":
                        row[name] = interface_child.text or ""
                    elif child_name == "namespace":
                        row[name + "Namespace"] = interface_child.text or ""
        rows.append(row)
    return rows


def filter_adapter_message_rows(rows, sender_component="", interface_name="", interface_namespace=""):
    filtered = []
    for row in rows:
        if sender_component and row.get("senderName") != sender_component:
            continue
        if interface_name:
            sender_match = row.get("senderInterface") == interface_name
            receiver_match = row.get("receiverInterface") == interface_name
            if not sender_match and not receiver_match:
                continue
        if interface_namespace:
            sender_match = row.get("senderInterfaceNamespace") == interface_namespace
            receiver_match = row.get("receiverInterfaceNamespace") == interface_namespace
            if not sender_match and not receiver_match:
                continue
        filtered.append(row)
    return filtered


def message_search_json(xml_text, sender_component="", interface_name="", interface_namespace=""):
    rows = adapter_message_rows(xml_text)
    filtered = filter_adapter_message_rows(
        rows,
        sender_component=sender_component,
        interface_name=interface_name,
        interface_namespace=interface_namespace,
    )
    return json.dumps(
        {
            "returned_count": len(rows),
            "count": len(filtered),
            "messages": filtered,
        },
        indent=2,
    )


def get_message_list(start_time, end_time, max_messages=100, status="", only_faulty_messages=False, sender_component="", interface_name="", interface_namespace=""):
    filter_xml = message_monitoring_filter_xml(
        start_time,
        end_time,
        status=status,
        only_faulty_messages=only_faulty_messages,
        sender_component=sender_component,
        interface_name=interface_name,
        interface_namespace=interface_namespace,
    )
    soap_body = """
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:urn="urn:AdapterMessageMonitoringVi" xmlns:urn1="urn:com.sap.aii.mdt.server.adapterframework.ws" xmlns:dat="urn:com.sap.aii.mdt.api.data">
       <soapenv:Body>
          <urn:getMessageList>
             {filter_xml}
             <maxMessages>{max_messages}</maxMessages>
          </urn:getMessageList>
       </soapenv:Body>
    </soapenv:Envelope>
    """.format(filter_xml=filter_xml, max_messages=max(1, int(max_messages)))

    headers = {'Content-Type': 'text/xml;charset=UTF-8', 'SOAPAction': 'getMessageList'}
    url = pi_url("AdapterMessageMonitoring/basic?style=document")
    response = requests.post(url, data=soap_body, headers=headers, auth=(PI_USER, PI_PWD), timeout=30)
    response.raise_for_status()
    return response.text


@mcp.tool()
def get_pi_channel_status(channel_name: str) -> str:
    """
    Checks the status of a specific SAP PI Communication Channel.
    """
    # SAP PI SOAP endpoint for Monitoring
    url = pi_url("AdapterFramework/ChannelAdminServlet?party=*&service=*&channel={}&action=status".format(urllib.parse.quote(channel_name, safe="")))
    
    try:
        # Calling PI (Simplified GET for the AdminServlet, or use SOAP for Directory API)
        response = requests.get(url, auth=(PI_USER, PI_PWD), timeout=10)
        response.raise_for_status()
        
        # Return the raw XML or parse it for the AI
        return f"Status for {channel_name}:\n{response.text[:500]}..." 
    except Exception as e:
        return f"Error connecting to SAP PI: {str(e)}"

@mcp.tool()
def search_failed_messages(hours_back: int = 1) -> str:
    """
    Searches for failed messages in the last X hours.
    """
    try:
        start_time, end_time = message_monitoring_time_range(hours_back)
        return get_message_list(
            start_time,
            end_time,
            max_messages=10,
            status="error",
            only_faulty_messages=True,
        )
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def search_processed_messages(hours_back: int = 1) -> str:
    """
    Searches for successfully processed messages in the last X hours.
    """
    try:
        start_time, end_time = message_monitoring_time_range(hours_back)
        return get_message_list(
            start_time,
            end_time,
            max_messages=10,
            status="success",
        )
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def search_messages_by_sender_component(sender_component: str, hours_back: int = 1, status: str = "", max_messages: int = 100) -> str:
    """
    Searches SAP PI message monitoring by sender component in the last X hours.
    Optional status examples: success, error.
    """
    try:
        start_time, end_time = message_monitoring_time_range(hours_back)
        xml_text = get_message_list(
            start_time,
            end_time,
            max_messages=max_messages,
            status=status,
            only_faulty_messages=status.lower() == "error",
            sender_component=sender_component,
        )
        return message_search_json(xml_text, sender_component=sender_component)
    except Exception as e:
        return f"Error searching messages by sender component: {str(e)}"

@mcp.tool()
def search_messages_by_interface(interface_name: str, interface_namespace: str = "", hours_back: int = 1, status: str = "", max_messages: int = 100) -> str:
    """
    Searches SAP PI message monitoring by interface name and optional namespace in the last X hours.
    Optional status examples: success, error.
    """
    try:
        start_time, end_time = message_monitoring_time_range(hours_back)
        xml_text = get_message_list(
            start_time,
            end_time,
            max_messages=max_messages,
            status=status,
            only_faulty_messages=status.lower() == "error",
            interface_name=interface_name,
            interface_namespace=interface_namespace,
        )
        return message_search_json(
            xml_text,
            interface_name=interface_name,
            interface_namespace=interface_namespace,
        )
    except Exception as e:
        return f"Error searching messages by interface: {str(e)}"

@mcp.tool()
def search_messages_by_interface_and_sender(sender_component: str, interface_name: str, interface_namespace: str = "", hours_back: int = 1, status: str = "", max_messages: int = 100) -> str:
    """
    Searches SAP PI message monitoring by sender component and interface in the last X hours.
    Optional status examples: success, error.
    """
    try:
        start_time, end_time = message_monitoring_time_range(hours_back)
        xml_text = get_message_list(
            start_time,
            end_time,
            max_messages=max_messages,
            status=status,
            only_faulty_messages=status.lower() == "error",
            sender_component=sender_component,
            interface_name=interface_name,
            interface_namespace=interface_namespace,
        )
        return message_search_json(
            xml_text,
            sender_component=sender_component,
            interface_name=interface_name,
            interface_namespace=interface_namespace,
        )
    except Exception as e:
        return f"Error: {str(e)}"

@mcp.tool()
def get_service_interface_details(name: str, namespace: str) -> str:
    """
    Fetches full metadata for an SAP PI Service Interface (Operations, Type, etc.).
    """
    soap_body = f"""
    <soapenv:Envelope xmlns:soapenv="http://xmlsoap.org" xmlns:bas="http://sap.com/xi/BASIS">
       <soapenv:Body>
          <bas:ServiceInterfaceReadRequest>
             <ReadContext>Active</ReadContext>
             <ServiceInterfaceID>
                <Name>{escape(name)}</Name>
                <Namespace>{escape(namespace)}</Namespace>
             </ServiceInterfaceID>
          </bas:ServiceInterfaceReadRequest>
       </soapenv:Body>
    </soapenv:Envelope>
    """

    # Note: URL structure for Directory APIs is different from Monitoring APIs
    url = pi_url("ServiceInterfaceInService/ServiceInterfaceInImplBean")
    headers = {'Content-Type': 'text/xml;charset=UTF-8'}

    try:
        response = requests.post(url, data=soap_body, headers=headers, auth=(PI_USER, PI_PWD))
        return response.text
    except Exception as e:
        return f"Error fetching interface details: {str(e)}"

@mcp.tool()
def get_integrated_configuration_details(sender_component: str, interface_name: str, interface_namespace: str) -> str:
    """
    Fetches full metadata for an SAP PI Integrated Configuration (ICO).
    """
    soap_body = f"""
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:bas="http://sap.com/xi/BASIS">
       <soapenv:Header/>
       <soapenv:Body>
          <bas:IntegratedConfigurationReadRequest>
             <ReadContext>Active</ReadContext>
             <IntegratedConfigurationID>
                <SenderComponentID>{escape(sender_component)}</SenderComponentID>
                <InterfaceName>{escape(interface_name)}</InterfaceName>
                <InterfaceNamespace>{escape(interface_namespace)}</InterfaceNamespace>
             </IntegratedConfigurationID>
          </bas:IntegratedConfigurationReadRequest>
       </soapenv:Body>
    </soapenv:Envelope>
    """

    url = pi_url("IntegratedConfigurationInService/IntegratedConfigurationInImplBean")
    headers = {'Content-Type': 'text/xml;charset=UTF-8', 'SOAPAction': 'http://sap.com/xi/WebService/soap1.1'}

    try:
        response = requests.post(url, data=soap_body, headers=headers, auth=(PI_USER, PI_PWD))
        return response.text
    except Exception as e:
        return f"Error fetching ICO details: {str(e)}"

@mcp.tool()
def list_integrated_configurations() -> str:
    """
    Lists all SAP PI Integrated Configurations (ICOs).
    """
    soap_body = """
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:bas="http://sap.com/xi/BASIS">
       <soapenv:Header/>
       <soapenv:Body>
          <bas:IntegratedConfigurationQueryRequest>
          </bas:IntegratedConfigurationQueryRequest>
       </soapenv:Body>
    </soapenv:Envelope>
    """

    url = pi_url("IntegratedConfigurationInService/IntegratedConfigurationInImplBean")
    headers = {'Content-Type': 'text/xml;charset=UTF-8', 'SOAPAction': 'http://sap.com/xi/WebService/soap1.1'}

    try:
        response = requests.post(url, data=soap_body, headers=headers, auth=(PI_USER, PI_PWD), timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        icos = []

        for ico_id in root.findall('.//IntegratedConfigurationID'):
            icos.append({
                "sender_party": ico_id.findtext('SenderPartyID', default=''),
                "sender_component": ico_id.findtext('SenderComponentID', default=''),
                "interface_name": ico_id.findtext('InterfaceName', default=''),
                "interface_namespace": ico_id.findtext('InterfaceNamespace', default=''),
                "receiver_party": ico_id.findtext('ReceiverPartyID', default=''),
                "receiver_component": ico_id.findtext('ReceiverComponentID', default=''),
            })

        return json.dumps({"count": len(icos), "integrated_configurations": icos}, indent=2)
    except Exception as e:
        return f"Error listing ICOs: {str(e)}"

@mcp.tool()
def list_communication_channels(component_id: str = "", channel_id: str = "", party_id: str = "") -> str:
    """
    Lists SAP PI Communication Channels, optionally filtered by component, channel, or party.
    """
    try:
        query_id = ""
        if component_id or channel_id or party_id:
            query_id = communication_channel_id_xml(component_id, channel_id, party_id)

        response_xml = post_soap(
            "CommunicationChannelInService/CommunicationChannelInImplBean",
            "<bas:CommunicationChannelQueryRequest>{}</bas:CommunicationChannelQueryRequest>".format(query_id),
        )
        channels = parse_response_ids(
            response_xml,
            "CommunicationChannelID",
            ["PartyID", "ComponentID", "ChannelID"],
        )
        return json_result("communication_channels", channels)
    except Exception as e:
        return f"Error listing communication channels: {str(e)}"

@mcp.tool()
def get_communication_channel_details(component_id: str, channel_id: str, party_id: str = "") -> str:
    """
    Fetches full metadata for an SAP PI Communication Channel.
    """
    try:
        body = (
            "<bas:CommunicationChannelReadRequest>"
            "<ReadContext>Active</ReadContext>"
            + communication_channel_id_xml(component_id, channel_id, party_id)
            + "</bas:CommunicationChannelReadRequest>"
        )
        return post_soap("CommunicationChannelInService/CommunicationChannelInImplBean", body)
    except Exception as e:
        return f"Error fetching communication channel details: {str(e)}"

@mcp.tool()
def check_communication_channel(component_id: str, channel_id: str, party_id: str = "") -> str:
    """
    Validates an SAP PI Communication Channel and returns SAP log messages.
    """
    try:
        body = (
            "<bas:CommunicationChannelCheckRequest>"
            + communication_channel_id_xml(component_id, channel_id, party_id)
            + "</bas:CommunicationChannelCheckRequest>"
        )
        return post_soap("CommunicationChannelInService/CommunicationChannelInImplBean", body)
    except Exception as e:
        return f"Error checking communication channel: {str(e)}"

@mcp.tool()
def list_integrated_configurations_750() -> str:
    """
    Lists SAP PI Integrated Configurations using the PO 7.5 IntegratedConfiguration750 service.
    """
    try:
        response_xml = post_soap(
            "IntegratedConfiguration750InService/IntegratedConfiguration750InImplBean",
            "<bas:IntegratedConfigurationQueryRequest></bas:IntegratedConfigurationQueryRequest>",
        )
        icos = parse_response_ids(
            response_xml,
            "IntegratedConfigurationID",
            ["SenderPartyID", "SenderComponentID", "InterfaceName", "InterfaceNamespace", "ReceiverPartyID", "ReceiverComponentID"],
        )
        return json_result("integrated_configurations", icos)
    except Exception as e:
        return f"Error listing ICOs with IntegratedConfiguration750: {str(e)}"

@mcp.tool()
def get_integrated_configuration_750_details(sender_component: str, interface_name: str, interface_namespace: str, sender_party: str = "", receiver_party: str = "", receiver_component: str = "") -> str:
    """
    Fetches full PO 7.5 metadata for an SAP PI Integrated Configuration.
    """
    try:
        body = (
            "<bas:IntegratedConfigurationReadRequest>"
            "<ReadContext>Active</ReadContext>"
            + integrated_configuration_id_xml(sender_component, interface_name, interface_namespace, sender_party, receiver_party, receiver_component)
            + "</bas:IntegratedConfigurationReadRequest>"
        )
        return post_soap("IntegratedConfiguration750InService/IntegratedConfiguration750InImplBean", body)
    except Exception as e:
        return f"Error fetching IntegratedConfiguration750 details: {str(e)}"

@mcp.tool()
def check_integrated_configuration_750(sender_component: str, interface_name: str, interface_namespace: str, sender_party: str = "", receiver_party: str = "", receiver_component: str = "") -> str:
    """
    Validates an SAP PI Integrated Configuration using the PO 7.5 service.
    """
    try:
        body = (
            "<bas:IntegratedConfigurationCheckRequest>"
            + integrated_configuration_id_xml(sender_component, interface_name, interface_namespace, sender_party, receiver_party, receiver_component)
            + "</bas:IntegratedConfigurationCheckRequest>"
        )
        return post_soap("IntegratedConfiguration750InService/IntegratedConfiguration750InImplBean", body)
    except Exception as e:
        return f"Error checking IntegratedConfiguration750: {str(e)}"

@mcp.tool()
def get_operation_mapping_details(operation_mapping_name: str, namespace: str = "", software_component_version: str = "") -> str:
    """
    Fetches SAP PI/PO Operation Mapping metadata and linked transformation programs from ESR SimpleQuery.
    """
    try:
        details = operation_mapping_details(
            operation_mapping_name,
            namespace=namespace,
            software_component_version=software_component_version,
        )
        return json.dumps(details, indent=2)
    except Exception as e:
        return f"Error fetching operation mapping details: {str(e)}"

@mcp.tool()
def get_ico_operation_mapping_transformations(sender_component: str, interface_name: str, interface_namespace: str, sender_party: str = "", receiver_party: str = "", receiver_component: str = "") -> str:
    """
    Reads an ICO, finds assigned Operation Mappings, and returns their linked transformation programs.
    """
    try:
        body = (
            "<bas:IntegratedConfigurationReadRequest>"
            "<ReadContext>Active</ReadContext>"
            + integrated_configuration_id_xml(sender_component, interface_name, interface_namespace, sender_party, receiver_party, receiver_component)
            + "</bas:IntegratedConfigurationReadRequest>"
        )
        ico_xml = post_soap("IntegratedConfiguration750InService/IntegratedConfiguration750InImplBean", body)
        assigned_mappings = operation_mappings_from_ico_xml(ico_xml)

        simple_query_xml = post_simple_query(operation_mapping_simple_query_xml())
        simple_query_rows = parse_simple_query_rows(simple_query_xml)
        enriched_mappings = []

        for assigned_mapping in assigned_mappings:
            mapping = assigned_mapping["operation_mapping"]
            details = operation_mapping_details_from_rows(
                simple_query_rows,
                mapping["name"],
                namespace=mapping["namespace"],
                software_component_version=mapping["software_component_version"],
            )
            if details["count"] == 0 and mapping["software_component_version"]:
                details = operation_mapping_details_from_rows(
                    simple_query_rows,
                    mapping["name"],
                    namespace=mapping["namespace"],
                )
            operation_mapping = dict(mapping)
            operation_mapping["matches"] = details["count"]
            operation_mapping["transformations"] = []
            if details["operation_mappings"]:
                operation_mapping["kind"] = details["operation_mappings"][0].get("kind", "")
                operation_mapping["transformations"] = details["operation_mappings"][0].get("transformations", [])

            enriched = dict(assigned_mapping)
            enriched["operation_mapping"] = operation_mapping
            enriched_mappings.append(enriched)

        return json.dumps(
            {
                "count": len(enriched_mappings),
                "ico": {
                    "sender_party": sender_party,
                    "sender_component": sender_component,
                    "interface_name": interface_name,
                    "interface_namespace": interface_namespace,
                    "receiver_party": receiver_party,
                    "receiver_component": receiver_component,
                },
                "operation_mappings": enriched_mappings,
            },
            indent=2,
        )
    except Exception as e:
        return f"Error fetching ICO operation mapping transformations: {str(e)}"

@mcp.tool()
def get_message_mapping_definition(message_mapping_name: str, namespace: str = "", software_component_version: str = "", include_source: bool = False) -> str:
    """
    Reads an SAP PI/PO Message Mapping (XI_TRAFO) definition from ESR and decodes its embedded metadata/source blobs.
    """
    try:
        return json.dumps(
            read_message_mapping_definition(
                message_mapping_name,
                namespace=namespace,
                software_component_version=software_component_version,
                include_source=include_source,
            ),
            indent=2,
        )
    except Exception as e:
        return f"Error fetching message mapping definition: {str(e)}"

@mcp.tool()
def list_message_mappings(name: str = "", namespace: str = "", software_component_version: str = "") -> str:
    """
    Lists SAP PI/PO Message Mappings (XI_TRAFO) from ESR SimpleQuery.
    """
    try:
        return json.dumps(
            list_message_mapping_refs(
                message_mapping_name=name,
                namespace=namespace,
                software_component_version=software_component_version,
            ),
            indent=2,
        )
    except Exception as e:
        return f"Error listing message mappings: {str(e)}"

@mcp.tool()
def get_message_mapping_source(message_mapping_name: str, namespace: str = "", software_component_version: str = "", source_type: str = "metadata") -> str:
    """
    Reads decoded Message Mapping source entries. source_type can be metadata, generated_java, text, or all.
    """
    try:
        return json.dumps(
            get_message_mapping_sources(
                message_mapping_name,
                namespace=namespace,
                software_component_version=software_component_version,
                source_type=source_type,
            ),
            indent=2,
        )
    except Exception as e:
        return f"Error fetching message mapping source: {str(e)}"

@mcp.tool()
def get_message_mapping_dependencies(message_mapping_name: str, namespace: str = "", software_component_version: str = "") -> str:
    """
    Lists ESR dependencies for a Message Mapping, including source/target messages, function libraries, archives, templates, IDocs, and RFCs.
    """
    try:
        return json.dumps(
            get_message_mapping_dependency_details(
                message_mapping_name,
                namespace=namespace,
                software_component_version=software_component_version,
            ),
            indent=2,
        )
    except Exception as e:
        return f"Error fetching message mapping dependencies: {str(e)}"

@mcp.tool()
def analyze_message_mapping(message_mapping_name: str, namespace: str = "", software_component_version: str = "", include_source: bool = False) -> str:
    """
    Returns a consolidated analysis of a Message Mapping definition, dependencies, source/target paths, value mappings, UDFs, and imported archives.
    """
    try:
        return json.dumps(
            analyze_message_mapping_details(
                message_mapping_name,
                namespace=namespace,
                software_component_version=software_component_version,
                include_source=include_source,
            ),
            indent=2,
        )
    except Exception as e:
        return f"Error analyzing message mapping: {str(e)}"

@mcp.tool()
def get_ico_message_mapping_definitions(sender_component: str, interface_name: str, interface_namespace: str, sender_party: str = "", receiver_party: str = "", receiver_component: str = "", include_source: bool = False) -> str:
    """
    Starts from an ICO, finds assigned Operation Mappings, and returns analysis for each assigned Message Mapping.
    """
    try:
        return json.dumps(
            ico_message_mapping_definitions(
                sender_component,
                interface_name,
                interface_namespace,
                sender_party=sender_party,
                receiver_party=receiver_party,
                receiver_component=receiver_component,
                include_source=include_source,
            ),
            indent=2,
        )
    except Exception as e:
        return f"Error fetching ICO message mapping definitions: {str(e)}"

@mcp.tool()
def list_message_mappings_using_value_mapping(namespace: str = "", software_component_version: str = "", limit: int = 0) -> str:
    """
    Scans SAP PI/PO Message Mapping definitions and lists mappings that call value-mapping APIs/functions.
    """
    try:
        return json.dumps(
            list_message_mappings_with_value_mapping(
                namespace=namespace,
                software_component_version=software_component_version,
                limit=limit,
            ),
            indent=2,
        )
    except Exception as e:
        return f"Error listing message mappings using value mapping: {str(e)}"

@mcp.tool()
def list_value_mappings(value_mapping_id: str = "", group_name: str = "", representation: str = "", scheme_id: str = "", scheme_agency_id: str = "") -> str:
    """
    Lists SAP PI Value Mappings, optionally filtered by ID, group name, or representation.
    """
    try:
        response_xml = post_soap(
            "ValueMappingInService/ValueMappingInImplBean",
            value_mapping_query_body(value_mapping_id, group_name, representation, scheme_id, scheme_agency_id),
        )
        mappings = [{"value_mapping_id": elem.text or ""} for elem in find_by_local_name(ET.fromstring(response_xml), "ValueMappingID")]
        return json_result("value_mappings", mappings)
    except Exception as e:
        return f"Error listing value mappings: {str(e)}"

@mcp.tool()
def get_value_mapping_details(value_mapping_id: str) -> str:
    """
    Fetches full metadata for an SAP PI Value Mapping.
    """
    try:
        body = (
            "<bas:ValueMappingReadRequest>"
            "<ReadContext>Active</ReadContext>"
            + optional_xml("ValueMappingID", value_mapping_id)
            + "</bas:ValueMappingReadRequest>"
        )
        return post_soap("ValueMappingInService/ValueMappingInImplBean", body)
    except Exception as e:
        return f"Error fetching value mapping details: {str(e)}"

@mcp.tool()
def export_value_mappings_csv(value_mapping_id: str = "", group_name: str = "", representation: str = "", scheme_id: str = "", scheme_agency_id: str = "") -> str:
    """
    Exports SAP PI Value Mappings as CSV, with one row per representation.
    """
    try:
        return export_value_mappings_csv_text(
            value_mapping_id=value_mapping_id,
            group_name=group_name,
            representation=representation,
            scheme_id=scheme_id,
            scheme_agency_id=scheme_agency_id,
        )
    except Exception as e:
        return f"Error exporting value mappings CSV: {str(e)}"

@mcp.tool()
def check_value_mapping(value_mapping_id: str) -> str:
    """
    Validates an SAP PI Value Mapping and returns SAP log messages.
    """
    try:
        body = (
            "<bas:ValueMappingCheckRequest>"
            + optional_xml("ValueMappingID", value_mapping_id)
            + "</bas:ValueMappingCheckRequest>"
        )
        return post_soap("ValueMappingInService/ValueMappingInImplBean", body)
    except Exception as e:
        return f"Error checking value mapping: {str(e)}"

@mcp.tool()
def list_configuration_scenarios(configuration_scenario_id: str = "") -> str:
    """
    Lists SAP PI Configuration Scenarios, optionally filtered by scenario ID.
    """
    try:
        response_xml = post_soap(
            "ConfigurationScenarioInService/ConfigurationScenarioInImplBean",
            "<bas:ConfigurationScenarioQueryRequest>{}</bas:ConfigurationScenarioQueryRequest>".format(
                optional_xml("ConfigurationScenarioID", configuration_scenario_id)
            ),
        )
        scenarios = [{"configuration_scenario_id": elem.text or ""} for elem in find_by_local_name(ET.fromstring(response_xml), "ConfigurationScenarioID")]
        return json_result("configuration_scenarios", scenarios)
    except Exception as e:
        return f"Error listing configuration scenarios: {str(e)}"

@mcp.tool()
def get_configuration_scenario_details(configuration_scenario_id: str) -> str:
    """
    Fetches full metadata for an SAP PI Configuration Scenario.
    """
    try:
        body = (
            "<bas:ConfigurationScenarioReadRequest>"
            "<ReadContext>Active</ReadContext>"
            + optional_xml("ConfigurationScenarioID", configuration_scenario_id)
            + "</bas:ConfigurationScenarioReadRequest>"
        )
        return post_soap("ConfigurationScenarioInService/ConfigurationScenarioInImplBean", body)
    except Exception as e:
        return f"Error fetching configuration scenario details: {str(e)}"

@mcp.tool()
def check_configuration_scenario(configuration_scenario_id: str) -> str:
    """
    Validates an SAP PI Configuration Scenario and returns SAP log messages.
    """
    try:
        body = (
            "<bas:ConfigurationScenarioCheckRequest>"
            + optional_xml("ConfigurationScenarioID", configuration_scenario_id)
            + "</bas:ConfigurationScenarioCheckRequest>"
        )
        return post_soap("ConfigurationScenarioInService/ConfigurationScenarioInImplBean", body)
    except Exception as e:
        return f"Error checking configuration scenario: {str(e)}"

register_recovery_tools(mcp, pi_base_url, (PI_USER, PI_PWD), requests)

if __name__ == "__main__":
    mcp.run(transport="stdio")



