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
                mock.patch.object(cc, "AUTO_LOG", Path(d, "auto.log")), mock.patch.object(cc, "IS_WINDOWS", False), \
                mock.patch.object(cc.subprocess, "Popen") as po:
            cc.ensure_auto([f"{self.PY} /Users/u/.local/bin/cswap auto"])
            po.assert_not_called()
            cc.ensure_auto([])
        args = po.call_args.args[0]
        self.assertEqual(args[1:], ["auto", "--strategy", "consume-first", "--threshold", "98"])


class OpenProfile(unittest.TestCase):
    def test_opens_profile_on_the_extension_side_panel(self):
        with mock.patch.object(cc, "IS_WINDOWS", False), mock.patch.object(cc.subprocess, "run") as run:
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


class WindowsAutoRunning(unittest.TestCase):
    PY = r'"C:\Users\u\AppData\Roaming\uv\tools\claude-swap\Scripts\python.exe"'

    def test_detects_quoted_cswap_exe_auto(self):
        lines = [self.PY + r' "C:\Users\u\.local\bin\cswap.exe" auto --strategy consume-first --threshold 98']
        self.assertTrue(cc.auto_running(lines))

    def test_detects_unquoted_mixed_separators(self):
        self.assertTrue(cc.auto_running([r"C:\Users\u/.local/bin/cswap.exe auto --threshold 98"]))

    def test_tui_alone_is_not_auto(self):
        self.assertFalse(cc.auto_running([self.PY + r' "C:\Users\u\.local\bin\cswap.exe"']))

    def test_starts_detached_without_a_console(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cc, "LOG", Path(d, "log.txt")), \
                mock.patch.object(cc, "AUTO_LOG", Path(d, "auto.log")), mock.patch.object(cc, "IS_WINDOWS", True), \
                mock.patch.object(cc.subprocess, "Popen") as po:
            cc.ensure_auto([])
        kwargs = po.call_args.kwargs
        self.assertNotIn("start_new_session", kwargs)
        self.assertEqual(kwargs["creationflags"], cc.DETACHED_FLAGS)
        self.assertEqual(po.call_args.args[0][1:], ["auto", "--strategy", "consume-first", "--threshold", "98"])


class WindowsClaude(unittest.TestCase):
    def test_claude_exe_is_claude(self):
        self.assertTrue(cc.is_claude(r"C:\Users\u\.local\bin\claude.exe"))

    def test_other_exe_is_not_claude(self):
        self.assertFalse(cc.is_claude(r"C:\Program Files\Git\usr\bin\bash.exe"))

    def test_walks_up_to_claude_exe(self):
        table = {300: (200, "python.exe"), 200: (100, "claude.exe"), 100: (4, "WindowsTerminal.exe")}
        self.assertEqual(cc.claude_ancestor(300, table.get), 200)


class WindowsOpenProfile(unittest.TestCase):
    def test_runs_chrome_exe_with_profile_and_extension_page(self):
        with mock.patch.object(cc, "IS_WINDOWS", True), mock.patch.object(cc, "chrome_exe", return_value=r"C:\c\chrome.exe"), \
                mock.patch.object(cc.subprocess, "Popen") as po:
            cc.open_profile("Profile 1")
        args = po.call_args.args[0]
        self.assertEqual(args, [r"C:\c\chrome.exe", "--profile-directory=Profile 1", cc.EXTENSION_PAGE])

    def test_finds_chrome_in_the_first_existing_location(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d, "chrome.exe")
            exe.write_text("")
            self.assertEqual(cc.chrome_exe([Path(d, "missing.exe"), exe]), str(exe))

    def test_no_chrome_is_none(self):
        self.assertIsNone(cc.chrome_exe([Path("Z:/nope/chrome.exe")]))


class WindowsPrompt(unittest.TestCase):
    def test_yes_button_is_restart(self):
        self.assertEqual(cc.popup_answer("6"), "Restart")

    def test_no_and_timeout_are_ignore(self):
        for out in ("7", "-1", ""):
            self.assertEqual(cc.popup_answer(out), "Ignore")

    def test_resume_opens_a_terminal_tab_in_the_session_directory(self):
        args = cc.windows_resume_command("abc", r"C:\work\repo")
        self.assertEqual(args, ["wt", "-w", "0", "new-tab", "-d", r"C:\work\repo", "claude", "--resume", "abc", cc.RESUME_MESSAGE])


class WindowsHardening(unittest.TestCase):
    def test_powershell_values_go_through_the_environment(self):
        evil = "a\u2019; Start-Process calc; \u2019b"
        with mock.patch.object(cc.subprocess, "run") as run:
            cc.powershell("$args[0]", evil)
        command = run.call_args.args[0][-1]
        self.assertNotIn("calc", command)
        self.assertIn(evil, run.call_args.kwargs["env"].values())

    def test_reads_utf8_claude_json(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "claude.json")
            p.write_bytes(json.dumps({"x": "\u00c1", "oauthAccount": {"emailAddress": "a@x"}}, ensure_ascii=False).encode("utf-8"))
            self.assertEqual(cc.active_email(p), "a@x")

    def test_resume_cwd_with_semicolon_falls_back_to_home(self):
        args = cc.windows_resume_command("abc", r"C:\a;b")
        self.assertEqual(args[5], str(cc.HOME))

    def test_unreadable_process_list_starts_nothing(self):
        with mock.patch.object(cc, "process_lines", return_value=None), mock.patch.object(cc, "LOG", Path(tempfile.gettempdir(), "cc-test-log.txt")), \
                mock.patch.object(cc.subprocess, "Popen") as po:
            cc.ensure_auto()
        po.assert_not_called()

    def test_parent_cycle_ends(self):
        table = {300: (200, "a.exe"), 200: (300, "b.exe")}
        self.assertIsNone(cc.claude_ancestor(300, table.get))


class Watch(unittest.TestCase):
    def test_sync_error_does_not_stop_the_watch(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "claude.json")
            p.write_text("{}")
            with mock.patch.object(cc, "LOG", Path(d, "log.txt")), mock.patch.object(cc, "sync", side_effect=PermissionError("busy")):
                self.assertIsNotNone(cc.watch_step(p, None))

    def test_syncs_only_when_claude_json_changes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "claude.json")
            p.write_text("{}")
            with mock.patch.object(cc, "sync") as sy:
                seen = cc.watch_step(p, None)
                self.assertEqual(sy.call_count, 1)
                self.assertEqual(cc.watch_step(p, seen), seen)
                self.assertEqual(sy.call_count, 1)

    def test_missing_file_does_not_sync(self):
        with mock.patch.object(cc, "sync") as sy:
            self.assertIsNone(cc.watch_step(Path("Z:/nope/claude.json"), None))
        sy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
