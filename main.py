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
        # keep_open: this is a 60s poll, so the extension holds one persistent
        # worker tab for this URL and refreshes it, rather than opening and
        # closing a tab every minute. One-shot skills leave keep_open off and
        # get their tab closed when they finish.
        result = browser_run(USAGE_URL, [
            {"type": "customStep", "name": "sleep", "parameters": {"ms": 3000}},
            {"type": "customStep", "name": "read",
             "parameters": {"selector": "body", "extract": "text", "as": "body_text", "timeout_ms": 5000}},
        ], keep_open=True, timeout_ms=75000)
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


class _TaggedStdout:
    """stdout for one resident request: every JSON line main() prints goes out
    carrying the request's _id, which is how the engine matches the answer."""

    def __init__(self, out, rid):
        self.out, self.rid, self.buf = out, rid, ""

    def write(self, s: str) -> int:
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._emit(line)
        return len(s)

    def _emit(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return  # not part of the protocol; never let it reach the engine
        obj["_id"] = self.rid
        self.out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.out.flush()

    def flush(self) -> None:
        if self.buf:
            self._emit(self.buf)
            self.buf = ""


def serve() -> None:
    """Resident mode (MYWANT_MRS_SERVE=1, skill_serve on the want).

    The engine keeps this one interpreter alive and sends a request per tick,
    {"_id": n, "args": [...]} on a line, instead of starting python3 and
    importing mrs_browser every tick. Each request runs main() exactly as a spawn
    would, with the args in sys.argv.
    """
    real_stdout = sys.stdout
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            continue
        rid = req.get("_id")
        sys.argv = [sys.argv[0]] + [str(a) for a in (req.get("args") or [])]
        tagged = _TaggedStdout(real_stdout, rid)
        sys.stdout = tagged
        try:
            main()
        except Exception as e:  # one bad tick must not take the process down
            tagged.flush()
            sys.stdout = real_stdout
            real_stdout.write(json.dumps({"_id": rid, "error": f"{type(e).__name__}: {e}"}) + "\n")
            real_stdout.flush()
            continue
        tagged.flush()
        sys.stdout = real_stdout


if __name__ == "__main__":
    if os.environ.get("MYWANT_MRS_SERVE") == "1":
        serve()
    else:
        main()
