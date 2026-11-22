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
    def run_fake(self, list_mode: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp = Path(temp_dir.name)
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
                    mode = os.environ["LIST_MODE"]
                    if mode == "valid":
                        print("--selftest-alpha")
                        print("--selftest-beta")
                    elif mode == "invalid":
                        print("startup log noise")
                        print("--selftest-alpha")
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
        env["LIST_MODE"] = list_mode
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
        invocations = invocation_log.read_text(encoding="utf-8").splitlines() if invocation_log.exists() else []
        return result, invocations

    def test_selftest_discovery_uses_the_isolated_profile(self) -> None:
        """A running default-profile app must not force the stale fallback list."""
        result, invocations = self.run_fake("valid")
        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, combined)
        self.assertIn("discovered 2 self-test(s) via --selftest-list", combined)
        self.assertIn("All 2 self-test(s) passed.", combined)
        self.assertEqual(invocations, ["--selftest-alpha", "--selftest-beta"])

    def test_empty_supported_listing_fails_instead_of_using_fallback(self) -> None:
        result, invocations = self.run_fake("empty")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(invocations, [])

    def test_malformed_supported_listing_fails_instead_of_using_fallback(self) -> None:
        result, invocations = self.run_fake("invalid")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(invocations, [])


if __name__ == "__main__":
    unittest.main()
