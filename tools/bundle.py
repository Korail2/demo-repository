#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bundle.py — 사내 로컬망 검토용 오프라인 묶음

무엇을 만드나
    assets/ 와 data/ 를 HTML 안에 전부 집어넣어, 파일만 열면 바로 도는
    형태로 바꾼다. 웹서버도, 파이썬도, 인터넷도 필요 없다.

        offline/index.html      조회·다운로드 + 민원 분석
        offline/forecast.html   민원 예측
        offline/analysis.html   분석으로 보내는 안내 페이지
        offline/사용법.txt

어떻게 하나
    file:// 로 열면 브라우저가 fetch() 로 옆 파일 읽는 것을 막는다(CORS).
    common.js 의 데이터 입구는 readText(path) 하나뿐이므로, 그 함수만
    덮어써서 코드 안에 심어 둔 문자열을 돌려주게 한다. 화면 로직은 한 줄도
    고치지 않는다.

    /api/* 호출은 fetch 를 막아 정적 모드로 고정한다. 서버가 없는 환경에서
    서버 모드를 탐색하느라 콘솔에 오류가 찍히는 것을 막는다.

    구글 폰트 링크는 뺀다. 폐쇄망에서는 어차피 받아오지 못하고 받으려다
    수 초씩 멈춘다. 빼면 맑은 고딕으로 떨어진다 — 글꼴만 조금 다르고
    배치는 같다.

사용법
    python3 tools/bundle.py
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "offline")

# 코드 안에 심을 데이터. 경로는 화면이 readText 에 넘기는 값 그대로여야 한다.
#   slim.csv            부트에서 받는다 (목록·분석)
#   masked_seasonal.csv 상세·본문검색·CSV 생성 때만 쓴다
EMBED = ["data/slim.csv", "data/masked_seasonal.csv", "data/model.json"]


def rd(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8-sig") as f:
        return f.read()


def drop_fonts(html: str) -> str:
    html = re.sub(r'\s*<link rel="preconnect"[^>]*fonts\.gstatic[^>]*>', "", html)
    html = re.sub(r'\s*<link rel="stylesheet" href="https://fonts\.googleapis[^"]*">', "", html)
    return html


def inline_css(html: str) -> str:
    tag = '<link rel="stylesheet" href="assets/console.css">'
    if tag not in html:
        sys.exit("[중단] console.css 를 부르는 태그를 찾지 못했습니다.")
    return html.replace(tag, "<style>\n" + rd("assets", "console.css") + "\n</style>")


def inline_js(html: str, name: str) -> str:
    tag = f'<script src="assets/{name}"></script>'
    if tag not in html:
        sys.exit(f"[중단] {name} 를 부르는 태그를 찾지 못했습니다. HTML 이 바뀐 것 같습니다.")
    body = rd("assets", name).replace("</script>", "<\\/script>")
    return html.replace(tag, "<script>\n" + body + "\n</script>")


def shim(paths) -> str:
    """데이터를 심고, 데이터를 읽는 입구 하나만 덮어쓴다."""
    data = {p: rd(*p.split("/")) for p in paths}
    return ("<script>\n"
            "/* 오프라인 묶음 — 두 가지만 바꾼다.\n"
            "   1) readText 가 파일 대신 아래 문자열을 돌려준다\n"
            "   2) /api/* 를 부르지 않게 해 정적 모드로 고정한다\n"
            "   화면 로직은 그대로다. */\n"
            "const OFFLINE_FILES = " + json.dumps(data, ensure_ascii=False) + ";\n"
            "async function readText(path){\n"
            "  return Object.prototype.hasOwnProperty.call(OFFLINE_FILES, path)\n"
            "    ? OFFLINE_FILES[path] : null;\n"
            "}\n"
            "window.fetch = () => Promise.reject(new Error('offline bundle'));\n"
            "</script>")


def build_index() -> str:
    h = inline_css(drop_fonts(rd("index.html")))
    for js in ("common.js", "suggest.js", "ingest.js", "analysis.js"):
        h = inline_js(h, js)
    mark = "<script>\n'use strict';\nconst DL_SYNC_LIMIT"
    if mark not in h:
        sys.exit("[중단] 화면 스크립트 시작 지점을 찾지 못했습니다.")
    return h.replace(mark, shim(EMBED) + "\n" + mark)


def build_forecast() -> str:
    h = inline_js(inline_css(drop_fonts(rd("forecast.html"))), "common.js")
    i = h.rindex("<script>")
    return h[:i] + shim(["data/model.json"]) + "\n" + h[i:]


