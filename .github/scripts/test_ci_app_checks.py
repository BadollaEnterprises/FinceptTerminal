#!/usr/bin/env python3
"""Regression tests for the packaged-application CI wrapper."""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CI_APP_CHECKS = ROOT / ".github" / "scripts" / "ci_app_checks.sh"


class CiAppChecksTests(unittest.TestCase):
    def test_selftest_discovery_uses_the_isolated_profile(self) -> None:
        """A running default-profile app must not force the stale fallback list."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_binary = temp / "fake-fincept"
            invocation_log = temp / "invocations.txt"
            fake_binary.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import os
                    import sys

                    args = sys.argv[1:]
                    # Literal retained so ci_app_checks.sh can safely detect support:
                    # --selftest-list
                    if "--selftest-list" in args:
                        # Model the real single-instance collision: the default-profile
                        # secondary exits 0 without reaching self-test dispatch.
                        if "--profile" not in args:
                            raise SystemExit(0)
                        print("--selftest-alpha")
                        print("--selftest-beta")
                        raise SystemExit(0)

                    for arg in args:
                        if arg.startswith("--selftest-"):
                            with open(os.environ["INVOCATION_LOG"], "a", encoding="utf-8") as log:
                                log.write(arg + "\\n")
                            raise SystemExit(0)
                    raise SystemExit(2)
                    """
                ),
                encoding="utf-8",
            )
            fake_binary.chmod(0o755)

            env = os.environ.copy()
            env["INVOCATION_LOG"] = str(invocation_log)
            result = subprocess.run(
                [
                    "bash",
                    str(CI_APP_CHECKS),
                    "selftests",
                    str(fake_binary),
                    "isolated-ci",
                    "5",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )

            combined = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, combined)
            self.assertIn("discovered 2 self-test(s) via --selftest-list", combined)
            self.assertIn("All 2 self-test(s) passed.", combined)
            self.assertEqual(
                invocation_log.read_text(encoding="utf-8").splitlines(),
                ["--selftest-alpha", "--selftest-beta"],
            )


if __name__ == "__main__":
    unittest.main()
