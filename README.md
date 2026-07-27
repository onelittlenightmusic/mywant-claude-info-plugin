# mywant-claude-info-plugin

A MyWant want type that shows Claude.ai's current-session and weekly usage as a
gauge, refreshed every 60 seconds.

The page is read through the MyWant browser extension, which opens
`claude.ai/new#settings/usage` in a background tab of the browser you are already
signed in to — the plugin itself holds no credential of any kind.

## Install

```sh
mywant custom install onelittlenightmusic/mywant-claude-info-plugin
mywant custom install onelittlenightmusic/machine-readable-skills-browser  # shared browser_run() helper
```

Then restart the server so the agent is registered: agents are loaded at startup.

## Requirements

- Python 3.10+ (standard library only)
- The MyWant browser extension, pointed at the server this plugin runs on
- A browser session already signed in to claude.ai

## Files

| File | Purpose |
| :--- | :--- |
| `claude_info.yaml` | want type: state fields, gauge presentation, poll interval |
| `main.py` | reads the usage page via `browser_run()` and extracts the percentages |
