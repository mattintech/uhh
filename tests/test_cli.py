"""Unit tests for uhh — stdlib unittest only, no third-party deps."""
from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from uhh import cli  # noqa: E402


@contextmanager
def _patched_urlopen(payload: bytes | Exception):
    """Stub urllib.request.urlopen to return `payload` (or raise it)."""
    if isinstance(payload, Exception):
        m = mock.Mock(side_effect=payload)
    else:
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = payload
        m = mock.Mock(return_value=resp)
    with mock.patch("urllib.request.urlopen", m):
        yield m


class GatherContextTests(unittest.TestCase):
    def test_returns_dict_with_core_keys(self):
        facts = cli.gather_context()
        self.assertIsInstance(facts, dict)
        for key in ("hostname", "username", "os", "cwd"):
            self.assertIn(key, facts)
            self.assertTrue(facts[key])


class FormatFactsTests(unittest.TestCase):
    def test_empty_facts_returns_empty_string(self):
        self.assertEqual(cli.format_facts({}), "")

    def test_non_empty_facts_renders_each_line(self):
        out = cli.format_facts({"a": "1", "b": "2"})
        self.assertIn("SYSTEM FACTS", out)
        self.assertIn("- a: 1", out)
        self.assertIn("- b: 2", out)


class ResolveProfileTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "default_profile": "local",
            "profiles": {
                "local": {"host": "http://localhost:11434", "model": "m1"},
                "homelab": {"host": "http://homelab:11434", "model": "m2"},
            },
        }

    def test_default_profile(self):
        self.assertEqual(cli.resolve_profile(self.cfg, None)["model"], "m1")

    def test_named_profile(self):
        self.assertEqual(cli.resolve_profile(self.cfg, "homelab")["model"], "m2")

    def test_missing_profile_exits(self):
        with self.assertRaises(SystemExit):
            cli.resolve_profile(self.cfg, "nope")

    def test_no_profiles_returns_empty(self):
        self.assertEqual(cli.resolve_profile({}, None), {})


class DetectShellTests(unittest.TestCase):
    def test_override_passthrough(self):
        shell, os_name = cli.detect_shell("fish")
        self.assertEqual(shell, "fish")
        self.assertTrue(os_name)

    def test_unix_default_uses_SHELL_env(self):
        if sys.platform == "win32":
            self.skipTest("posix-only path")
        with mock.patch.dict("os.environ", {"SHELL": "/usr/bin/zsh"}, clear=False):
            shell, _ = cli.detect_shell(None)
        self.assertEqual(shell, "zsh")


class AskOllamaTests(unittest.TestCase):
    def test_parses_valid_json_response(self):
        inner = json.dumps({"command": "ls", "explanation": "list", "target_os": "any"})
        outer = json.dumps({"message": {"content": inner}}).encode()
        with _patched_urlopen(outer):
            result = cli.ask_ollama(
                "http://x", "m", "sys", "user", api_key=None, timeout=5
            )
        self.assertEqual(result["command"], "ls")
        self.assertEqual(result["target_os"], "any")

    def test_invalid_json_exits(self):
        outer = json.dumps({"message": {"content": "not json"}}).encode()
        with _patched_urlopen(outer):
            with self.assertRaises(SystemExit):
                cli.ask_ollama("http://x", "m", "s", "u", None, 5)

    def test_unreachable_host_exits(self):
        import urllib.error

        with _patched_urlopen(urllib.error.URLError("nope")):
            with self.assertRaises(SystemExit):
                cli.ask_ollama("http://x", "m", "s", "u", None, 5)


class CrossOsLogicTests(unittest.TestCase):
    """The cross_os check inside main() is a single expression — pin its semantics."""

    @staticmethod
    def is_cross(target_os: str, os_name: str) -> bool:
        return bool(target_os) and target_os.lower() not in (os_name.lower(), "any")

    def test_same_os_not_cross(self):
        self.assertFalse(self.is_cross("macOS", "macOS"))

    def test_any_not_cross(self):
        self.assertFalse(self.is_cross("any", "Linux"))

    def test_different_os_is_cross(self):
        self.assertTrue(self.is_cross("Windows", "Linux"))

    def test_empty_target_not_cross(self):
        self.assertFalse(self.is_cross("", "Linux"))


class WizardPlatformTests(unittest.TestCase):
    def test_rm_cmd_matches_platform(self):
        from uhh import wizard

        expected = "del" if sys.platform == "win32" else "rm"
        self.assertEqual(wizard._RM_CMD, expected)


class ClassifyHostTests(unittest.TestCase):
    def test_localhost_variants(self):
        for h in (
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "http://0.0.0.0:11434",
            "http://[::1]:11434",
        ):
            self.assertEqual(cli.classify_host(h), "localhost / 127.0.0.1", h)

    def test_remote_hosts(self):
        for h in (
            "http://homelab.lan:11434",
            "https://ollama.example.com",
            "http://10.0.0.42:11434",
            "http://my-server",
        ):
            self.assertEqual(cli.classify_host(h), "remote (LAN / VPN / cloud)", h)

    def test_garbage_input_falls_back_to_remote(self):
        self.assertEqual(cli.classify_host(""), "remote (LAN / VPN / cloud)")
        self.assertEqual(cli.classify_host("not a url"), "remote (LAN / VPN / cloud)")


class BuildBugReportUrlTests(unittest.TestCase):
    def test_url_starts_with_issue_endpoint_and_template(self):
        url = cli.build_bug_report_url(
            "0.1.2", "http://localhost:11434", "qwen3:14b", "zsh", "macOS", None
        )
        self.assertTrue(url.startswith(cli.BUG_REPORT_URL + "?"))
        self.assertIn("template=bug_report.yml", url)

    def test_includes_environment_fields(self):
        url = cli.build_bug_report_url(
            "0.1.2", "http://localhost:11434", "qwen3:14b", "zsh", "macOS", None
        )
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(qs["version"], ["uhh 0.1.2"])
        self.assertEqual(qs["shell"], ["zsh"])
        self.assertEqual(qs["model"], ["qwen3:14b"])
        self.assertEqual(qs["host"], ["localhost / 127.0.0.1"])

    def test_remote_host_redacted(self):
        url = cli.build_bug_report_url(
            "0.1.2", "http://homelab.lan:11434", "m", "zsh", "macOS", None
        )
        self.assertIn("homelab", "homelab.lan")  # sanity
        self.assertNotIn("homelab", url)
        self.assertIn("remote", url)

    def test_prompt_included_when_provided(self):
        url = cli.build_bug_report_url(
            "0.1.2", "http://localhost", "m", "zsh", "macOS", "list ports"
        )
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(qs["prompt"], ['uhh "list ports"'])

    def test_prompt_omitted_when_none(self):
        url = cli.build_bug_report_url(
            "0.1.2", "http://localhost", "m", "zsh", "macOS", None
        )
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertNotIn("prompt", qs)


if __name__ == "__main__":
    unittest.main()
