"""Write codegen-text-baseline.tsv, for when the change to it is meant.

Run inside the test image, where the corpus is mounted and the
interpreter is the one the digests belong to:

    docker compose -f tests/docker/compose.yml run --rm --entrypoint sh \\
        tests -c 'cp -r /repo/... /work && cd /work && \\
        python tests/data/record_codegen_baseline.py' > tests/data/...

The rule for which templates go in and how they are hashed is not
repeated here. It is imported from the test that reads the file back,
so that the two cannot drift apart.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))

import test_codegen_baseline as baseline                       # noqa: E402


def main() -> int:
    rows = baseline._generated()
    if not rows:
        print("no corpus mounted", file=sys.stderr)
        return 1
    out = ["## The Python the generator writes in text mode, frozen.",
           "## Regenerate only when a change to the generated code is",
           "## intended; see tests/unit/test_codegen_baseline.py.",
           "%s%s" % (baseline.VERSION_MARK, platform.python_version())]
    out.extend("%s\t%s" % (case_id, rows[case_id])
               for case_id in sorted(rows))
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
