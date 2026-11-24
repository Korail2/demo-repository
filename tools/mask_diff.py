#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mask_diff.py — 마스킹 전·후 대조 리포트

왜 웹이 아니라 여기인가
    "마스킹된 데이터를 원본과 대조하고 싶다"는 요청은 타당하다. 다만 그것을
    조회 콘솔에 넣으면 안 된다. 원문 대응표는 개인정보보호법이 말하는
    '추가정보'이고, 가명정보와 추가정보를 같은 화면에서 결합하는 순간
    가명처리가 성립하지 않는다. 그 화면은 개인정보처리시스템이 되고
    접근권한·접속기록·암호화 의무가 전부 따라붙는다.

    그래서 대조는 배포물이 아니라 **권한 있는 사람이 자기 PC에서 한 번
    돌려보는 도구**로 둔다. 결과 파일에는 원본이 들어 있으므로 공유하거나
    저장소에 올리면 안 된다. 확인이 끝나면 지운다.

무엇을 보여주나
    1. 요약 — 무엇을 몇 건 가렸는지, 손대지 않은 행은 몇 건인지
    2. 표본 대조 — 원본과 마스킹 결과를 나란히, 바뀐 부분에 표시
    3. 변화 없는 행 — 가려야 할 것을 놓친 건 아닌지 눈으로 확인하는 자리

사용법
    python3 tools/mask_diff.py -r seasonal_raw.csv -m data/masked_seasonal.csv
    python3 tools/mask_diff.py -r 원본.csv -m 마스킹.csv -n 100 --all-unchanged
