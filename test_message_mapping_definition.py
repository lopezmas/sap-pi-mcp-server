import base64
import io
import os
import unittest
import zipfile
from unittest.mock import patch

os.environ.setdefault("SAP_PI_HOST", "example.invalid:50000")
os.environ.setdefault("SAP_PI_USER", "test-user")
os.environ.setdefault("SAP_PI_PASSWORD", "test-password")

import get_pi_channel_status as pi


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for name, content in files.items():
            zip_file.writestr(name, content)
    return buffer.getvalue()


def sap_blob(marker, data):
    return "!{}!{}".format(marker, base64.b64encode(data).decode("ascii"))


QREF_QUERY_XML = """
<queryResult>
  <headerInfo xmlns="">
    <rows count="1" />
    <cols count="4" />
    <colDef>
      <def type="NAME" pos="0" />
      <def type="NAMESPACE" pos="1" />
      <def type="KIND" pos="2" />
      <def type="RA_XILINK" pos="3" />
    </colDef>
  </headerInfo>
  <matrix xmlns="">
    <r>
      <c><simple><strg>MM_TEST</strg></simple></c>
      <c><simple><strg>urn:test</strg></simple></c>
      <c />
      <c>
        <qref isMod="false" isInUnderL="false">
          <ref>
            <vc vcType="S" swcGuid="abc123" sp="-1" caption="TEST_SWC 1.0">
              <clCxt consider="L" />
            </vc>
            <key typeID="XI_TRAFO" oid="oid123">
              <elem>MM_TEST</elem>
              <elem>urn:test</elem>
            </key>
            <vspec type="4" id="vid123" deleted="false" />
          </ref>
        </qref>
      </c>
    </r>
  </matrix>
</queryResult>
"""


DEPENDENCY_XML = """
<queryResult>
  <row>
    <attribute name="NAME" value="MM_TEST"/>
    <attribute name="NAMESPACE" value="urn:test"/>
    <attribute name="RA_LINK_TARGET_REFERENCE" value="MT_SOURCE|urn:test"/>
    <attribute name="RA_LINK_TARGET_TYPE" value="ifmmessage"/>
    <attribute name="RA_LINK_ROLE" value="SOURCE_IFR_MESS"/>
  </row>
  <row>
    <attribute name="NAME" value="MM_TEST"/>
    <attribute name="NAMESPACE" value="urn:test"/>
    <attribute name="RA_LINK_TARGET_REFERENCE" value="FL_TEST|urn:test"/>
    <attribute name="RA_LINK_TARGET_TYPE" value="FUNC_LIB"/>
    <attribute name="RA_LINK_ROLE" value="USED_FUNC_LIB"/>
  </row>
</queryResult>
"""


def xi_trafo_xml(source_text):
    inner_zip = zip_bytes({"metadata": source_text.encode("utf-8")})
    outer_zip = zip_bytes({"value": inner_zip})
    return """
<p1:xiObj xmlns:p1="urn:sap-com:xi" xmlns:tr="urn:sap-com:xi:mapping:xitrafo">
  <p1:idInfo VID="vid123">
    <p1:vc vcType="S" swcGuid="abc123" sp="-1" caption="TEST_SWC 1.0" />
    <p1:key typeID="XI_TRAFO" oid="oid123">
      <p1:elem>MM_TEST</p1:elem>
      <p1:elem>urn:test</p1:elem>
    </p1:key>
  </p1:idInfo>
  <p1:generic>
    <p1:lnks>
      <p1:lnkRole role="SOURCE_IFR_MESS">
        <p1:lnk>
          <p1:key typeID="ifmmessage" oid="source_oid">
            <p1:elem>MT_SOURCE</p1:elem>
            <p1:elem>urn:test</p1:elem>
          </p1:key>
        </p1:lnk>
      </p1:lnkRole>
    </p1:lnks>
  </p1:generic>
  <p1:content>
    <tr:XiTrafo>
      <tr:MetaData>
        <tr:blob type="zip" zipped="true">{}</tr:blob>
      </tr:MetaData>
    </tr:XiTrafo>
  </p1:content>
</p1:xiObj>
""".format(sap_blob("zip", outer_zip))