GUIDE = """민원 조회 콘솔 — 사내 검토용 오프라인 묶음

■ 여는 법
   index.html 을 더블클릭하면 기본 브라우저에서 열립니다.
   설치할 것도, 인터넷도, 서버도 필요 없습니다.
   권장 브라우저: Chrome 또는 Edge

■ 들어 있는 화면
   조회·다운로드   기간·유형별 조회, 조회된 범위만 CSV 생성
   민원 분석       급증 구간, 대응 방안, 유형 지도, 부서 부하, 키워드
   민원 예측       요일·월·명절 계수 기반 접수량 예측

■ 데이터
   합성 더미입니다. 실제 민원이 아닙니다.
   처리부서는 알려주신 실제 체계를 반영했습니다 —
   영업처 85% / 차량처 10% / 나머지 여섯 부서 합계 5% 안팎이고,
   차량처는 추워질수록 늘어나도록 만들었습니다.

■ 마스킹 표시 — 어디가 원본이 아닌지
   본문의 자물쇠 표시  가린 값입니다 (<이름_1> 같은 토큰)
   머리글의 '가공' 표시  형태만 바꾼 값입니다
      · 접수번호는 원본이 아니라 행 구분용 대체키입니다
      · 등록일은 분·초를 잘라 시(時) 단위로 맞췄습니다
   민원을 하나 클릭하면 맨 위에 "이 건에서 가린 것"이 나옵니다.

■ 이 묶음에서 안 되는 것 — 검토 시 참고
   1) 접속기록이 남지 않습니다.
      화면의 '다운로드 이력'은 서버 기록이 아니라 이 PC 브라우저에만
      남는 값입니다. 다른 PC에서는 보이지 않고 배포한 쪽에서도 볼 수 없습니다.
      「개인정보의 안전성 확보조치 기준」 제8조의 접속기록은 서버 구성에서만
      성립합니다.
   2) 데이터가 파일에 들어 있습니다.
      파일을 복사하면 데이터도 같이 복사됩니다. 지금은 합성 더미라 괜찮지만
      실데이터 운영에는 이 형태를 쓰면 안 됩니다.
   3) 마스킹 전 원본과 대조할 수 없습니다.
      의도한 것입니다. 가명정보와 원문 대응표를 같은 화면에 두면 가명처리가
      성립하지 않습니다. 대조가 필요하시면 권한 있는 담당자가
      tools/mask_diff.py 를 자기 PC에서 돌려 리포트를 받는 방식이 있습니다.
      (그 결과 파일에는 원본이 들어 있으므로 공유하면 안 됩니다.)
   4) 여러 사람이 같은 데이터를 보지 않습니다.
      각자 받은 파일을 각자 엽니다. 갱신하려면 다시 배포해야 합니다.

   1·4번이 필요하면 서버 구성(Flask + PostgreSQL)이 있습니다.
   조회·반출이 DB 접속기록 테이블에 남고 데이터는 서버에만 둡니다.

■ CSV 추가 · 추가분 초기화
   조회 화면 맨 위에서 마스킹된 CSV 를 더할 수 있습니다.
   받기 전에 두 가지를 검사하고, 통과한 것만 넣습니다.
      · 화면이 쓰는 열이 다 있는지
      · 본문에 가려지지 않은 이름·연락처가 남아 있는지
   개인정보가 남아 있으면 받지 않고, 어떤 종류가 몇 행에 있는지 알려줍니다.
   더한 데이터는 이 PC 의 브라우저에만 쌓입니다. 다른 분께는 보이지 않습니다.
   '추가분 초기화'는 더한 것만 지우고 기본 데이터는 그대로 둡니다.

   더블클릭(file://)으로 열면 브라우저가 저장소를 막는 경우가 있습니다.
   그때는 창을 닫으면 추가분이 사라지며, 화면에 그렇게 표시됩니다.

■ 가벼움
   목록과 분석 화면은 민원 본문을 쓰지 않으므로, 처음에는 본문을 뺀
   가벼운 데이터만 읽습니다. 본문은 민원을 클릭하거나, 본문 키워드로
   검색하거나, CSV를 만들 때 비로소 씁니다.

■ 글꼴
   폐쇄망을 가정해 웹폰트를 뺐습니다. 윈도우 기본 글꼴로 표시되며
   배치는 동일합니다.
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    files = [("index.html", build_index()), ("forecast.html", build_forecast())]
    if os.path.exists(os.path.join(ROOT, "analysis.html")):
        files.append(("analysis.html", drop_fonts(rd("analysis.html"))))
    for name, html in files:
        p = os.path.join(OUT, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  {name:<16} {os.path.getsize(p):>10,} 바이트")
    with open(os.path.join(OUT, "사용법.txt"), "w", encoding="utf-8") as f:
        f.write(GUIDE)
    print("  사용법.txt")
    print(f"\n만들었습니다: {OUT}")


if __name__ == "__main__":
    main()
