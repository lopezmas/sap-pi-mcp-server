import csv
import os
import sys
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch

os.environ.setdefault("SAP_PI_HOST", "example.invalid:50000")
os.environ.setdefault("SAP_PI_USER", "test-user")
os.environ.setdefault("SAP_PI_PASSWORD", "test-password")

import export_value_mappings_csv as cli
import get_pi_channel_status as pi


VALUE_MAPPING_READ_XML = """
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:bas="http://sap.com/xi/BASIS">
  <soapenv:Body>
    <bas:ValueMappingReadResponse>
      <bas:ValueMapping>
        <bas:MasterLanguage>EN</bas:MasterLanguage>
        <bas:AdministrativeData>
          <bas:ResponsibleUserAccountID>OWNER</bas:ResponsibleUserAccountID>
          <bas:LastChangeUserAccountID>CHANGER</bas:LastChangeUserAccountID>
          <bas:LastChangeDateTime>2026-05-20T01:02:03Z</bas:LastChangeDateTime>
          <bas:FolderPathID>/vm/folder</bas:FolderPathID>
        </bas:AdministrativeData>
        <bas:Description>Primary description</bas:Description>
        <bas:Description>Second description</bas:Description>
        <bas:ValueMappingID>11111111-1111-1111-1111-111111111111</bas:ValueMappingID>
        <bas:GroupName>Example Codes</bas:GroupName>
        <bas:Representation schemeID="SAP" schemeAgencyID="ERP">1000</bas:Representation>
        <bas:Representation schemeID="LEGACY" schemeAgencyID="CRM">A, quote "value"</bas:Representation>
      </bas:ValueMapping>
    </bas:ValueMappingReadResponse>
  </soapenv:Body>
</soapenv:Envelope>
"""


class ValueMappingExportTests(unittest.TestCase):
    def test_parse_value_mapping_rows_flattens_representations(self):
        rows = pi.parse_value_mapping_rows(VALUE_MAPPING_READ_XML)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["value_mapping_id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(rows[0]["group_name"], "Example Codes")
        self.assertEqual(rows[0]["master_language"], "EN")
        self.assertEqual(rows[0]["description"], "Primary description | Second description")
        self.assertEqual(rows[0]["responsible_user"], "OWNER")
        self.assertEqual(rows[0]["last_change_user"], "CHANGER")
        self.assertEqual(rows[0]["last_change_datetime"], "2026-05-20T01:02:03Z")
        self.assertEqual(rows[0]["folder_path"], "/vm/folder")
        self.assertEqual(rows[0]["representation_value"], "1000")
        self.assertEqual(rows[0]["scheme_id"], "SAP")
        self.assertEqual(rows[0]["scheme_agency_id"], "ERP")
        self.assertEqual(rows[1]["representation_value"], 'A, quote "value"')

    def test_parse_value_mapping_rows_handles_missing_optional_fields(self):
        xml = """
        <ValueMappingReadResponse>
          <ValueMapping>
            <MasterLanguage>EN</MasterLanguage>
            <ValueMappingID>22222222-2222-2222-2222-222222222222</ValueMappingID>
            <Representation schemeID="A" schemeAgencyID="B">C</Representation>
          </ValueMapping>
        </ValueMappingReadResponse>
        """
        rows = pi.parse_value_mapping_rows(xml)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["group_name"], "")
        self.assertEqual(rows[0]["description"], "")
        self.assertEqual(rows[0]["responsible_user"], "")
        self.assertEqual(rows[0]["last_change_user"], "")

    def test_parse_value_mapping_rows_handles_empty_response(self):
        self.assertEqual(pi.parse_value_mapping_rows("<ValueMappingReadResponse/>"), [])
        self.assertEqual(pi.parse_value_mapping_rows(""), [])

    def test_value_mapping_rows_to_csv_has_stable_columns_and_quotes_values(self):
        rows = pi.parse_value_mapping_rows(VALUE_MAPPING_READ_XML)
        csv_text = pi.value_mapping_rows_to_csv(rows)
        parsed_rows = list(csv.DictReader(StringIO(csv_text)))

        self.assertEqual(csv_text.splitlines()[0], ",".join(pi.VALUE_MAPPING_CSV_COLUMNS))
        self.assertEqual(len(parsed_rows), 2)
        self.assertEqual(parsed_rows[1]["representation_value"], 'A, quote "value"')

    def test_export_value_mappings_csv_text_queries_then_reads_ids(self):
        query_xml = """
        <ValueMappingQueryResponse>
          <ValueMappingID>11111111-1111-1111-1111-111111111111</ValueMappingID>
        </ValueMappingQueryResponse>
        """

        with patch.object(pi, "post_soap", side_effect=[query_xml, VALUE_MAPPING_READ_XML]) as post_soap:
            csv_text = pi.export_value_mappings_csv_text(group_name="Example Codes")

        calls = post_soap.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn("ValueMappingQueryRequest", calls[0].args[1])
        self.assertIn("<GroupName>Example Codes</GroupName>", calls[0].args[1])
        self.assertIn("ValueMappingReadRequest", calls[1].args[1])
        self.assertIn("<ReadContext>Active</ReadContext>", calls[1].args[1])
        self.assertIn("Example Codes", csv_text)

    def test_export_value_mappings_csv_tool_returns_csv(self):
        with patch.object(pi, "export_value_mappings_csv_text", return_value="value_mapping_id\nabc\n"):
            self.assertEqual(pi.export_value_mappings_csv(value_mapping_id="abc"), "value_mapping_id\nabc\n")

    def test_cli_writes_csv_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            output_path = temp_file.name
        try:
            with patch.object(cli, "export_value_mappings_csv_text", return_value="value_mapping_id\nabc\n"):
                with patch.object(sys, "argv", ["export_value_mappings_csv.py", output_path, "--group-name", "Example Codes"]):
                    cli.main()

            with open(output_path, "r", encoding="utf-8") as csv_file:
                self.assertEqual(csv_file.read(), "value_mapping_id\nabc\n")
        finally:
            os.remove(output_path)


if __name__ == "__main__":
    unittest.main()
