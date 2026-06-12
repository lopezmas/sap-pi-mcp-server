import argparse

from get_pi_channel_status import export_value_mappings_csv_text


def main():
    parser = argparse.ArgumentParser(description="Export SAP PI/PO Value Mappings to CSV.")
    parser.add_argument("output", help="Path to the CSV file to write.")
    parser.add_argument("--value-mapping-id", default="", help="Optional ValueMappingID filter.")
    parser.add_argument("--group-name", default="", help="Optional GroupName filter.")
    parser.add_argument("--representation", default="", help="Optional Representation value filter.")
    parser.add_argument("--scheme-id", default="", help="Optional Representation schemeID filter.")
    parser.add_argument("--scheme-agency-id", default="", help="Optional Representation schemeAgencyID filter.")
    args = parser.parse_args()

    csv_text = export_value_mappings_csv_text(
        value_mapping_id=args.value_mapping_id,
        group_name=args.group_name,
        representation=args.representation,
        scheme_id=args.scheme_id,
        scheme_agency_id=args.scheme_agency_id,
    )

    with open(args.output, "w", encoding="utf-8", newline="") as csv_file:
        csv_file.write(csv_text)


if __name__ == "__main__":
    main()
