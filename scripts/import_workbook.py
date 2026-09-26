#!/usr/bin/env python3
"""Convert the challenge's projects sheet to portable JSON; stdlib only."""

import argparse
import json
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_rows(path):
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.itertext()) for node in root.findall("x:si", NS)]
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        for row in root.findall(".//x:row", NS):
            cells = {}
            for cell in row.findall("x:c", NS):
                column = re.match(r"[A-Z]+", cell.attrib["r"]).group()
                value = cell.find("x:v", NS)
                inline = cell.find("x:is", NS)
                raw = value.text if value is not None else "".join(inline.itertext()) if inline is not None else ""
                cells[column] = shared[int(raw)] if cell.get("t") == "s" else raw
            yield cells


def excel_date(value):
    if not value:
        return None
    if value.replace(".", "", 1).isdigit():
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date().isoformat()
    return datetime.strptime(value, "%m/%d/%Y").date().isoformat()


def point(lat, lon):
    return {"lat": round(float(lat), 6), "lon": round(float(lon), 6)} if lat and lon else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rows = list(read_rows(args.workbook))
    columns = {name: column for column, name in rows[0].items()}
    projects = []
    for row in rows[1:]:
        get = lambda name: row.get(columns[name], "")
        projects.append({
            "id": get("project_id"),
            "utility": "DESC" if get("project_id").startswith("DESC") else "GPC",
            "state": get("state"),
            "name": get("project_name"),
            "endpoints": [
                {"name": get("name_a"), "point": point(get("lat_a"), get("lon_a"))},
                {"name": get("name_b"), "point": point(get("lat_b"), get("lon_b"))},
            ],
            "center": point(get("lat_center"), get("lon_center")),
            "inServiceDate": excel_date(get("in_service_date")),
            "source": "Projects_Overlaps.xlsx, projects sheet; compiled from supplied public planning PDFs",
            "locationStatus": "Starter coordinates; independently verify before field planning",
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"projects": projects}, indent=2) + "\n")
    print(f"Wrote {len(projects)} projects to {args.output}")


if __name__ == "__main__":
    main()
