# cswap-chrome

Keeps Claude in Chrome in step with [cswap](https://github.com/realiti4/claude-swap). When cswap
switches the Claude Code account, Chrome opens the profile of that account and wakes its Claude
extension. When a Claude session cannot reach Chrome, a dialog offers to restart that session.

## What it does

| Trigger | Action |
|---|---|
| Any cswap switch (auto, TUI, manual) changes `~/.claude.json` | The launchd agent opens the Chrome profile of the new account on the extension page. The page load wakes an idle extension. |
| A `mcp__claude-in-chrome__*` call returns "Browser extension is not connected" | The hook wakes the extension of the active profile. Then a dialog asks "Restart" or "Ignore". "Restart" stops that session and types `claude --resume <id>` into its iTerm tab. |
| A Claude session starts | If no `cswap auto` process runs, the hook starts `cswap auto --strategy consume-first --threshold 98`. |

The browser bridge shows only the extensions that are signed in to the session's claude.ai
account. So each account needs its own Chrome profile, and the Claude extension in that profile
must be signed in to that account.

## Requirements

- macOS, Google Chrome, iTerm2.
- Or Windows, Google Chrome, Windows Terminal (`wt`), Git Bash and Python 3.12 or later.
- `cswap` in `~/.local/bin`, with all accounts added.
- One Chrome profile for each account, with the Claude extension signed in to that account.

## Install

```sh
git clone git@github.com:ValachPatrik/cswap-chrome.git ~/.local/share/cswap-chrome
~/.local/share/cswap-chrome/install.sh
```

The clone stays the install: the link in `~/.local/bin` and the launchd agent point into it.
To update, run `git pull`.

On Windows, install cswap with `uv tool install claude-swap`, then run in PowerShell:

```powershell
git clone https://github.com/ValachPatrik/cswap-chrome.git $HOME\.local\share\cswap-chrome
& $HOME\.local\share\cswap-chrome\install.ps1
```

The installer writes a sh wrapper to `~/.local/bin/cswap-chrome`, because Claude Code runs
hooks in Git Bash. A Task Scheduler task, `cswap-chrome-watch`, starts at logon. It runs
`cswap_chrome.py watch`, which checks `~/.claude.json` every 2 seconds in place of launchd.
On "Restart", the session opens again in a new Windows Terminal tab, not in its old tab.

Then set the profile folder for each account in `~/.config/cswap-chrome/config.json`. To find
the folder, open `chrome://version` in the profile and read the last part of "Profile Path".
Never guess a folder: Chrome makes a new, empty profile for a folder that does not exist.

```json
{ "profiles": { "you@example.com": "Default", "work@example.com": "Profile 1" } }
```

## Hooks

Add these to `~/.claude/settings.json`, unless the file already has them:

```json
"PostToolUse": [
  { "matcher": "mcp__claude-in-chrome__.*",
    "hooks": [{ "type": "command", "command": "~/.local/bin/cswap-chrome hook", "timeout": 15 }] }
],
"PostToolUseFailure": [
  { "matcher": "mcp__claude-in-chrome__.*",
    "hooks": [{ "type": "command", "command": "~/.local/bin/cswap-chrome hook", "timeout": 15 }] }
],
"SessionStart": [
  { "hooks": [{ "type": "command", "command": "~/.local/bin/cswap-chrome ensure-auto", "timeout": 15 }] }
]
```

## Files

| Path | Content |
|---|---|
| `~/.config/cswap-chrome/config.json` | Account email to Chrome profile folder |
| `~/.local/state/cswap-chrome/log.txt` | Switches, hook events, dialog answers |
| `~/.local/state/cswap-chrome/auto.log` | Output of the `cswap auto` process |
| `~/.local/state/cswap-chrome/state.json` | Last synced account, last dialog time per session |

## Test

```sh
/usr/bin/python3 -m unittest test_cswap_chrome
```

## Limits

- Each wake opens one tab with the extension page.
- The dialog shows once per session in 5 minutes at most.
- Restart works only for sessions in iTerm2. For other terminals, a notification gives the
  `claude --resume` command.
