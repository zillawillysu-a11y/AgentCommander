import json
import re


def parse_jsonl(path):
    final = None
    usage = {"model": "unavailable", "input_tokens": "unavailable", "output_tokens": "unavailable", "cache_read_tokens": "unavailable", "cache_write_tokens": "unavailable"}
    malformed = 0
    with open(path, encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(event, dict):
                continue
            message = event.get("message", {})
            if isinstance(message, dict):
                if message.get("role") == "assistant":
                    content = message.get("content", [])
                    parts = [x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text"] if isinstance(content, list) else [str(content)]
                    if parts:
                        final = "\n".join(parts)
                if message.get("model"):
                    usage["model"] = message["model"]
                u = message.get("usage", {})
                if event.get("type") == "message_end" and message.get("role") == "assistant" and isinstance(u, dict):
                    for source, dest in (("input", "input_tokens"), ("output", "output_tokens"), ("cacheRead", "cache_read_tokens"), ("cacheWrite", "cache_write_tokens")):
                        if isinstance(u.get(source), int):
                            usage[dest] = (usage[dest] if isinstance(usage[dest], int) else 0) + u[source]
            if event.get("type") == "agent_end" and event.get("messages"):
                pass
    claim = None
    if final:
        match = re.search(r"\{[\s\S]*\}", final)
        if match:
            try:
                claim = json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {"claim": claim, "final_message": final[-4000:] if final else None, "usage": usage, "malformed_lines": malformed}
