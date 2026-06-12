import json
import os
import unittest

os.environ.setdefault("SAP_PI_HOST", "example.invalid:50000")
os.environ.setdefault("SAP_PI_USER", "test-user")
os.environ.setdefault("SAP_PI_PASSWORD", "test-password")

import get_pi_channel_status as pi


SIMPLE_QUERY_XML = """
<queryResult>
  <row>
    <attribute name="NAME" value="OM_SALES_ORDER"/>
    <attribute name="NAMESPACE" value="urn:test:sales"/>
    <attribute name="SOFTWARE_COMPONENT_VERSION" value="SCV_A"/>
    <attribute name="KIND" value="OPERATION_MAPPING"/>
    <attribute name="RA_LINK_TARGET_REFERENCE" value="MM_SALES_REQUEST|urn:test:sales|SCV_A"/>
    <attribute name="RA_LINK_TARGET_TYPE" value="REQUEST_TRAFO"/>
    <attribute name="RA_LINK_ROLE" value="request"/>
  </row>
  <row>
    <attribute name="NAME" value="OM_SYNC"/>
    <attribute name="NAMESPACE" value="urn:test:sync"/>
    <attribute name="SOFTWARE_COMPONENT_VERSION" value="SCV_B"/>
    <attribute name="KIND" value="OPERATION_MAPPING"/>
    <attribute name="RA_LINK_TARGET_REFERENCE" value="MM_SYNC_REQUEST|urn:test:sync|SCV_B"/>
    <attribute name="RA_LINK_TARGET_REFERENCE" value="MM_SYNC_RESPONSE|urn:test:sync|SCV_B"/>
    <attribute name="RA_LINK_TARGET_TYPE" value="REQUEST_TRAFO"/>
    <attribute name="RA_LINK_TARGET_TYPE" value="RESPONSE_TRAFO"/>
    <attribute name="RA_LINK_ROLE" value="request"/>
    <attribute name="RA_LINK_ROLE" value="response"/>
  </row>
  <row>
    <attribute name="NAME" value="OM_SALES_ORDER"/>
    <attribute name="NAMESPACE" value="urn:test:other"/>
    <attribute name="SOFTWARE_COMPONENT_VERSION" value="SCV_C"/>
    <attribute name="KIND" value="OPERATION_MAPPING"/>
    <attribute name="RA_LINK_TARGET_REFERENCE" value="MM_OTHER_REQUEST|urn:test:other|SCV_C"/>
    <attribute name="RA_LINK_TARGET_TYPE" value="REQUEST_TRAFO"/>
  </row>
</queryResult>
"""


ICO_XML = """
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <IntegratedConfiguration>
      <ReceiverInterfaces>
        <Receiver>
          <ComponentID>BC_RECEIVER_A</ComponentID>
        </Receiver>
        <ReceiverInterface>
          <Name>SI_Receiver_A</Name>
          <Namespace>urn:test:sales</Namespace>
        </ReceiverInterface>
        <ReceiverInterfaceRule>
          <Mapping>
            <Name>OM_SALES_ORDER</Name>
            <Namespace>urn:test:sales</Namespace>
            <SoftwareComponentVersionID>SCV_A</SoftwareComponentVersionID>
          </Mapping>
        </ReceiverInterfaceRule>
      </ReceiverInterfaces>
      <ReceiverInterfaces>
        <Receiver>
          <ComponentID>BC_RECEIVER_B</ComponentID>
        </Receiver>
        <ReceiverInterface>
          <Name>SI_Receiver_B</Name>
          <Namespace>urn:test:sync</Namespace>
        </ReceiverInterface>
        <ReceiverInterfaceRule>
          <Mapping>
            <Name>OM_SYNC</Name>
            <Namespace>urn:test:sync</Namespace>
            <SoftwareComponentVersionID>SCV_B</SoftwareComponentVersionID>
          </Mapping>
        </ReceiverInterfaceRule>
      </ReceiverInterfaces>
    </IntegratedConfiguration>
  </soapenv:Body>
</soapenv:Envelope>
"""


