"""문맥이 대상 줄을 밀어내지 않았는지 봅니다.

오염은 두 모양으로 나옵니다. 번역이 참고 줄의 내용으로 채워지거나
(`ここまで。` -> "이번에 V스포 보컬 노래 방송으로요."), 아예 실패로
떨어지거나. 둘 다 원문 대비 길이가 크게 튀므로 그 비율로 후보를 찾고
사람이 확인합니다.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
load = lambda m: json.load(open(os.path.join(HERE, f"out_prompt_{m}.json"), encoding="utf-8"))["rows"]

for mode in ["generic", "preset", "generic-ctx", "preset-ctx"]:
    rows = load(mode)
    fails = [r for r in rows if r["error"]]
    # 원문의 세 배를 넘는 출력. 자막 한 줄 번역이 이렇게 길어질 이유는
    # 없습니다 -- 원문이 아주 짧은 경우를 빼려고 하한을 둡니다.
    blown = [r for r in rows
             if not r["error"] and len(r["text"]) >= 4
             and len(r["out"]) > len(r["text"]) * 3]
    print(f"{mode:<12} 실패 {len(fails)}  길이폭발 {len(blown)}")
    for r in fails + blown:
        print(f"    {r['text'][:24]!r} -> {(r['out'] or r['error'])[:60]!r}")
