"""JUnit XML results, the format CI systems and test dashboards read.

Deliberately leaves out the `hostname` attribute: results files get shared,
and a machine name can identify a person or a lab.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from benchpilot.testcases.executor import Outcome, TestResult

# XML 1.0 can't carry most control characters, even escaped.
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(text: str) -> str:
    return _INVALID_XML.sub("", text)


def junit_xml(results: list[TestResult], suite_name: str, timestamp: datetime | None = None) -> str:
    failures = sum(r.outcome is Outcome.FAILED for r in results)
    errors = sum(r.outcome is Outcome.ERROR for r in results)
    total_time = f"{sum(r.wall_time_s for r in results):.3f}"
    counts = {"tests": str(len(results)), "failures": str(failures), "errors": str(errors)}

    root = ET.Element("testsuites", {"name": "benchpilot", **counts, "time": total_time})
    suite = ET.SubElement(
        root,
        "testsuite",
        {
            "name": _clean(suite_name),
            **counts,
            "skipped": "0",
            "time": total_time,
            "timestamp": (timestamp or datetime.now()).isoformat(timespec="seconds"),
        },
    )
    for r in results:
        tc = r.testcase
        attrs = {
            "classname": _clean(r.source.stem if r.source else suite_name),
            "name": _clean(f"{tc.id} {tc.title}"),
            "time": f"{r.wall_time_s:.3f}",
        }
        if r.source is not None:
            attrs["file"] = _clean(str(r.source))
        case = ET.SubElement(suite, "testcase", attrs)
        props = ET.SubElement(case, "properties")
        if tc.requirement:
            ET.SubElement(
                props, "property", {"name": "requirement", "value": _clean(tc.requirement)}
            )
        if tc.tags:
            ET.SubElement(props, "property", {"name": "tags", "value": _clean(",".join(tc.tags))})
        ET.SubElement(props, "property", {"name": "bench_time_s", "value": f"{r.bench_time_s:.3f}"})
        if not r.passed:
            tag = "failure" if r.outcome is Outcome.FAILED else "error"
            detail = f"{r.location}: {r.message}" if r.location else r.message
            node = ET.SubElement(case, tag, {"message": _clean(r.message), "type": tag})
            node.text = _clean(detail)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def write_junit(
    results: list[TestResult], path: str | Path, suite_name: str, timestamp: datetime | None = None
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(junit_xml(results, suite_name, timestamp), encoding="utf-8")
    return path
