"""문맥 줄 수를 조건마다 네 번씩 돌린 결과를 모읍니다.

temperature 0.2이므로 한 번의 결과는 표본 하나입니다. `って。`가 5줄에서
번역되지 않고 돌아온 것이 우연인지 성향인지는 반복해야 알 수 있습니다.
"""
import json, os, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
NS = [0, 1, 2, 3, 5, 8]
TAGS = ["", "-r1", "-r2", "-r3"]

def load(n, tag):
    p = os.path.join(HERE, f"out_prompt_ctx{n}{tag}.json")
    return json.load(open(p, encoding="utf-8"))["rows"]

# 한국어 번역에 히라가나·가타카나가 남으면 옮기지 않고 돌려준 것입니다.
def leaked(r):
    return (not r["error"] and r["src_lang"] == "ja"
            and any(0x3040 <= ord(c) <= 0x30ff for c in r["out"]))

print("| 문맥 줄 | 원문유출 (4회 합) | 유출된 줄 | 실패 | 중앙값(초) |")
print("|---|---|---|---|---|")
detail = {}
for n in NS:
    tot, fails, secs, lines = 0, 0, [], {}
    for tag in TAGS:
        rows = load(n, tag)
        for r in rows:
            if leaked(r):
                tot += 1
                lines.setdefault(r["text"], []).append(r["out"])
            if r["error"]:
                fails += 1
        secs += [r["seconds"] for r in rows]
    detail[n] = lines
    names = ", ".join(f"`{t}`" for t in lines) or "-"
    print(f"| {n} | {tot}/{len(TAGS) * len(rows)} | {names} | {fails} | "
          f"{statistics.median(secs):.2f} |")

print("\n유출 내용")
for n in NS:
    for text, outs in detail[n].items():
        print(f"  ctx{n}  {text!r} -> {outs}")
