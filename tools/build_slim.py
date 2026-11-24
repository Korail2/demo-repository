#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_slim.py — 목록·분석용 슬림 CSV 만들기

왜 필요한가
    조회 화면과 분석 화면은 민원 본문(질문·답변)을 쓰지 않는다. 그런데
    브라우저는 전체 CSV 를 받아 전 행을 객체로 들고 있었다. 본문이 전체
    글자수의 57% 라서, 쓰지도 않는 값이 메모리의 대부분을 차지했다.

    실측(34,635행 기준): 전체 컬럼 228MB / 슬림 14MB · 로딩 10초 → 6초.
    사무용 PC 에서 느껴지는 차이가 여기서 난다.

    본문은 상세 패널을 열거나, 본문 키워드로 검색하거나, CSV 를 내려받을 때
    비로소 받는다. 그때까지는 받지 않는다.

    (내려받는 용량 자체는 문제가 아니었다. GitHub Pages 가 gzip 으로 보내
     6.3MB CSV 가 실제로는 393KB 다. 병목은 전송이 아니라 메모리였다.)

사용법
    python3 tools/build_slim.py
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "masked_seasonal.csv")
OUT = os.path.join(ROOT, "data", "slim.csv")

# 목록 표와 분석 화면이 실제로 읽는 컬럼만. 하나라도 빠지면 화면이 빈다.
#   접수번호  행 식별 + 상세 패널을 열 때 본문을 맞추는 열쇠
#   등록일    기간 조회·시간대 집계·일별 추이
#   처리기한·완료일  처리상태(완료/미완료/기한초과) 계산
#   제목      목록 표시 + 분석 화면 키워드
KEEP = ["접수번호", "민원유형", "유입경로", "등록일", "처리기한", "완료일",
        "제목", "처리부서", "완료유형"]


def main():
    if not os.path.exists(SRC):
        sys.exit(f"[중단] {SRC} 가 없습니다.")

    # data/ 에는 잔존검사를 통과한 것만 둔다는 규칙을 여기서도 지킨다.
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "pii_mask.py"), "audit", SRC],
                       capture_output=True, text=True, encoding="utf-8")
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.exit("원본이 잔존검사를 통과하지 못했습니다. 슬림 파일을 만들지 않습니다.")

    with open(SRC, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    missing = [c for c in KEEP if c not in rows[0]]
    if missing:
        sys.exit(f"[중단] 원본에 없는 컬럼: {', '.join(missing)}")

    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=KEEP, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    a, b = os.path.getsize(SRC), os.path.getsize(OUT)
    print(f"{OUT} — {len(rows):,}행 · {len(KEEP)}컬럼")
    print(f"  전체 {a:,} → 슬림 {b:,} 바이트 ({b / a * 100:.0f}%)")


if __name__ == "__main__":
    main()
