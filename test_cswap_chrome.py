import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cswap_chrome as cc


class ActiveEmail(unittest.TestCase):
    def test_reads_oauth_account_email(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "claude.json")
            p.write_text(json.dumps({"oauthAccount": {"emailAddress": "a@x"}}))
            self.assertEqual(cc.active_email(p), "a@x")

    def test_missing_account_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "claude.json")
            p.write_text("{}")
            self.assertIsNone(cc.active_email(p))


class ProfileFor(unittest.TestCase):
    CONFIG = {"profiles": {"a@x": "Profile 1", "b@x": "Default"}}

    def test_maps_email_to_profile_directory(self):
        self.assertEqual(cc.profile_for(self.CONFIG, "b@x"), "Default")

    def test_unknown_email_is_none(self):
        self.assertIsNone(cc.profile_for(self.CONFIG, "c@x"))


class NotConnected(unittest.TestCase):
    def test_failure_event_with_bridge_error(self):
        event = {
            "tool_name": "mcp__claude-in-chrome__navigate",
            "error": "Browser extension is not connected. Please ensure ...",
        }
        self.assertTrue(cc.is_not_connected(event))

    def test_post_tool_use_result_text(self):
        event = {
            "tool_name": "mcp__claude-in-chrome__tabs_context_mcp",
            "tool_response": [{"type": "text", "text": "No Chrome extension connected after discovery"}],
        }
        self.assertTrue(cc.is_not_connected(event))

    def test_working_call_is_ignored(self):
        event = {"tool_name": "mcp__claude-in-chrome__navigate", "tool_response": "ok"}
        self.assertFalse(cc.is_not_connected(event))

    def test_other_tool_is_ignored(self):
        event = {"tool_name": "Bash", "tool_response": "Browser extension is not connected"}
        self.assertFalse(cc.is_not_connected(event))


class Cooldown(unittest.TestCase):
    def test_first_failure_prompts(self):
        self.assertTrue(cc.should_prompt({}, "s1", now=1000))

    def test_repeat_inside_cooldown_is_silent(self):
        self.assertFalse(cc.should_prompt({"prompted": {"s1": 900}}, "s1", now=1000))

    def test_repeat_after_cooldown_prompts(self):
        self.assertTrue(cc.should_prompt({"prompted": {"s1": 0}}, "s1", now=1000))

    def test_other_session_is_independent(self):
        self.assertTrue(cc.should_prompt({"prompted": {"s1": 900}}, "s2", now=1000))


class Sync(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = Path(self.dir.name)
        self.claude_json = d / "claude.json"
        self.state = d / "state.json"
        self.config = {"profiles": {"a@x": "Profile 1"}}
        log_patch = mock.patch.object(cc, "LOG", d / "log.txt")
        log_patch.start()
        self.addCleanup(log_patch.stop)

    def tearDown(self):
        self.dir.cleanup()

    def _active(self, email):
        self.claude_json.write_text(json.dumps({"oauthAccount": {"emailAddress": email}}))

    def test_switch_opens_the_matching_profile_once(self):
        self._active("a@x")
        with mock.patch.object(cc, "open_profile") as op:
            cc.sync(self.config, self.claude_json, self.state)
            cc.sync(self.config, self.claude_json, self.state)
        op.assert_called_once_with("Profile 1")

    def test_unmapped_account_opens_nothing_and_notifies(self):
        self._active("z@x")
        with mock.patch.object(cc, "open_profile") as op, mock.patch.object(cc, "notify") as nt:
            cc.sync(self.config, self.claude_json, self.state)
        op.assert_not_called()
        nt.assert_called_once()


class AutoRunning(unittest.TestCase):
    PY = "/opt/homebrew/Cellar/python@3.14/3.14.8/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python"

    def test_detects_cswap_auto(self):
        lines = [f"{self.PY} /Users/u/.local/bin/cswap auto --strategy consume-first --threshold 98"]
        self.assertTrue(cc.auto_running(lines))

    def test_tui_alone_is_not_auto(self):
        self.assertFalse(cc.auto_running([f"{self.PY} /Users/u/.local/bin/cswap"]))

    def test_other_auto_process_is_not_cswap(self):
        self.assertFalse(cc.auto_running(["/usr/bin/foo auto", "grep cswap auto"]))

    def test_starts_auto_only_when_missing(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cc, "LOG", Path(d, "log.txt")), \
                mock.patch.object(cc, "AUTO_LOG", Path(d, "auto.log")), mock.patch.object(cc.subprocess, "Popen") as po:
            cc.ensure_auto([f"{self.PY} /Users/u/.local/bin/cswap auto"])
            po.assert_not_called()
            cc.ensure_auto([])
        args = po.call_args.args[0]
        self.assertEqual(args[1:], ["auto", "--strategy", "consume-first", "--threshold", "98"])


class OpenProfile(unittest.TestCase):
    def test_opens_profile_on_the_extension_side_panel(self):
        with mock.patch.object(cc.subprocess, "run") as run:
            cc.open_profile("Profile 1")
        args = run.call_args.args[0]
        self.assertIn("--profile-directory=Profile 1", args)
        self.assertEqual(args[-1], "chrome-extension://fcoeoabgfenejglbffodgkkbkcdhcgfn/sidepanel.html")


class ClaudeAncestor(unittest.TestCase):
    def test_walks_up_to_the_claude_process(self):
        table = {300: (200, "/bin/sh"), 200: (100, "/Users/u/.local/bin/claude"), 100: (1, "-zsh")}
        self.assertEqual(cc.claude_ancestor(300, table.get), 200)

    def test_none_without_claude(self):
        table = {300: (1, "/bin/sh")}
        self.assertIsNone(cc.claude_ancestor(300, table.get))


if __name__ == "__main__":
    unittest.main()
