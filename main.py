#!/usr/bin/env python3
"""
Claude.ai usage monitor via the mywant browser extension.

Opens claude.ai/new#settings/usage in a background tab (no focus stolen —
this polls every 60s, see claude_info.yaml's skill_poll_interval_ms) via
POST /api/v1/web-wants/browser-run, and extracts the current session and
weekly usage percentages from the page text. See
engine/server/handlers_web_wants.go's browserRun and
mcp/playwright-app/webext-src/browser-run-interpreter.ts.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

# mrs_browser ships as the "machine-readable-skills-browser" custom; a checkout
# at ~/work wins when present so a development copy still overrides it.
for _mrs_path in ("~/work/machine-readable-skills-browser",
                  "~/.mywant/custom-types/machine-readable-skills-browser"):
    _mrs_path = os.path.expanduser(_mrs_path)
    if os.path.isdir(_mrs_path):
        sys.path.insert(0, _mrs_path)
        break
from mrs_browser import browser_run  # noqa: E402

USAGE_URL = "https://claude.ai/new#settings/usage"


def progress(pct: int, msg: str = "") -> None:
    print(json.dumps({"_progress": pct, "_message": msg}, ensure_ascii=False), flush=True)


def empty_result(error: str = "") -> dict:
    return {
        "session_usage_pct": 0,
        "session_resets_in": "",
        "weekly_usage_pct": 0,
        "weekly_resets_at": "",
        "achieving_percentage": 0,
        "last_updated": "",
        "last_error": error,
    }


def extract_usage(body_text: str) -> dict:
    """Ports cdp_helper.mjs's extractUsage() line-scan (previously run
    in-page via CDP) to Python, operating on the same document.body.innerText
    the browser extension reads back."""
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]
    session_pct, session_resets_in, weekly_pct, weekly_resets_at = 0, "", 0, ""
    for i, line in enumerate(lines):
        if line == "Current session":
            for j in range(i + 1, min(i + 6, len(lines))):
                rm = re.match(r"Resets in (.+)", lines[j])
                if rm:
                    session_resets_in = rm.group(1)
                pm = re.match(r"^(\d+)%\s*used", lines[j])
                if pm:
                    session_pct = int(pm.group(1))
        if line == "Weekly limits":
            for j in range(i + 1, min(i + 8, len(lines))):
                rm = re.match(r"^Resets (.+)", lines[j])
                if rm:
                    weekly_resets_at = rm.group(1)
                pm = re.match(r"^(\d+)%\s*used", lines[j])
                if pm:
                    weekly_pct = int(pm.group(1))
    return {
        "sessionPct": session_pct,
        "sessionResetsIn": session_resets_in,
        "weeklyPct": weekly_pct,
        "weeklyResetsAt": weekly_resets_at,
    }


def main() -> None:
    now_ms = time.time() * 1000

    progress(20, "opening claude.ai usage page")

    try:
        # timeout_ms must clear the extension's own polling latency (up to
        # 60s — chrome.alarms' minimum period, see background.js's
        # PENDING_ACTION_ALARM) plus the page-load/sleep/read time below, or
        # every call trips the browserRun handler's own timeout and this
        # 60s-interval poller (skill_poll_interval_ms) never gets a result.
        result = browser_run(USAGE_URL, [
            {"type": "customStep", "name": "sleep", "parameters": {"ms": 3000}},
            {"type": "customStep", "name": "read",
             "parameters": {"selector": "body", "extract": "text", "as": "body_text", "timeout_ms": 5000}},
        ], timeout_ms=75000)
    except Exception as e:
        out = {**empty_result(str(e)), "last_poll_at": str(int(now_ms))}
        print(json.dumps(out, ensure_ascii=False), flush=True)
        return

    body_text = result.get("body_text") or ""
    if "Current session" not in body_text:
        out = {**empty_result("使用状況ページの読み込みに失敗しました。ログイン状態を確認してください。"),
               "last_poll_at": str(int(now_ms))}
        print(json.dumps(out, ensure_ascii=False), flush=True)
        return

    data = extract_usage(body_text)

    session_pct = int(data.get("sessionPct", 0))
    session_resets_in = data.get("sessionResetsIn", "")
    weekly_pct = int(data.get("weeklyPct", 0))
    summary = f"Session {session_pct}%"
    if session_resets_in:
        summary += f" · resets in {session_resets_in}"
    summary += f" | Weekly {weekly_pct}%"
    out = {
        "session_usage_pct": session_pct,
        "session_resets_in": session_resets_in,
        "weekly_usage_pct": weekly_pct,
        "weekly_resets_at": data.get("weeklyResetsAt", ""),
        "achieving_percentage": session_pct,
        "summary": summary,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_error": "",
        "last_poll_at": str(int(now_ms)),
    }

    progress(100, f"session {session_pct}% used · resets in {session_resets_in or '?'}")
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
