"""Timestamp every subtitle event arriving on hayamimi's SSE stream.

Measured against the WebSocket back-channel in ws_ingest.py, this path
delivered 92 events where the WS mirror delivered 15 over the same window,
so mimiwatch takes subtitles from here and uses the WS endpoint only to
push audio in.

    python sse_probe.py --seconds 120 --out measurements/sse_events.json
"""
import argparse
import json
import time
import urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8833/events")
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--out", default="sse_events.json")
    args = ap.parse_args()

    started = time.time()
    events = []
    # The server replays recent `refine` events to every new subscriber, so
    # anything arriving in the first moments is history, not live latency.
    replay_cutoff = 1.0

    with urllib.request.urlopen(args.url, timeout=args.seconds + 30) as r:
        for raw in r:
            t = time.time() - started
            if t >= args.seconds:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            if ev.get("type") == "partial":
                continue
            ev["t"] = round(t, 3)
            ev["replay"] = t < replay_cutoff
            events.append(ev)
            if not ev["replay"]:
                print(f"[{t:7.2f}s] {ev['type']:<11} ({ev.get('lang','')}) "
                      f"{ev.get('text','')[:80]}", flush=True)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=1)
    live = [e for e in events if not e["replay"]]
    print(f"\n{len(events)} events ({len(live)} live) -> {args.out}")


if __name__ == "__main__":
    main()
