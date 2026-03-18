"""Reads stream-json from stdin, prints formatted output to stdout.

Designed to be used as a pipe: claude ... --output-format stream-json | python -m claude_scheduler.formatter
"""

import json
import sys


def _format_tool(name: str, input_data: dict) -> str:
    if name == "Bash":
        return input_data.get("description", input_data.get("command", ""))
    if name in ("Read", "Glob", "Grep"):
        return input_data.get("file_path") or input_data.get("pattern") or input_data.get("path", "")
    if name in ("Edit", "Write"):
        return input_data.get("file_path", "")
    if name == "Agent":
        return input_data.get("description", "")
    return ""


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(line, flush=True)
            continue

        msg_type = event.get("type", "")

        if msg_type == "system":
            subtype = event.get("subtype", "")
            if subtype == "init":
                model = event.get("model", "unknown")
                print(f"\033[90m[init] model={model}\033[0m", flush=True)
            elif subtype:
                print(f"\033[90m[{subtype}]\033[0m", flush=True)

        elif msg_type == "assistant":
            msg = event.get("message", {})
            if not isinstance(msg, dict):
                continue
            for block in msg.get("content", []):
                if not isinstance(block, dict):
                    continue
                bt = block.get("type", "")
                if bt == "text":
                    text = block.get("text", "")
                    if isinstance(text, str) and text:
                        print(text, end="", flush=True)
                elif bt == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    if not isinstance(inp, dict):
                        inp = {}
                    detail = _format_tool(name, inp)
                    if detail:
                        print(f"\n\033[36m> [{name}] {detail}\033[0m", flush=True)
                    else:
                        print(f"\n\033[36m> [{name}]\033[0m", flush=True)

        elif msg_type == "message":
            # Alternative event shape (direct API messages)
            for block in event.get("content", []):
                if not isinstance(block, dict):
                    continue
                bt = block.get("type", "")
                if bt == "text":
                    text = block.get("text", "")
                    if isinstance(text, str) and text:
                        print(text, end="", flush=True)
                elif bt == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    if not isinstance(inp, dict):
                        inp = {}
                    detail = _format_tool(name, inp)
                    if detail:
                        print(f"\n\033[36m> [{name}] {detail}\033[0m", flush=True)
                    else:
                        print(f"\n\033[36m> [{name}]\033[0m", flush=True)

        elif msg_type == "result":
            result = event.get("result", "")
            cost = event.get("total_cost_usd")
            duration = event.get("duration_ms")
            turns = event.get("num_turns", "?")

            # Print result if we haven't seen assistant text
            if isinstance(result, str) and result:
                # Only print if short (full text was already streamed via assistant events)
                pass

            print()
            cost_str = f"${cost:.4f}" if cost else "?"
            dur_str = f"{duration / 1000:.1f}s" if duration else "?"
            print(f"\033[32m--- done | {turns} turns | {dur_str} | {cost_str} ---\033[0m", flush=True)

        elif msg_type == "error":
            err = event.get("error", {})
            if isinstance(err, dict):
                msg = err.get("message", str(err))
            else:
                msg = str(err)
            print(f"\n\033[31m[ERROR] {msg}\033[0m", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
