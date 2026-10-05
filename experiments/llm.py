"""Model backend: `claude -p` in an isolated mode (no tools, no user settings/memory).

Returns the text plus real token usage and cost reported by the CLI.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

SYSTEM = "You write structured JSON for a StoryMap generator. Reply with a single JSON object only."


@dataclass
class Call:
    text: str
    input_tokens: int      # input + cache creation + cache read
    output_tokens: int
    cost_usd: float
    ok: bool = True


def call_claude(prompt: str, system: str = SYSTEM, model: str = "sonnet", timeout: int = 300) -> Call:
    cmd = ["claude", "-p", "--output-format", "json", "--tools", "", "--system-prompt", system,
           "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence", "--model", model]
    for attempt in range(2):
        try:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
            d = json.loads(p.stdout)
            if d.get("is_error"):
                raise RuntimeError(str(d.get("result"))[:200])
            u = d["usage"]
            return Call(d["result"],
                        u["input_tokens"] + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                        u["output_tokens"], float(d.get("total_cost_usd", 0.0)))
        except Exception as e:  # retry once on transient failures
            err = f"{type(e).__name__}: {e}"
    return Call(err, 0, 0, 0.0, ok=False)


def extract_json(text: str) -> dict:
    return json.loads(text[text.index("{"): text.rindex("}") + 1])
