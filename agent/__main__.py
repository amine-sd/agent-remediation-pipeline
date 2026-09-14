"""Run the agent once on the current state of the pipeline and print its verdict.

    python -m agent

The whole transcript (alert, every model turn, tool call and answer) is saved in logs/agent/.
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

from agent.loop import MODEL, OPTIONS, alert, run_agent
from agent.tools import read_logs

TRANSCRIPTS = Path("logs/agent")


def main() -> int:
    view = read_logs(runs=1)["runs"][0]
    text = alert(view)
    print(text + "\n", flush=True)

    started = time.monotonic()
    result = run_agent(text, log=lambda line: print(line, flush=True))
    elapsed = round(time.monotonic() - started)

    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    path = TRANSCRIPTS / f"{datetime.now():%Y%m%dT%H%M%S}-{view['data_date']}.json"
    path.write_text(json.dumps({"model": MODEL, "options": OPTIONS, "alert": text,
                                "seconds": elapsed, **result}, ensure_ascii=False, indent=2),
                    encoding="utf-8")

    usage = result["usage"]
    print(f"\nstopped: {result['stopped']} | model calls: {usage['model_calls']} | "
          f"tool calls: {len(result['tool_calls'])} | tokens in/out: "
          f"{usage['prompt_tokens']}/{usage['output_tokens']} | {elapsed} s")
    print(f"last prompt: about {result['prompt_chars'] // 4} tokens for a context of "
          f"{OPTIONS['num_ctx']} (rough estimate, 4 characters per token)")
    if result["notes"]:
        print("\n=== notes at the end of the investigation ===\n" + result["notes"])
    if result["verdict"]:
        print("\n=== verdict ===\n" + json.dumps(result["verdict"], ensure_ascii=False, indent=2))
    else:
        print(f"\n=== no verdict ({result['stopped']}) ===\n{result['error'] or ''}")
        if result["raw_verdict"]:
            print("rejected answer: " + result["raw_verdict"])
    print(f"\ntranscript: {path.as_posix()}")
    return 0


sys.exit(main())