SIMPLE_QUERY_MATRIX_HTML = """
<html>
  <body>
    <textarea name="response" cols="50" rows="10" readonly>&lt;?xml version=&quot;1.0&quot; encoding=&quot;UTF-8&quot;?&gt;
&lt;queryResult&gt;
  &lt;headerInfo xmlns=&quot;&quot;&gt;
    &lt;rows count=&quot;1&quot; /&gt;
    &lt;cols count=&quot;8&quot; /&gt;
    &lt;colDef&gt;
      &lt;def type=&quot;NAME&quot; pos=&quot;0&quot; /&gt;
      &lt;def type=&quot;NAMESPACE&quot; pos=&quot;1&quot; /&gt;
      &lt;def type=&quot;SOFTWARE_COMPONENT_VERSION&quot; pos=&quot;2&quot; /&gt;
      &lt;def type=&quot;SWCV&quot; pos=&quot;3&quot; /&gt;
      &lt;def type=&quot;KIND&quot; pos=&quot;4&quot; /&gt;
      &lt;def type=&quot;RA_LINK_TARGET_REFERENCE&quot; pos=&quot;5&quot; /&gt;
      &lt;def type=&quot;RA_LINK_TARGET_TYPE&quot; pos=&quot;6&quot; /&gt;
      &lt;def type=&quot;RA_LINK_ROLE&quot; pos=&quot;7&quot; /&gt;
    &lt;/colDef&gt;
  &lt;/headerInfo&gt;
    &lt;matrix xmlns=&quot;&quot;&gt;
    &lt;r&gt;
      &lt;c&gt;&lt;simple&gt;&lt;strg&gt;OM_MATRIX&lt;/strg&gt;&lt;/simple&gt;&lt;/c&gt;
      &lt;c&gt;&lt;simple&gt;&lt;strg&gt;urn:test:matrix&lt;/strg&gt;&lt;/simple&gt;&lt;/c&gt;
      &lt;c&gt;&lt;simple&gt;&lt;strg&gt;SCV_M&lt;/strg&gt;&lt;/simple&gt;&lt;/c&gt;
      &lt;c&gt;&lt;simple&gt;&lt;strg&gt;Matrix SWCV&lt;/strg&gt;&lt;/simple&gt;&lt;/c&gt;
      &lt;c&gt;&lt;simple&gt;&lt;strg&gt;OPERATION_MAPPING&lt;/strg&gt;&lt;/simple&gt;&lt;/c&gt;
      &lt;c&gt;&lt;ref&gt;&lt;key typeID=&quot;XI_TRAFO&quot;&gt;&lt;elem&gt;MM_MATRIX_REQUEST&lt;/elem&gt;&lt;elem&gt;urn:test:matrix&lt;/elem&gt;&lt;/key&gt;&lt;/ref&gt;&lt;/c&gt;
      &lt;c&gt;&lt;simple&gt;&lt;strg&gt;XI_TRAFO&lt;/strg&gt;&lt;/simple&gt;&lt;/c&gt;
      &lt;c&gt;&lt;simple&gt;&lt;strg&gt;REQUEST_TRAFO&lt;/strg&gt;&lt;/simple&gt;&lt;/c&gt;
    &lt;/r&gt;
  &lt;/matrix&gt;
&lt;/queryResult&gt;</textarea>
  </body>
</html>
"""


class OperationMappingHelperTests(unittest.TestCase):
    def test_operation_mapping_with_one_request_transformation(self):
        rows = pi.parse_simple_query_rows(SIMPLE_QUERY_XML)
        details = pi.operation_mapping_details_from_rows(rows, "OM_SALES_ORDER", "urn:test:sales")

        self.assertEqual(details["count"], 1)
        transformations = details["operation_mappings"][0]["transformations"]
        self.assertEqual(len(transformations), 1)
        self.assertEqual(transformations[0]["name"], "MM_SALES_REQUEST")
        self.assertEqual(transformations[0]["role"], "request")

    def test_operation_mapping_with_request_and_response_transformations(self):
        rows = pi.parse_simple_query_rows(SIMPLE_QUERY_XML)
        details = pi.operation_mapping_details_from_rows(rows, "OM_SYNC", "urn:test:sync")

        transformations = details["operation_mappings"][0]["transformations"]
        self.assertEqual([item["role"] for item in transformations], ["request", "response"])
        self.assertEqual([item["name"] for item in transformations], ["MM_SYNC_REQUEST", "MM_SYNC_RESPONSE"])

    def test_same_name_can_be_filtered_by_namespace(self):
        rows = pi.parse_simple_query_rows(SIMPLE_QUERY_XML)
        details = pi.operation_mapping_details_from_rows(rows, "OM_SALES_ORDER", "urn:test:other")

        self.assertEqual(details["count"], 1)
        self.assertEqual(details["operation_mappings"][0]["namespace"], "urn:test:other")
        self.assertEqual(details["operation_mappings"][0]["software_component_version"], "SCV_C")

    def test_no_match_returns_empty_result(self):
        rows = pi.parse_simple_query_rows(SIMPLE_QUERY_XML)
        details = pi.operation_mapping_details_from_rows(rows, "OM_DOES_NOT_EXIST")

        self.assertEqual(details, {"count": 0, "operation_mappings": []})

    def test_ico_operation_mapping_extraction(self):
        operation_mappings = pi.operation_mappings_from_ico_xml(ICO_XML)

        self.assertEqual(len(operation_mappings), 2)
        self.assertEqual(operation_mappings[0]["receiver_component"], "BC_RECEIVER_A")
        self.assertEqual(operation_mappings[0]["receiver_interface_name"], "SI_Receiver_A")
        self.assertEqual(operation_mappings[0]["operation_mapping"]["name"], "OM_SALES_ORDER")
        self.assertEqual(operation_mappings[1]["operation_mapping"]["software_component_version"], "SCV_B")

    def test_html_wrapped_simple_query_xml_is_supported(self):
        wrapped = "<html><body><textarea>{}</textarea></body></html>".format(SIMPLE_QUERY_XML)
        rows = pi.parse_simple_query_rows(wrapped)

        self.assertEqual(len(rows), 3)

    def test_html_wrapped_simple_query_matrix_xml_is_supported(self):
        rows = pi.parse_simple_query_rows(SIMPLE_QUERY_MATRIX_HTML)
        details = pi.operation_mapping_details_from_rows(rows, "OM_MATRIX", "urn:test:matrix")

        self.assertEqual(details["count"], 1)
        self.assertEqual(details["operation_mappings"][0]["transformations"][0]["name"], "MM_MATRIX_REQUEST")


if __name__ == "__main__":
    unittest.main()
