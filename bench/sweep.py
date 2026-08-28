"""문맥 줄 수를 바꿔 가며 무엇이 달라지는지 봅니다.

품질을 자동으로 점수 낼 방법이 없으므로 세 가지 대리 지표를 봅니다.

  실패      `looks_broken`에 걸려 번역이 버려진 줄. 오염이 심해지면 늡니다.
  길이폭발  원문의 세 배를 넘는 출력. 모델이 참고 줄까지 옮겼을 때의 모양입니다.
  원문유출  출력에 일본어 가나가 남은 줄. 문맥을 깊게 주면 모델이 옮기는
            대신 원문을 되돌려 줍니다(`って。` -> `って`). 이것이 가장
            나쁜 실패입니다 -- `looks_broken`이 잡지 못합니다. 비어 있지도,
            길지도 않고, `⁇`도 없으니까요.
  변화      ctx0(문맥 없음) 대비 글자까지 달라진 줄. 문맥이 실제로 일한 양입니다.

지표가 판정을 대신하지는 않습니다. 어느 줄이 달라졌는지는 사람이 봅니다.
"""
import json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
NS = [0, 1, 2, 3, 5, 8]
load = lambda n: json.load(open(os.path.join(HERE, f"out_prompt_ctx{n}.json"),
                                encoding="utf-8"))["rows"]
runs = {n: load(n) for n in NS}
base = runs[0]

# 히라가나·가타카나. 한자는 세지 않습니다 -- 한국어 번역에 한자가 남는
# 것은 드물지만 정당할 수 있고, 가나는 그렇지 않습니다.
KANA = [(0x3040, 0x30ff)]


def leaks(row):
    if row["error"] or row["src_lang"] != "ja":
        return False
    return any(lo <= ord(ch) <= hi for ch in row["out"] for lo, hi in KANA)


print("| 문맥 줄 | 실패 | 길이폭발 | 원문유출 | ctx0 대비 변화 | 중앙값(초) |")
print("|---|---|---|---|---|---|")
for n in NS:
    rows = runs[n]
    fails = sum(1 for r in rows if r["error"])
    blown = sum(1 for r in rows if not r["error"] and len(r["text"]) >= 4
                and len(r["out"]) > len(r["text"]) * 3)
    leaked = [r for r in rows if leaks(r)]
    diff = sum(1 for a, b in zip(base, rows) if a["out"] != b["out"])
    secs = sorted(r["seconds"] for r in rows)
    print(f"| {n} | {fails} | {blown} | {len(leaked)} | {diff} | "
          f"{statistics.median(secs):.2f} |")
    for r in leaked:
        print(f"|   ↳ | | | `{r['text']}` → `{r['out']}` | | |")

if len(sys.argv) > 1:                    # 특정 줄이 어떻게 변해 가는지
    key = sys.argv[1]
    idx = [i for i, r in enumerate(base) if r["text"].startswith(key)]
    for i in idx:
        print(f"\n원문: {base[i]['text'][:60]}")
        print(f"  직전: {' / '.join((base[i].get('context') or [])[-8:])[:100]}")
        for n in NS:
            print(f"  ctx{n}: {runs[n][i]['out'] or runs[n][i]['error']}")
