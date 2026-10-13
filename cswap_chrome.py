#!/usr/bin/python3 -I
"""Keep the Chrome profile and the Claude in Chrome extension in step with the cswap account.

  sync      open the Chrome profile mapped to the active Claude account (launchd runs this)
  hook      Claude Code PostToolUse / PostToolUseFailure hook for mcp__claude-in-chrome__*
  prompt    ask whether to restart one session, and restart it on "Restart"
  ensure-auto  start `cswap auto` (consume-first, 98 %) unless one runs (SessionStart hook)
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CLAUDE_JSON = HOME / ".claude.json"
CONFIG = HOME / ".config/cswap-chrome/config.json"
STATE = HOME / ".local/state/cswap-chrome/state.json"
LOG = HOME / ".local/state/cswap-chrome/log.txt"
AUTO_LOG = HOME / ".local/state/cswap-chrome/auto.log"
CSWAP = HOME / ".local/bin/cswap"
AUTO_ARGS = ["auto", "--strategy", "consume-first", "--threshold", "98"]
CHROME_TOOL_PREFIX = "mcp__claude-in-chrome__"
EXTENSION_PAGE = "chrome-extension://fcoeoabgfenejglbffodgkkbkcdhcgfn/sidepanel.html"
NOT_CONNECTED = ("Browser extension is not connected", "No Chrome extension connected")
COOLDOWN_SECONDS = 300
SESSION_ID = re.compile(r"^[0-9a-f-]{36}$")
RESUME_MESSAGE = "Chrome was reconnected after a cswap account switch. Continue the task where you stopped."


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def active_email(claude_json=CLAUDE_JSON):
    return (read_json(claude_json, {}).get("oauthAccount") or {}).get("emailAddress")


def profile_for(config, email):
    return config.get("profiles", {}).get(email)


def osascript(script, *args):
    return subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True)


def notify(message):
    osascript("on run argv\ndisplay notification (item 1 of argv) with title \"cswap-chrome\"\nend run", message)


def open_profile(profile_dir):
    # Loading an extension page restarts the extension after Chrome has stopped it for being idle.
    subprocess.run(["open", "-na", "Google Chrome", "--args", f"--profile-directory={profile_dir}", EXTENSION_PAGE], check=False)


def sync(config, claude_json=CLAUDE_JSON, state_path=STATE):
    email = active_email(claude_json)
    state = read_json(state_path, {})
    if not email or email == state.get("email"):
        return False
    profile = profile_for(config, email)
    if profile is None:
        notify(f"No Chrome profile is set for {email}. Add it to {CONFIG}.")
        log(f"sync: no profile for {email}")
    else:
        open_profile(profile)
        log(f"sync: {state.get('email')} -> {email}, opened profile {profile!r}")
    state["email"] = email
    write_json(state_path, state)
    return profile is not None


def auto_running(process_lines):
    for line in process_lines:
        tokens = line.split()
        for i, token in enumerate(tokens):
            if "/" in token and os.path.basename(token) == "cswap" and tokens[i + 1:i + 2] == ["auto"]:
                return True
    return False


def ensure_auto(process_lines=None):
    if process_lines is None:
        process_lines = subprocess.run(["ps", "-axo", "args="], capture_output=True, text=True).stdout.splitlines()
    if auto_running(process_lines):
        return
    AUTO_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUTO_LOG.open("a") as out:
        subprocess.Popen([str(CSWAP), *AUTO_ARGS], stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    log(f"ensure-auto: started cswap {' '.join(AUTO_ARGS)}")


def is_not_connected(event):
    if not str(event.get("tool_name", "")).startswith(CHROME_TOOL_PREFIX):
        return False
    text = json.dumps([event.get("error"), event.get("tool_response")])
    return any(marker in text for marker in NOT_CONNECTED)


def should_prompt(state, session_id, now):
    return now - state.get("prompted", {}).get(session_id, float("-inf")) >= COOLDOWN_SECONDS


def ps_parent(pid):
    out = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    if not out:
        return None
    ppid, _, args = out.partition(" ")
    return int(ppid), args.split(" ")[0]


def is_claude(executable):
    return os.path.basename(executable) == "claude" or "/claude/versions/" in executable


def claude_ancestor(pid, parent_of=ps_parent):
    while pid and pid > 1:
        entry = parent_of(pid)
        if entry is None:
            return None
        ppid, executable = entry
        if is_claude(executable):
            return pid
        pid = ppid
    return None


def tty_of(pid):
    tty = subprocess.run(["ps", "-o", "tty=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return f"/dev/{tty}" if tty and tty != "??" else None


def hook():
    event = json.load(sys.stdin)
    if not is_not_connected(event):
        return
    session_id = event.get("session_id", "")
    state = read_json(STATE, {})
    now = time.time()
    if not should_prompt(state, session_id, now):
        return
    state.setdefault("prompted", {})[session_id] = now
    write_json(STATE, state)

    pid = claude_ancestor(os.getppid())
    tty = tty_of(pid) if pid else None
    log(f"hook: not connected in session {session_id} pid={pid} tty={tty}")
    config = read_json(CONFIG, {})
    profile = profile_for(config, active_email())
    if not sync(config) and profile:
        open_profile(profile)
    if pid and tty and SESSION_ID.match(session_id):
        with LOG.open("a") as out:
            subprocess.Popen(
                [sys.executable, "-I", os.path.realpath(__file__), "prompt", session_id, str(pid), tty, event.get("cwd", "")],
                stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True,
            )
        note = "A dialog now asks the user whether to restart this session to reconnect Chrome. Stop browser work and tell the user."
    else:
        notify("Claude in Chrome is not connected. Restart the Claude session to reconnect.")
        note = "Claude in Chrome is not connected. Ask the user to restart this session."
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event.get("hook_event_name", "PostToolUse"), "additionalContext": note}}))


DIALOG = """on run argv
set r to display dialog (item 1 of argv) buttons {"Ignore", "Restart"} default button "Restart" with title "cswap-chrome" giving up after 900
return button returned of r
end run"""

ITERM_WRITE = """on run argv
tell application "iTerm2"
repeat with w in windows
repeat with t in tabs of w
repeat with s in sessions of t
if tty of s is (item 1 of argv) then
tell s to write text (item 2 of argv)
return "ok"
end if
end repeat
end repeat
end repeat
end tell
return "missing"
end run"""


def shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def prompt(session_id, pid, tty, cwd):
    pid = int(pid)
    where = os.path.basename(cwd) or cwd or tty
    answer = osascript(DIALOG, f"Claude in Chrome is not connected in the session in {where}. Restart that session?").stdout.strip()
    log(f"prompt: session {session_id} answer={answer!r}")
    if answer != "Restart":
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    else:
        log(f"prompt: pid {pid} did not exit; no resume")
        notify("The Claude session did not stop. Restart it by hand.")
        return
    sync(read_json(CONFIG, {}))
    result = osascript(ITERM_WRITE, tty, f"claude --resume {session_id} {shell_quote(RESUME_MESSAGE)}").stdout.strip()
    log(f"prompt: resumed {session_id} in {tty}: {result}")
    if result != "ok":
        notify(f"Run: claude --resume {session_id}")


def main(argv):
    command = argv[1] if len(argv) > 1 else ""
    if command == "sync":
        sync(read_json(CONFIG, {}))
    elif command == "hook":
        hook()
    elif command == "ensure-auto":
        ensure_auto()
    elif command == "prompt" and len(argv) == 6:
        prompt(*argv[2:6])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
