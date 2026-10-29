"""Run reports: self-contained HTML with signal plots, and JUnit XML."""

from benchpilot.reporting.html import render_html, write_html
from benchpilot.reporting.junit import junit_xml, write_junit

__all__ = ["junit_xml", "render_html", "write_html", "write_junit"]
