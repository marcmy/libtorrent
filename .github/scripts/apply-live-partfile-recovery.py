#!/usr/bin/env python3
import subprocess
import textwrap

SOURCE_COMMIT = "077eb0d81378cf71f9f10504e3cc5c812f1a2d65"
WORKFLOW_PATH = ".github/workflows/live-partfile-recovery.yml"

workflow = subprocess.check_output(
    ["git", "show", f"{SOURCE_COMMIT}:{WORKFLOW_PATH}"], text=True
)
marker = "          python3 - <<'PY'\n"
start = workflow.index(marker) + len(marker)
end = workflow.index("          PY\n", start)
script = textwrap.dedent(workflow[start:end])
exec(compile(script, f"{WORKFLOW_PATH}@{SOURCE_COMMIT}", "exec"), {"__name__": "__main__"})