"""
from __future__ import annotations

import argparse
import csv
import html
import os
import random
import re
import sys
from collections import Counter

BODY = ["제목", "질문", "답변"]
TOKEN = re.compile(r"<([^<>]{1,18}?)_(\d+)>")
LABEL = re.compile(r"\[([가-힣A-Za-z]{2,10})\]")


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def kinds(text: str) -> Counter:
    """이 칸에서 어떤 종류를 몇 개 가렸는지. 같은 번호는 하나로 센다."""
    c = Counter()
    seen = set()
    for k, n in TOKEN.findall(text or ""):
        if (k, n) not in seen:
            seen.add((k, n)); c[k] += 1
    for k in LABEL.findall(text or ""):
        c[k] += 1
    return c


def mark(masked: str) -> str:
    """마스킹 결과에서 토큰·라벨을 눈에 띄게."""
    s = html.escape(masked or "")
    s = re.sub(r"&lt;([^&]{1,18}?_\d+)&gt;", r'<mark>&lt;\1&gt;</mark>', s)
    s = re.sub(r"\[([가-힣A-Za-z]{2,10})\]", r'<mark>[\1]</mark>', s)
    return s


CSS = """
body{font:14px/1.7 "Malgun Gothic",system-ui,sans-serif;margin:0;background:#f4f6f9;color:#1a2430}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 60px}
.warn{background:#fdecec;border:2px solid #d9534f;border-radius:8px;padding:16px 20px;margin-bottom:24px}
.warn b{color:#b32020;font-size:16px}
.warn p{margin:6px 0 0;font-size:13px;color:#6b2020}
h1{font-size:22px;margin:0 0 4px}
.meta{color:#667;font-size:12.5px;margin-bottom:22px}
.card{background:#fff;border:1px solid #dde3ea;border-radius:8px;padding:18px 20px;margin-bottom:18px}
h2{font-size:15px;margin:0 0 12px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid #e6ebf1;padding:7px 9px;text-align:left;vertical-align:top}
th{font-size:11.5px;color:#667;font-weight:600;background:#f7f9fc}
td.n{text-align:right;font-family:Consolas,monospace;white-space:nowrap}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:0;border:1px solid #dde3ea;border-radius:6px;
  overflow:hidden;margin-bottom:10px}
.pair>div{padding:10px 13px;font-size:12.5px;line-height:1.65}
.pair .a{background:#fff8f8;border-right:1px solid #dde3ea}
.pair .b{background:#f7fbf8}
.pair .h{font-size:10.5px;font-weight:700;letter-spacing:.03em;margin-bottom:5px}
.pair .a .h{color:#b32020}
.pair .b .h{color:#14663c}
mark{background:#ffe8b0;color:#7a4a00;padding:0 3px;border-radius:3px;font-weight:600}
.rowhd{font-size:11.5px;color:#667;margin:18px 0 6px;font-family:Consolas,monospace}
.none{color:#8a97a6;font-size:12.5px}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-r", "--raw", required=True, help="마스킹 전 원본 CSV")
    ap.add_argument("-m", "--masked", required=True, help="마스킹 후 CSV")
    ap.add_argument("-o", "--out", default="mask_diff_report.html")
    ap.add_argument("-n", "--sample", type=int, default=40, help="나란히 볼 표본 수 (기본 40)")
    ap.add_argument("--all-unchanged", action="store_true",
                    help="본문이 전혀 바뀌지 않은 행을 전부 싣는다 (기본은 20건)")
    ap.add_argument("--seed", type=int, default=20261124)
    a = ap.parse_args()

    raw, msk = read(a.raw), read(a.masked)
    if len(raw) != len(msk):
        sys.exit(f"[중단] 행 수가 다릅니다 — 원본 {len(raw):,} / 마스킹 {len(msk):,}.\n"
                 "       같은 입력으로 만든 짝이어야 순서대로 맞출 수 있습니다.")

    total = Counter()
    changed, unchanged = [], []
    for i, (r, m) in enumerate(zip(raw, msk)):
        k = Counter()
        for col in BODY:
            k += kinds(m.get(col, ""))
        total += k
        same = all((r.get(c) or "") == (m.get(c) or "") for c in BODY)
        (unchanged if same else changed).append(i)

    rnd = random.Random(a.seed)
    pick = rnd.sample(changed, min(a.sample, len(changed))) if changed else []
    pick.sort()
    show_un = unchanged if a.all_unchanged else unchanged[:20]

    def pair_block(i):
        r, m = raw[i], msk[i]
        out = [f'<div class="rowhd">#{i + 1} · 마스킹 후 접수번호 {html.escape(m.get("접수번호", ""))}'
               f' · {html.escape(m.get("민원유형", ""))} · {html.escape(m.get("처리부서", ""))}</div>']
        for col in BODY:
            o, n = r.get(col) or "", m.get(col) or ""
            if o == n:
                continue
            out.append(f'<div class="pair"><div class="a"><div class="h">{col} — 마스킹 전</div>'
                       f'{html.escape(o)}</div>'
                       f'<div class="b"><div class="h">{col} — 마스킹 후</div>{mark(n)}</div></div>')
        return "".join(out)

    rows = "".join(f"<tr><td>{html.escape(k)}</td><td class='n'>{v:,}</td></tr>"
                   for k, v in total.most_common())

    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>마스킹 전·후 대조</title><style>{CSS}</style></head><body><div class="wrap">
<div class="warn"><b>이 파일에는 마스킹 전 원본이 들어 있습니다</b>
<p>개인정보보호법상 '추가정보'에 해당합니다. 공유·메일 첨부·저장소 커밋을 하지 마시고,
확인이 끝나면 삭제하십시오. 웹 콘솔에는 이 내용이 올라가지 않습니다.</p></div>
<h1>마스킹 전·후 대조</h1>
<div class="meta">원본 {html.escape(os.path.basename(a.raw))} · 마스킹 {html.escape(os.path.basename(a.masked))}
 · 총 {len(raw):,}행 · 생성 {html.escape(a.out)}</div>

<div class="card"><h2>1. 무엇을 가렸나</h2>
<table><tr><th>종류</th><th style="text-align:right">건수</th></tr>{rows or
  '<tr><td colspan="2" class="none">가린 항목이 없습니다.</td></tr>'}</table></div>

<div class="card"><h2>2. 본문이 바뀐 행 — 표본 {len(pick):,}건 / 전체 {len(changed):,}건</h2>
{''.join(pair_block(i) for i in pick) or '<p class="none">바뀐 행이 없습니다.</p>'}</div>

<div class="card"><h2>3. 본문이 전혀 바뀌지 않은 행 — {len(unchanged):,}건</h2>
<p class="meta">가려야 할 것을 놓친 건 아닌지 눈으로 확인하는 자리입니다.
개인정보가 원래 없던 민원이면 정상입니다.{'' if a.all_unchanged else ' 아래는 앞 20건입니다 (--all-unchanged 로 전부).'}</p>
<table><tr><th>#</th><th>제목</th><th>유형</th></tr>
{''.join(f"<tr><td class='n'>{i+1}</td><td>{html.escape(msk[i].get('제목',''))}</td>"
         f"<td>{html.escape(msk[i].get('민원유형',''))}</td></tr>" for i in show_un)
  or '<tr><td colspan="3" class="none">없습니다.</td></tr>'}</table></div>
</div></body></html>"""

    with open(a.out, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"{a.out} — {len(raw):,}행 대조")
    print(f"  본문 변경 {len(changed):,}행 / 변경 없음 {len(unchanged):,}행")
    print(f"  가린 항목 {sum(total.values()):,}개 · 종류 {len(total)}가지")
    print()
    print("  ⚠ 결과 파일에는 원본이 들어 있습니다. 공유하지 말고 확인 후 삭제하세요.")


if __name__ == "__main__":
    main()
