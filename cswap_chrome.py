#!/usr/bin/python3 -I
"""Keep the Chrome profile and the Claude in Chrome extension in step with the cswap account.

  sync      open the Chrome profile mapped to the active Claude account (launchd runs this)
  watch     run sync each time ~/.claude.json changes (Windows: Task Scheduler runs this at logon)
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

IS_WINDOWS = sys.platform == "win32"
HOME = Path.home()
CLAUDE_JSON = HOME / ".claude.json"
CONFIG = HOME / ".config/cswap-chrome/config.json"
STATE = HOME / ".local/state/cswap-chrome/state.json"
LOG = HOME / ".local/state/cswap-chrome/log.txt"
AUTO_LOG = HOME / ".local/state/cswap-chrome/auto.log"
CSWAP = HOME / (".local/bin/cswap.exe" if IS_WINDOWS else ".local/bin/cswap")
AUTO_ARGS = ["auto", "--strategy", "consume-first", "--threshold", "98"]
CHROME_TOOL_PREFIX = "mcp__claude-in-chrome__"
EXTENSION_PAGE = "chrome-extension://fcoeoabgfenejglbffodgkkbkcdhcgfn/sidepanel.html"
NOT_CONNECTED = ("Browser extension is not connected", "No Chrome extension connected")
COOLDOWN_SECONDS = 300
SESSION_ID = re.compile(r"^[0-9a-f-]{36}$")
RESUME_MESSAGE = "Chrome was reconnected after a cswap account switch. Continue the task where you stopped."
# CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB | CREATE_NO_WINDOW: outlive the hook's job object.
DETACHED_FLAGS = 0x00000200 | 0x01000000 | 0x08000000
WATCH_SECONDS = 2


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


def powershell(script, *args):
    # The script reads its arguments from $args, so no value is spliced into the script text.
    command = f"& {{ {script} }} " + " ".join("'" + a.replace("'", "''") + "'" for a in args)
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                          capture_output=True, text=True, creationflags=0x08000000)


WIN_NOTIFY = """Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.ShowBalloonTip(10000, 'cswap-chrome', $args[0], 'Info')
Start-Sleep -Seconds 10
$n.Dispose()"""


def notify(message):
    if IS_WINDOWS:
        spawn_detached(["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                        f"& {{ {WIN_NOTIFY} }} '" + message.replace("'", "''") + "'"], subprocess.DEVNULL)
        return
    osascript("on run argv\ndisplay notification (item 1 of argv) with title \"cswap-chrome\"\nend run", message)


def chrome_exe(candidates=None):
    if candidates is None:
        candidates = [Path(os.environ.get(var, ""), "Google/Chrome/Application/chrome.exe")
                      for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if os.environ.get(var)]
    return next((str(p) for p in candidates if p.is_file()), None)


def spawn_detached(args, out):
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": out}
    if not IS_WINDOWS:
        return subprocess.Popen(args, start_new_session=True, **kwargs)
    try:
        return subprocess.Popen(args, creationflags=DETACHED_FLAGS, **kwargs)
    except OSError:
        # The job object of the caller does not allow breakaway.
        return subprocess.Popen(args, creationflags=DETACHED_FLAGS & ~0x01000000, **kwargs)


def open_profile(profile_dir):
    # Loading an extension page restarts the extension after Chrome has stopped it for being idle.
    if IS_WINDOWS:
        exe = chrome_exe()
        if exe is None:
            log("open_profile: chrome.exe not found")
            return
        subprocess.Popen([exe, f"--profile-directory={profile_dir}", EXTENSION_PAGE],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
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


def watch_step(claude_json, seen):
    try:
        mtime = claude_json.stat().st_mtime_ns
    except OSError:
        return seen
    if mtime != seen:
        sync(read_json(CONFIG, {}), claude_json)
    return mtime


def watch():
    seen = None
    while True:
        seen = watch_step(CLAUDE_JSON, seen)
        time.sleep(WATCH_SECONDS)


def basename(path):
    return re.split(r"[\\/]", path)[-1]


def auto_running(process_lines):
    for line in process_lines:
        tokens = [quoted or bare for quoted, bare in re.findall(r'"([^"]*)"|(\S+)', line)]
        for i, token in enumerate(tokens):
            if re.search(r"[\\/]", token) and basename(token) in ("cswap", "cswap.exe") and tokens[i + 1:i + 2] == ["auto"]:
                return True
    return False


WIN_COMMAND_LINES = "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"


def process_lines():
    if IS_WINDOWS:
        return powershell(WIN_COMMAND_LINES).stdout.splitlines()
    return subprocess.run(["ps", "-axo", "args="], capture_output=True, text=True).stdout.splitlines()


def ensure_auto(lines=None):
    if lines is None:
        lines = process_lines()
    if auto_running(lines):
        return
    AUTO_LOG.parent.mkdir(parents=True, exist_ok=True)
    with AUTO_LOG.open("a") as out:
        spawn_detached([str(CSWAP), *AUTO_ARGS], out)
    log(f"ensure-auto: started cswap {' '.join(AUTO_ARGS)}")


def is_not_connected(event):
    if not str(event.get("tool_name", "")).startswith(CHROME_TOOL_PREFIX):
        return False
    text = json.dumps([event.get("error"), event.get("tool_response")])
    return any(marker in text for marker in NOT_CONNECTED)


def should_prompt(state, session_id, now):
    return now - state.get("prompted", {}).get(session_id, float("-inf")) >= COOLDOWN_SECONDS


def win_process_table():
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    table = {}
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
    while ok:
        table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
        ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    kernel32.CloseHandle(snapshot)
    return table


def win_alive(pid):
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def alive(pid):
    if IS_WINDOWS:
        return win_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def ps_parent(pid):
    if IS_WINDOWS:
        return win_process_table().get(pid)
    out = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    if not out:
        return None
    ppid, _, args = out.partition(" ")
    return int(ppid), args.split(" ")[0]


def is_claude(executable):
    return basename(executable).lower() in ("claude", "claude.exe") or "/claude/versions/" in executable


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
    if IS_WINDOWS:
        return "console"
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
            spawn_detached(
                [sys.executable, "-I", os.path.realpath(__file__), "prompt", session_id, str(pid), tty, event.get("cwd", "")], out)
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


WIN_POPUP = "(New-Object -ComObject WScript.Shell).Popup($args[0], 900, 'cswap-chrome', 4 + 48)"


def popup_answer(stdout):
    return "Restart" if stdout.strip() == "6" else "Ignore"


def windows_resume_command(session_id, cwd):
    return ["wt", "-w", "0", "new-tab", "-d", cwd, "claude", "--resume", session_id, RESUME_MESSAGE]


def shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def prompt(session_id, pid, tty, cwd):
    pid = int(pid)
    where = os.path.basename(cwd) or cwd or tty
    question = f"Claude in Chrome is not connected in the session in {where}. Restart that session?"
    if IS_WINDOWS:
        answer = popup_answer(powershell(WIN_POPUP, question).stdout)
    else:
        answer = osascript(DIALOG, question).stdout.strip()
    log(f"prompt: session {session_id} answer={answer!r}")
    if answer != "Restart":
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    for _ in range(50):
        if not alive(pid):
            break
        time.sleep(0.2)
    else:
        log(f"prompt: pid {pid} did not exit; no resume")
        notify("The Claude session did not stop. Restart it by hand.")
        return
    sync(read_json(CONFIG, {}))
    if IS_WINDOWS:
        try:
            subprocess.Popen(windows_resume_command(session_id, cwd or str(HOME)))
            result = "ok"
        except OSError as e:
            result = f"wt failed: {e}"
    else:
        result = osascript(ITERM_WRITE, tty, f"claude --resume {session_id} {shell_quote(RESUME_MESSAGE)}").stdout.strip()
    log(f"prompt: resumed {session_id} in {tty}: {result}")
    if result != "ok":
        notify(f"Run: claude --resume {session_id}")


def main(argv):
    command = argv[1] if len(argv) > 1 else ""
    if command == "sync":
        sync(read_json(CONFIG, {}))
    elif command == "watch":
        watch()
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
