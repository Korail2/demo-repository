#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — 화면 소스를 정적 호스팅용 완전한 HTML 문서로 감싼다

왜 필요한가
    같은 화면을 두 환경에 올린다.
      · claude.ai 아티팩트 — 발행 시 플랫폼이 doctype/charset/viewport/리셋을 붙여준다
      · GitHub Pages     — 아무것도 붙지 않는다. 직접 넣어야 한다

    charset 선언이 없으면 브라우저가 문서를 windows-1252 로 읽어
    JavaScript 안의 한글 컬럼명(r['민원유형'])이 깨지고 표가 통째로 빈다.
    실제로 로컬 테스트에서 재현된 버그이므로 이 단계를 건너뛰면 안 된다.

    또 el.hidden 으로 토글하는 요소가 display:flex/grid 규칙에 덮이지 않도록
    [hidden]{display:none!important} 이 필요하다. (아티팩트 리셋에는 들어있다)

사용법
    python3 tools/build.py src/console.html index.html
"""
from __future__ import annotations

import sys

HEAD = '''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="description" content="열차 민원 마스킹 데이터 조회·다운로드 및 추석 기반 설 연휴 민원 예측 콘솔">
<meta name="robots" content="noindex">
<style>
/* 정적 호스팅용 기본 리셋 — 아티팩트 환경에서는 플랫폼이 넣어주는 부분 */
:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
body{margin:0}
img{max-width:100%}
/* el.hidden 으로 토글하는 요소가 display:flex/grid 규칙에 덮이지 않게 한다 */
[hidden]{display:none!important}
</style>
'''
TAIL = '\n</body>\n</html>\n'


def build(src_path: str, out_path: str) -> None:
    src = open(src_path, encoding="utf-8").read()
    if src.lstrip().lower().startswith("<!doctype"):
        sys.exit(f"{src_path} 는 이미 완전한 문서입니다. 아티팩트용 조각을 입력으로 주세요.")
    i = src.index("</style>") + len("</style>")
    out = HEAD + src[:i] + "\n</head>\n<body>" + src[i:] + TAIL
    open(out_path, "w", encoding="utf-8").write(out)

    need = ["<!doctype html>", '<meta charset="utf-8">', "viewport-fit=cover",
            "[hidden]{display:none!important}", "</body>"]
    missing = [p for p in need if p not in out]
    if missing:
        sys.exit("필수 요소 누락: " + ", ".join(missing))
    print(f"{out_path} — {len(out):,} bytes, 검사 {len(need)}항목 통과")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("사용법: python3 tools/build.py <아티팩트용 조각> <출력 index.html>")
    build(sys.argv[1], sys.argv[2])
