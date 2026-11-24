#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
embed_data.py — data/ 파일을 <script> 로 읽을 수 있는 .js 로 감싼다

왜 필요한가
    index.html 을 더블클릭해 file:// 로 열면 브라우저가 fetch() 로 옆 파일을
    읽는 것을 막는다. 그래서 데이터가 비어 보인다. <script src> 는 막지 않으므로
    같은 내용을 .js 로 한 벌 더 둔다.

    common.js 의 loadCSV / loadJSON 은 먼저 fetch() 로 읽고, 실패했을 때만
    이 .js 를 불러온다. 서버·GitHub Pages 에서는 .js 를 받지 않는다.

개인정보
    data/ 에는 잔존검사 0건인 파일만 둔다는 규칙을 여기서도 지킨다.
    CSV 는 감싸기 전에 pii_mask.py audit 를 돌리고, 1건이라도 나오면 멈춘다.

사용법
    python3 tools/embed_data.py                      # 기본: 마스킹 CSV + 예측 모델
    python3 tools/embed_data.py data/<파일>.csv ...  # 지정한 파일만
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = ["data/masked_seasonal.csv", "data/model.json"]


def audit(rel: str) -> None:
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "pii_mask.py"), "audit",
                        os.path.join(ROOT, rel)], capture_output=True, text=True, encoding="utf-8")
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        sys.exit(f"{rel}: 잔존검사를 통과하지 못해 감싸지 않았습니다.")


def embed(rel: str) -> None:
    rel = rel.replace("\\", "/")
    if not rel.startswith("data/"):
        sys.exit(f"{rel}: data/ 아래 파일만 감쌉니다.")
    src = os.path.join(ROOT, rel)
    if rel.endswith(".csv"):
        audit(rel)
    text = open(src, encoding="utf-8-sig").read()
    if rel.endswith(".json"):
        json.loads(text)                      # 깨진 JSON 을 감싸 두지 않는다
    out = os.path.splitext(src)[0] + ".js"
    body = ("/* tools/embed_data.py 가 " + rel + " 에서 만든 파일 — 직접 고치지 말 것.\n"
            "   file:// 로 열었을 때만 쓰인다 (common.js loadCSV/loadJSON). */\n"
            "window.__EMBED = window.__EMBED || {};\n"
            f"window.__EMBED[{json.dumps(rel)}] = {json.dumps(text, ensure_ascii=False)};\n")
    open(out, "w", encoding="utf-8", newline="\n").write(body)
    print(f"{os.path.relpath(out, ROOT)} — {len(body.encode('utf-8')):,} bytes")


if __name__ == "__main__":
    for f in (sys.argv[1:] or DEFAULT):
        embed(f)
