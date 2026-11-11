"""The statuses and phases a research job reports are labelled in the browser (frontend/src/researchStatus.js)."""

import os
import re

import research

SOURCE = open(research.__file__, encoding="utf-8").read()
LABELS = open(os.path.join(os.path.dirname(__file__), "..", "frontend", "src", "researchStatus.js"), encoding="utf-8").read()


def server_values(key):
    return set(re.findall(rf'job\["{key}"\]\s*=\s*"(\w+)"', SOURCE)) | set(re.findall(rf'"{key}":\s*"(\w+)"', SOURCE))


def browser_keys(table):
    body = LABELS.split(f"export const {table} = {{", 1)[1].split("};", 1)[0]
    return set(re.findall(r"(\w+):\s*'", body))


def test_every_phase_the_server_reports_has_a_label():
    assert server_values("phase") <= browser_keys("PHASE_LABEL")


def test_every_way_a_job_can_end_has_a_label():
    ended = server_values("status") - {"running"}
    assert ended <= browser_keys("ENDED_LABEL"), sorted(ended - browser_keys("ENDED_LABEL"))