class MessageMappingDefinitionTests(unittest.TestCase):
    def test_simple_query_qref_rows_include_read_parameters(self):
        rows = pi.parse_simple_query_qref_rows(QREF_QUERY_XML)

        self.assertEqual(len(rows), 1)
        qref = rows[0]["RA_XILINK_REF"]
        self.assertEqual(qref["name"], "MM_TEST")
        self.assertEqual(qref["namespace"], "urn:test")
        self.assertEqual(qref["swc_guid"], "abc123")
        self.assertIn("TYPE=XI_TRAFO", pi.message_mapping_read_path(qref))
        self.assertIn("KEY=MM_TEST%7Curn%3Atest", pi.message_mapping_read_path(qref))

    def test_message_mapping_definition_decodes_nested_zip_metadata(self):
        summary = pi.summarize_message_mapping_definition(
            xi_trafo_xml('<mappingtool><brick type="Func">valueMapping("A")</brick></mappingtool>'),
            include_source=True,
        )

        self.assertEqual(summary["identity"]["name"], "MM_TEST")
        self.assertEqual(summary["links"][0]["name"], "MT_SOURCE")
        self.assertTrue(summary["uses_value_mapping"])
        self.assertEqual(summary["decoded_entries"][0]["path"], "0_zip/value/metadata")
        self.assertIn("valueMapping", summary["decoded_entries"][0]["text"])

    def test_value_mapping_detection_avoids_object_name_only_false_positive(self):
        summary = pi.summarize_message_mapping_definition(
            xi_trafo_xml("<mappingtool><brick path='/ValueMappingReplication/Item'/></mappingtool>"),
            include_source=False,
        )

        self.assertFalse(summary["uses_value_mapping"])

    def test_source_entries_can_filter_metadata(self):
        sources = pi.source_entries_from_xml(
            xi_trafo_xml('<mappingtool><brick type="Dst" path="/Target"/></mappingtool>'),
            source_type="metadata",
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["source_type"], "metadata")
        self.assertIn("/Target", sources[0]["text"])

    def test_message_mapping_dependencies_are_categorized(self):
        rows = pi.parse_simple_query_rows(DEPENDENCY_XML)
        details = pi.message_mapping_dependencies_from_rows(rows, "MM_TEST", "urn:test")

        self.assertEqual(details["count"], 1)
        dependencies = details["message_mappings"][0]["dependencies"]
        self.assertEqual(dependencies[0]["category"], "source")
        self.assertEqual(dependencies[1]["category"], "function_library")

    def test_analyze_message_mapping_combines_definition_and_dependencies(self):
        xml_text = xi_trafo_xml(
            '<mappingtool><brick type="Dst" path="/Target"><arg><brick type="Src" path="/Source"/></arg></brick></mappingtool>'
        )
        dependencies = {
            "count": 1,
            "message_mappings": [
                {
                    "name": "MM_TEST",
                    "namespace": "urn:test",
                    "dependencies": [
                        {
                            "name": "FL_TEST",
                            "namespace": "urn:test",
                            "category": "function_library",
                            "type_code": "FUNC_LIB",
                            "raw_role": "USED_FUNC_LIB",
                        }
                    ],
                }
            ],
        }

        with patch.object(pi, "read_message_mapping_xmls", return_value=[({"read_path": "rep/read/ext?x=1"}, xml_text)]):
            with patch.object(pi, "get_message_mapping_dependency_details", return_value=dependencies):
                analysis = pi.analyze_message_mapping_details("MM_TEST", "urn:test")

        mapping = analysis["message_mappings"][0]
        self.assertTrue(mapping["uses_function_libraries"])
        self.assertEqual(mapping["source_paths"], ["/Source"])
        self.assertEqual(mapping["target_paths"], ["/Target"])


if __name__ == "__main__":
    unittest.main()
