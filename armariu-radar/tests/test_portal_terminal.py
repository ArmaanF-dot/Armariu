"""Terminal mode on the client portal: wired in, shipped with the static export, and read-only."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar.portal import server  # noqa: E402

STATIC = server.STATIC


class TerminalModeTests(unittest.TestCase):
    def test_index_loads_terminal_after_portal(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="terminal.css"', html)
        self.assertLess(html.index('src="portal.js"'), html.index('src="terminal.js"'))
        self.assertIn('id="to-term"', html)
        # The clean view stays the default: terminal only when this browser chose it.
        self.assertIn('getItem("armariu-portal-mode")==="terminal"', html)

    def test_terminal_is_read_only(self):
        js = (STATIC / "terminal.js").read_text(encoding="utf-8")
        for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "method:", "POST"):
            self.assertNotIn(banned, js, banned)
        # The only storage is this browser's own watchlist, filters and mode.
        keys = set(re.findall(r'"(armariu-[a-z-]+(?:-v\d)?)"', js))
        self.assertEqual(keys, {"armariu-portal-mode", "armariu-term-v1", "armariu-watchlist-v1"})

    def test_terminal_drops_individuals(self):
        js = (STATIC / "terminal.js").read_text(encoding="utf-8")
        self.assertIn("private individual", js)
        self.assertIn('class !== "individual"', js)

    def test_export_includes_terminal_files(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(STATIC, tmp / "site")
            for name in ("terminal.js", "terminal.css"):
                self.assertTrue((tmp / "site" / name).is_file(), name)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_scripts_parse(self):
        for name in ("terminal.js", "portal.js", "insights.js"):
            res = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
