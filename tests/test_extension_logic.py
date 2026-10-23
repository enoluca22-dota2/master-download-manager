"""Browser-extension logic that decides what gets captured, run with Node
(skipped when Node isn't installed). Also checks both builds stay in sync
and only request the permissions they need."""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")

SCRIPT = r"""
const c = require(process.argv[2]);
const cfg = { capture_enabled: true, file_types: ["zip", "iso", "exe", "pdf"], min_size_kb: 100,
              blacklist: ["bank.example", "*.corp.example"] };
const cases = {
  zip_by_name: c.ytdShouldCapture({ url: "https://a.example/x", filename: "C:\\Users\\me\\Downloads\\file.zip", totalBytes: 500000 }, cfg),
  iso_by_url: c.ytdShouldCapture({ url: "https://a.example/dl/os.iso?x=1", filename: "", totalBytes: 0 }, cfg),
  exe_by_mime: c.ytdShouldCapture({ url: "https://a.example/download", filename: "", mime: "application/x-msdownload" }, cfg),
  wrong_type: c.ytdShouldCapture({ url: "https://a.example/a.mp3", filename: "a.mp3", totalBytes: 9e6 }, cfg),
  too_small: c.ytdShouldCapture({ url: "https://a.example/s.zip", filename: "s.zip", totalBytes: 2000 }, cfg),
  blacklisted: c.ytdShouldCapture({ url: "https://www.bank.example/s.zip", filename: "s.zip", totalBytes: 9e6 }, cfg),
  sub_blacklisted: c.ytdShouldCapture({ url: "https://files.corp.example/s.zip", filename: "s.zip" }, cfg),
  referrer_blacklisted: c.ytdShouldCapture({ url: "https://cdn.example/s.zip", referrer: "https://bank.example/", filename: "s.zip" }, cfg),
  blob: c.ytdShouldCapture({ url: "blob:https://a.example/123", filename: "x.zip" }, cfg),
  by_extension: c.ytdShouldCapture({ url: "https://a.example/x.zip", filename: "x.zip", byExtensionId: "abc" }, cfg),
  off: c.ytdShouldCapture({ url: "https://a.example/x.zip", filename: "x.zip" }, { ...cfg, capture_enabled: false }),
  no_cfg: c.ytdShouldCapture({ url: "https://a.example/x.zip", filename: "x.zip" }, null),
};
const out = {};
for (const [k, v] of Object.entries(cases)) out[k] = v.capture;
out.sendable = [c.ytdIsSendableUrl("magnet:?xt=urn:btih:a"), c.ytdIsSendableUrl("javascript:alert(1)"), c.ytdIsSendableUrl("file:///c:/x")];
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="Node.js not installed")
def test_capture_decisions(tmp_path):
    script = tmp_path / "t.js"
    script.write_text(SCRIPT, encoding="utf-8")
    client = os.path.join(ROOT, "extension-shared", "ytd-client.js")
    out = subprocess.run([NODE, str(script), client], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["zip_by_name"] and r["iso_by_url"] and r["exe_by_mime"]
    for k in ("wrong_type", "too_small", "blacklisted", "sub_blacklisted", "referrer_blacklisted", "blob",
              "by_extension", "off", "no_cfg"):
        assert r[k] is False, k
    assert r["sendable"] == [True, False, False]


def test_builds_share_logic_and_minimal_permissions():
    shared = open(os.path.join(ROOT, "extension-shared", "ytd-client.js"), encoding="utf-8").read()
    for browser in ("chrome", "firefox"):
        folder = os.path.join(ROOT, f"extension-{browser}")
        manifest = json.load(open(os.path.join(folder, "manifest.json"), encoding="utf-8"))
        assert set(manifest["permissions"]) == {"storage", "activeTab", "contextMenus", "downloads", "scripting"}
        # the only host the extension may talk to is this PC's local API; never "<all_urls>" or cookies
        assert manifest["host_permissions"] == ["http://127.0.0.1/*"]
        assert "cookies" not in manifest["permissions"] and "webRequest" not in manifest["permissions"]
        bg = open(os.path.join(folder, "background.js"), encoding="utf-8").read()
        assert "downloads.pause" in bg and "downloads.resume" in bg  # pause first, resume on failure
        # build.py syncs the shared client into each build; a stale copy would ship old logic
        copy = open(os.path.join(folder, "ytd-client.js"), encoding="utf-8").read()
        assert copy == shared, f"extension-{browser}/ytd-client.js is out of sync with extension-shared/"
    assert "ytdShouldCapture" in shared
