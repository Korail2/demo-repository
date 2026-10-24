#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pii_mask.py — 열차 민원 개인정보 마스킹 올인원 (단일 파일, 외부 의존성 없음)

  python3 pii_mask.py dummy   -n 50 -o dummy.csv          더미데이터 생성
  python3 pii_mask.py mask    -i dummy.csv -o masked.csv  테이블 마스킹
  python3 pii_mask.py text    "민원 본문"                  본문 한 줄 마스킹
  python3 pii_mask.py audit   masked.csv                  마스킹 결과 잔존 검사
  python3 pii_mask.py selftest                            내장 검증

설계 기준
  1. SHIELD 선점   보존할 운영정보(열차번호·역명·금액·대표번호)를 규칙보다 먼저 확정
  2. FAIL-SAFE     SHIELD 에 안 걸린 7자리 이상 숫자는 전부 마스킹
  3. 우선순위 해소 같은 글자를 두 규칙이 다르게 보면 숫자가 큰 쪽이 이김
  4. 운영 게이트   잔존검사 실패 시 QUARANTINE → 사람 검토
                   "완벽" = 유출 0 이 아니라 '무검토 유출 0'
  5. 행 일관성     제목·질문·답변에서 같은 사람에게 같은 토큰 부여

컬럼 정책은 POLICY 표, 보존 번호는 OFFICIAL_NUMBERS, 이름 부정사전은 NAME_STOP.
이 3개가 운영자가 관리하는 설정이다.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

# ======================================================================
# 운영자가 관리하는 입력 — 이 두 개는 코드가 아니라 '설정'이다
# ======================================================================

# 공식 대표번호 화이트리스트. 여기에 없는 전화번호는 전부 개인 연락처로 간주한다.
# (직원 '직통' 번호는 개인에 귀속되므로 절대 넣지 않는다)
OFFICIAL_NUMBERS: List[str] = [
    "1544-7788",     # 코레일 고객센터
    "1599-7777",     # SR 고객센터
    "1544-8545",
    "02-6484-0000",  # 광명역 주차관리실
]

# 역명 사전. 패턴(○○역)으로도 잡지만, 사전이 있으면 오탐이 줄어든다.
STATIONS: List[str] = [
    "서울", "용산", "청량리", "영등포", "광명", "수원", "천안아산", "오송", "대전",
    "김천구미", "동대구", "경주", "울산", "부산", "밀양", "구포", "포항", "익산",
    "정읍", "광주송정", "목포", "여수엑스포", "순천", "전주", "강릉", "평창", "원주",
    "춘천", "제천", "안동", "마산", "진주", "창원중앙", "수서", "동탄", "지제",
]

# ======================================================================
# Layer 0. 정규화 — 길이를 바꾸지 않으므로 원문 오프셋이 그대로 유효하다
# ======================================================================

_FULLWIDTH_DIGIT = {chr(0xFF10 + i): str(i) for i in range(10)}
_ZEROWIDTH = ["​", "‌", "‍", "﻿"]
_CONF_MAP = {"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"}
# 전화번호 '모양'의 덩어리 안에서만 혼동문자를 숫자로 바꾼다.
# 양쪽에 알파벳이 붙어 있으면 영단어의 일부다.
# (경계 검사가 없으면 'call 1599-7777' 의 'll' 을 '11' 로 바꿔 영문을 망가뜨린다)
_PHONE_SHAPED = re.compile(
    r"(?<![A-Za-z])[0-9OolI|]{2,4}[-.·\s]?[0-9OolI|]{3,4}[-.·\s]?[0-9OolI|]{4}(?![A-Za-z])")


def normalize(text: str) -> str:
    for fw, hw in _FULLWIDTH_DIGIT.items():
        text = text.replace(fw, hw)
    for zw in _ZEROWIDTH:
        text = text.replace(zw, " ")

    def _fix(m: "re.Match[str]") -> str:
        chunk = m.group()
        if sum(c.isdigit() for c in chunk) < 6:   # 숫자가 과반이어야 전화번호로 본다
            return chunk
        return "".join(_CONF_MAP.get(c, c) for c in chunk)

    return _PHONE_SHAPED.sub(_fix, text)


# ======================================================================
# 사전
# ======================================================================

TRAINS = r"(?:KTX-이음|KTX-산천|KTX|SRT|ITX-새마을|ITX-청춘|ITX-마음|무궁화호|새마을호|누리로)"

# '○○역' 으로 끝나지만 역이 아닌 단어
NOT_STATION = {"지역", "영역", "구역", "권역", "유역", "병역", "음역", "번역", "통역",
               "면역", "반역", "역역", "이역", "수역", "성역", "전역"}

# 이름 자리에 올 수 없는 고빈도 어절 (이름 규칙 공통 필터)
NAME_STOP = {
    "고객", "담당", "직원", "기사", "역장", "과장", "부장", "차장", "대리", "주임",
    "성함", "이름", "성명", "본인", "저희", "귀하", "사장", "교사", "학생", "보호",
    "예금", "수취", "신청", "민원", "주문", "인솔", "확인", "접수", "문의", "답변",
    "안내", "환불", "교환", "취소", "변경", "처리", "조치", "발권", "예약", "좌석",
    "열차", "승차", "운임", "요금", "정상", "지연", "분실", "유실", "주차", "차량",
    "회원", "포인트", "마일", "할인", "쿠폰", "결제", "카드", "계좌", "입금", "송금",
    "이번", "저번", "지난", "다음", "오늘", "어제", "내일", "최근", "현재", "당일",
    "제품", "서비", "시설", "화장", "청소", "점검", "운영", "관리", "시스템", "장애",
    "오류", "모바", "모델", "번호", "정보", "내용", "사항", "경우", "기준", "규정",
    "약관", "대상", "가능", "불가", "제한", "이상", "미만", "초과", "이내", "이후",
    "전액", "차액", "금액", "일부", "전부", "모두", "각각", "해당", "관련", "이용",
    "탑승", "하차", "출발", "도착", "경유", "환승", "매표", "창구", "전광", "안심",
    "단체", "법인", "정기", "입석", "자유", "유아", "아동", "어린", "학년", "학교",
    "무엇", "뭐가", "제대", "조회", "추적", "기재", "설명", "사용", "등록", "수정",
    # ↓ 실데이터 오탐을 보고 계속 늘려야 하는 영역. 규칙 기반 운영 비용의 본체다.
    "연락처", "직통", "백팩", "문자", "신분증", "주소", "주말", "방지", "주차권",
    "이메일", "배정", "신청서", "기간", "연결", "전광판", "주기", "기존", "기록",
    "유실물", "교육", "의견", "반영", "복원", "소멸", "유효", "만료", "출발", "경과",
    "편성", "운송", "약관", "조건", "변동", "지급", "보상", "규정", "확보", "내선",
    "가입", "주차장", "명찰", "창구", "매표소", "시간표", "홈페이지", "업데이트",
    "계정", "로그인", "결제", "승인", "청구", "자동", "취소분", "중복", "회신", "요청",
}

# 어미·조사 파편 — 이름 2~3음절에 섞여 들어오면 이름이 아니다
NON_NAME_SYL = set("것거때쪽런떤렇럼죠요됨된될됩큼곳며했었으회적델받번야음함되")

BRAND_CAPS = {"KTX", "SRT", "ITX", "SMS", "QR", "VIP", "PDF", "URL", "APP", "ATM"}

# 근접확산에서 '이 뒤에 붙은 한글 어절은 사람 이름' 이라고 볼 수 있는 식별자
CONTACT_LABELS = {"휴대전화", "유선전화", "안심번호", "이메일", "사번", "한글전화"}

TITLES = (r"고객님|귀하|사모님|선생님|기사님|역장님|사장님|과장님|부장님|차장님|대리님|"
          r"팀장님|실장님|대표님|회원님|주임님|매니저님|님|씨|군|양|과장|부장|차장|대리|주임|교사")

# 조사는 길게 매칭될수록 앞의 '이름 후보'가 실제 명사 경계에 맞는다.
# ('주말에도' 를 '주말'+'에도' 로 끊어야 부정사전이 걸러낼 수 있다)
JOSA = (r"이라고|라고|이라는|라는|에게도|에게|한테도|한테|께서도|께서|께도|께|이랑|랑|"
        r"으로는|으로도|으로|로는|로도|로|에서는|에서도|에서|에는|에도|에|"
        r"이라도|라도|이나|이든|이야|조차|마저|밖에|처럼|같이|"
        r"은|는|이|가|을|를|의|도|만|과|와|부터|까지|보다|입니다|이고|이며")

SURNAMES = [
    "남궁", "독고", "황보", "제갈", "사공", "선우", "서문",
    "김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오", "서", "신",
    "권", "황", "안", "송", "류", "전", "홍", "고", "문", "양", "손", "배", "백", "허",
    "유", "남", "심", "노", "하", "곽", "성", "차", "주", "우", "구", "나", "민", "진",
    "지", "엄", "채", "원", "천", "방", "공", "현", "함", "변", "염", "여", "추", "도",
    "소", "석", "선", "설", "마", "길", "연", "위", "표", "명", "기", "반", "왕", "금",
    "옥", "육", "인", "맹", "제", "모", "탁", "국", "봉",
]
_SUR = "|".join(sorted(SURNAMES, key=len, reverse=True))

# 라벨 — 뒤에 오는 어절은 성씨 사전과 무관하게 이름으로 본다
NAME_LABELS = (r"성명|이름|성함|예금주|수취인|신청인|민원인|담당자|담당|인솔교사|인솔자|"
               r"보호자|주문자|고객명|명찰에|승객명|접수자|신고자|예약자|탑승자|"
               r"passenger\s+name|name\s+is|name")

ORG_SUFFIX = r"(?:초등학교|중학교|고등학교|대학교|대학|유치원|어린이집)"
DEPT_SUFFIX = r"(?:본부|사업부|지역본부|팀|센터|지사|영업소|관리실|고객실)"


# ======================================================================
# 검증기
# ======================================================================

def luhn_ok(s: str) -> bool:
    d = [int(c) for c in s if c.isdigit()]
    if len(d) < 13:
        return False
    total, parity = 0, len(d) % 2
    for i, n in enumerate(d):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def rrn_checksum_ok(s: str) -> bool:
    """2020-10 이후 발급분은 뒷자리가 임의값이라 실패한다 → 가산점 전용."""
    d = [int(c) for c in s if c.isdigit()]
    if len(d) != 13:
        return False
    w = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    return (11 - sum(a * b for a, b in zip(d[:12], w)) % 11) % 10 == d[12]


def digits_at_least(n: int) -> Callable[[str], bool]:
    return lambda s: sum(c.isdigit() for c in s) >= n


_LABEL_WORDS = set(re.split(r"\|", NAME_LABELS))


def is_name(word: str) -> bool:
    word = word.strip()
    if not (2 <= len(word) <= 5):
        return False
    # 조사가 붙은 채 잡혔을 수 있으므로 어간까지 부정사전을 본다 ('좌석은' → '좌석')
    if any(w in NAME_STOP for w in (word, word[:-1], word[:-2])):
        return False
    if word in _LABEL_WORDS:          # '예금주', '담당자' 는 라벨이지 이름이 아니다
        return False
    if re.search(r"(?:은행|카드|센터|본부|지사|공사|대학|학교|회사)$", word):
        return False
    if not all("가" <= c <= "힣" for c in word):
        return False
    if any(c in NON_NAME_SYL for c in word[1:]):
        return False
    if word[-1] in "님씨들역":   # '고객님' 같은 호칭 덩어리 차단
        return False
    return True


def is_birth_date(s: str) -> bool:
    """민원의 운행일은 항상 최근 ⇒ 2012년 이전 연도가 붙은 날짜는 생년월일."""
    m = re.search(r"(19|20)\d{2}", s)
    return bool(m) and int(m.group()) <= 2012


def not_station_word(s: str) -> bool:
    return s.strip() not in NOT_STATION


def caps_name_ok(s: str) -> bool:
    toks = [t for t in re.split(r"\s+", s.strip()) if t]
    if len(toks) < 2:
        return False
    return all(len(t) >= 3 and t.upper() == t and t.upper() not in BRAND_CAPS for t in toks)


# ======================================================================
# Span / Rule
# ======================================================================

SHIELD = "·보존·"   # 이 라벨을 가진 span 은 마스킹하지 않고 '자리만 점유'한다


@dataclass
class Span:
    start: int
    end: int
    label: str
    raw: str
    prio: int
    rule: str = ""

    @property
    def is_shield(self) -> bool:
        return self.label == SHIELD


@dataclass
class Rule:
    label: str
    pattern: "re.Pattern[str]"
    prio: int
    group: int = 0
    validator: Optional[Callable[[str], bool]] = None
    name: str = ""


def _r(p: str) -> "re.Pattern[str]":
    return re.compile(p)


# ----------------------------------------------------------------------
# Layer 1. SHIELD (prio 100) — 보존 대상 선점
# ----------------------------------------------------------------------

SHIELDS: List[Rule] = [
    Rule(SHIELD, _r(TRAINS + r"(?:\s*제?\s*\d{1,4}\s*호?)?"), 100, name="열차"),
    Rule(SHIELD, _r(r"[가-힣]{2,8}역(?![가-힣])"), 100, validator=not_station_word, name="역명"),
    Rule(SHIELD, _r(r"(?:" + "|".join(map(re.escape, OFFICIAL_NUMBERS)) + r")"), 100, name="대표번호"),
    Rule(SHIELD, _r(r"\d{1,3}(?:,\d{3})*\s*원"), 100, name="금액"),
    Rule(SHIELD, _r(r"\d{1,3}(?:\.\d+)?\s*%"), 100, name="비율"),
    Rule(SHIELD, _r(r"\d{1,3}(?:,\d{3})*\s*(?:명|석|점|건|개|번|분|시간|일|주|달|개월|회|차례|학년)"),
         100, name="수량"),
    Rule(SHIELD, _r(r"\d{1,2}\s*호차(?![\s]*\d)"), 100, name="호차단독"),
    Rule(SHIELD, _r(r"\d{1,2}:\d{2}"), 100, name="시각"),
    Rule(SHIELD, _r(r"\d{1,2}\s*월\s*\d{1,2}\s*일"), 100, name="운행일"),
    Rule(SHIELD, _r(r"(?<!\d)\d{1,2}/\d{1,2}(?!\d)"), 100, name="운행일"),
    Rule(SHIELD, _r(r"[A-Z]-\d{3,5}"), 100, name="오류코드"),
    # 앞에 '숫자.' 가 오면 IP 의 일부다 → 버전으로 오인해 IP를 보호해버리는 것 방지
    Rule(SHIELD, _r(r"(?<!\d\.)(?<!\d)\d+\.\d+\.\d+(?!\.?\d)"), 100, name="버전"),
    Rule(SHIELD, _r(r"[가-힣]{2,10}" + DEPT_SUFFIX + r"(?![가-힣])"), 100, name="부서"),
    # 은행명 자체는 개인정보가 아니다 (계좌번호가 개인정보다)
    Rule(SHIELD, _r(r"(?:국민|KB|신한|우리|하나|농협|NH|기업|IBK|산업|수협|새마을|신협|우체국|"
                    r"카카오|케이|토스|SC제일|씨티|대구|부산|경남|광주|전북|제주)\s*(?:은행|뱅크)"),
         100, name="은행명"),
    Rule(SHIELD, _r(r"(?<!\d)20(?:1\d|2\d|3\d)(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\d)"),
         100, name="운행일8자리"),
]

# ----------------------------------------------------------------------
# Layer 2. 탐지 규칙
# ----------------------------------------------------------------------

RULES: List[Rule] = [
    # ===== prio 120 : 라벨 문맥 식별자 (구조만으로는 못 잡는 번호 체계) =====
    Rule("사번", _r(r"(?:사번|직원번호|사원번호)\s*(?:은|는|이)?\s*[:：]?\s*([A-Za-z]?\d{3,10})"),
         120, group=1),
    Rule("회원번호", _r(r"(?:회원번호|멤버십번호|고객번호)\s*(?:은|는|이)?\s*[:：]?\s*([A-Za-z]?[\d-]{4,16}\d)"),
         120, group=1),
    Rule("예약번호", _r(r"(?:예약번호|예매번호|발권번호|보관번호|접수번호)\s*(?:은|는|이)?\s*[:：]?\s*"
                    r"([A-Za-z]{0,2}[\d-]{4,20}\d)"), 120, group=1),
    Rule("승차권번호", _r(r"(?:승차권번호|승차권|티켓번호)\s*(?:은|는|이)?\s*[:：]?\s*([\d][\d\-\s]{6,22}\d)"),
         120, group=1),
    Rule("복지카드번호", _r(r"(?:복지카드번호|복지카드|장애인등록번호)\s*(?:은|는|이)?\s*[:：]?\s*([\d][\d-]{4,16}\d)"),
         120, group=1),
    Rule("계좌번호", _r(r"(?:계좌번호|입금계좌|환불계좌|계좌)\s*(?:은|는|이)?\s*[:：]?\s*([\d][\d\-\s]{6,20}\d)"),
         120, group=1),
    Rule("생년월일", _r(r"(?:생년월일|생일|출생일)\s*(?:은|는|이)?\s*[:：]?\s*"
                    r"((?:19|20)?\d{2}\s*[년.\-/]\s*\d{1,2}\s*[월.\-/]\s*\d{1,2}\s*일?)"), 120, group=1),
    Rule("나이", _r(r"(?:아이|자녀|아동|유아|어린이|나이|연령)[^.]{0,24}?(만?\s*\d{1,2}\s*세)(?![기대])"),
         120, group=1),
    Rule("영문이름", _r(r"(?:name\s+is|성명|영문이름|영문성명|Dear)\s*[:：]?\s*"
                    r"([A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]{2,})+)"), 120, group=1),

    # ===== prio 110 : 과거연도 날짜 → 생년월일 =====
    Rule("생년월일", _r(r"(?:19|20)\d{2}\s*[년.\-/]\s*\d{1,2}\s*[월.\-/]\s*\d{1,2}\s*일?"),
         110, validator=is_birth_date),

    # ===== prio 105 : 좌석번호 (호차 SHIELD 를 덮어써야 함) =====
    Rule("좌석번호", _r(r"\d{1,2}\s*호차\s*\d{1,2}\s*[A-Fa-f가-힣]?\s*(?:석|번)?"), 105),
    Rule("좌석번호", _r(r"(?:좌석|자리)\s*(?:은|는|이)?\s*[:：]?\s*(\d{1,2}\s*[A-Fa-f]\b)"), 105, group=1),

    # ===== prio 90 : 구조적 직접식별자 =====
    Rule("주민등록번호", _r(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\s*[-–~]\s*[1-8]\d{6}(?!\d)"), 90),
    Rule("주민등록번호", _r(r"(?<!\d)\d{6}[1-8]\d{6}(?!\d)"), 90),
    Rule("휴대전화", _r(r"(?<!\d)01[016789][-.·)\s]{0,2}\d{3,4}[-.·\s]{0,2}\d{4}(?!\d)"), 90),
    Rule("안심번호", _r(r"(?<!\d)050\d[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"), 90),
    Rule("유선전화", _r(r"(?<!\d)0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70)[-.·)\s]{0,2}"
                    r"\d{3,4}[-.·\s]{0,2}\d{4}(?!\d)"), 90),
    Rule("한글전화", _r(r"(?:공|영|일|이|삼|사|오|육|륙|칠|팔|구)"
                    r"(?:\s*[-.]?\s*(?:공|영|일|이|삼|사|오|육|륙|칠|팔|구)){9,}"), 90),
    Rule("이메일", _r(r"[A-Za-z0-9._%+\-]+\s*(?:@|골뱅이|앳)\s*[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), 90),
    Rule("카드번호", _r(r"(?<!\d)\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}(?!\d)"), 90, validator=luhn_ok),
    Rule("카드번호", _r(r"(?<!\d)\d{4}[-\s]?[*xX]{4}[-\s]?[*xX]{4}[-\s]?\d{4}(?!\d)"), 90),
    Rule("여권번호", _r(r"(?<![A-Za-z0-9])[MSROmsro]\d{8}(?![0-9])"), 90),
    Rule("사업자번호", _r(r"(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)"), 90),
    Rule("차량번호", _r(r"(?<![0-9가-힣])(?:[가-힣]{2}\s?)?\d{2,3}\s?"
                    r"[가나다라마거너더러머버서어저고노도로모보소오조구누두루무부수우주하허호배]\s?\d{4}(?![0-9])"), 90),
    Rule("IP주소", _r(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"), 90),
    Rule("주소", _r(r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)"
                  r"(?:특별시|광역시|특별자치시|특별자치도|도)?\s*"
                  r"(?:[가-힣]{1,8}(?:시|군|구)\s*){1,2}[가-힣0-9]{1,14}(?:읍|면|동|가|로|길)\s*[0-9]{0,4}"
                  r"(?:\s*-\s*[0-9]{1,4})?"), 90),
    Rule("상세주소", _r(r"\d{1,4}\s*동\s*\d{1,5}\s*호"), 90),

    # ===== prio 80 : 도메인 준식별자·소속 =====
    Rule("예약번호", _r(r"(?<![A-Za-z0-9])[A-Z]\d{8,12}(?![A-Za-z0-9])"), 80),
    Rule("소속", _r(r"[가-힣]{2,10}" + ORG_SUFFIX + r"(?![가-힣])"), 80),
    Rule("영문이름", _r(r"(?<![A-Za-z])[A-Z]{3,}(?:\s+[A-Z]{3,})+(?![A-Za-z])"), 80,
         validator=caps_name_ok),

    # ===== prio 70 : 이름 — 문맥이 확실하면 성씨 사전을 보지 않는다 =====
    # 조사 뒤에 공백을 요구한다. 없으면 '명찰에 이수경' 의 '이'를 조사로 먹어
    # '수경이라고' 를 이름으로 잡는다 (실제로 발생한 버그).
    Rule("이름", _r(r"(?:" + NAME_LABELS + r")\s*[:：]?\s*(?:(?:은|는|이|가)(?=\s))?\s*"
                  r"([가-힣]{2,5})(?![가-힣])"), 70, group=1),
    Rule("이름", _r(r"(?<![가-힣])([가-힣]{2,5})\s*(?:" + TITLES + r")(?![가-힣])"), 70, group=1),
    Rule("이름", _r(r"(?:제|저|본인)\s*(?:이름은|이름이|성명은)?\s*([가-힣]{2,5})"
                  r"\s*(?:이라고|라고|입니다|이고|예요|이에요)"), 70, group=1),

    # ===== prio 60 : 이름 — 성씨 사전 + 조사 경계 (보조 수단) =====
    # 주의: 조사는 모든 명사 뒤에 붙으므로 이 규칙만으로는 '주말에도'를 이름으로 본다.
    #       정밀도는 NAME_STOP 부정 사전이 결정한다 (운영 비용의 본체).
    Rule("이름", _r(r"(?<![가-힣])((?:" + _SUR + r")[가-힣]{1,2})(?:" + JOSA + r")(?![가-힣])"), 60, group=1),

    # ===== prio 30 : FAIL-SAFE — 모르는 번호 체계를 통째로 차단 =====
    Rule("식별번호", _r(r"(?<![\d.])\d{7,}(?![\d.])"), 30),
    Rule("식별번호", _r(r"(?<![\d\-.])[A-Za-z]?\d{2,6}(?:[-\s]\d{2,6}){1,4}(?![\d\-.])"), 30,
         validator=digits_at_least(8)),
    Rule("식별번호", _r(r"(?<![A-Za-z0-9])[A-Za-z]{1,2}\d{6,}(?![A-Za-z0-9])"), 30),
]

for _r_ in RULES:
    if not _r_.name:
        _r_.name = _r_.pattern.pattern[:24]


# ======================================================================
# Layer 5. 잔존검사 — 통과 못 하면 격리
# ======================================================================

MASK_TOKEN_START = "[<*〓"

# 마스킹 토큰 자체를 검사 대상에서 제외한다.
# (제외하지 않으면 '[이름]이고' 의 '이름'을 라벨로 읽어 자기 자신을 격리한다)
_TOKEN_SHAPE = _r(r"\[[^\[\]]{1,24}\]|<[^<>]{1,24}>|\*{2,}")


def _blind_tokens(masked: str) -> str:
    """토큰을 같은 길이의 〓 로 바꿔 오프셋을 유지한 채 가린다."""
    return _TOKEN_SHAPE.sub(lambda m: "〓" * (m.end() - m.start()), masked)


def _mixed_alnum(s: str) -> bool:
    """영문과 숫자가 섞인 긴 토큰 = 모르는 번호 체계일 가능성."""
    return any(c.isdigit() for c in s) and any(c.isalpha() for c in s)


RESIDUAL = [
    ("연속숫자7+", _r(r"(?<![\d.])\d{7,}(?![\d.])"), None),
    ("숫자군", _r(r"(?<![\d\-.])\d{3,4}[-\s]\d{4}(?![\d\-.])"), None),
    ("이메일흔적", _r(r"[A-Za-z0-9]+\s*@"), None),
    ("휴대전화흔적", _r(r"(?<!\d)01[016789][-.\s]?\d{3,4}"), None),
    ("호칭앞어절", _r(r"(?<![\]\>가-힣])([가-힣]{2,5})\s*(?:고객님|귀하|님|씨)(?![가-힣])"), None),
    ("연속대문자", _r(r"(?<![A-Za-z])[A-Z]{3,}(?:\s+[A-Z]{3,})+(?![A-Za-z])"), caps_name_ok),
    # ↓ 모르는 표기를 격리로 보내기 위한 '포괄 그물'
    ("영숫자혼합토큰", _r(r"(?<![A-Za-z0-9])[A-Za-z0-9]{10,}(?![A-Za-z0-9])"), _mixed_alnum),
    ("분절숫자열", _r(r"(?:\d[\s.\-]+){6,}\d"), None),
    ("우회표기", _r(r"[A-Za-z0-9]{2,}\s+(?:dot|at|골뱅이|점|닷|앳)\s+[A-Za-z0-9]{2,}"), None),
]

# 한글 규칙은 영문 성명을 신뢰성 있게 가릴 수 없다.
# 영문 비중이 높은 레코드는 통과시키지 않고 사람 검토로 보낸다 (언어 게이트).
LATIN_WORD_LIMIT = 4
_LATIN_WORD = _r(r"(?<![A-Za-z])[A-Za-z]{2,}(?![A-Za-z])")

# 사람 라벨 뒤가 마스킹되지 않았으면 무조건 격리한다.
# 규칙이 모르는 성씨·표기여도 '라벨이 있다'는 사실만으로 유출을 잡을 수 있다.
_LABEL_RESIDUAL = _r(r"(?:" + NAME_LABELS + r")\s*[:：]?\s*(?:(?:은|는|이|가)\s+)?\s*"
                     r"([^\s\[\<\*][^\s,./]{0,12})")


def residual_scan(raw_masked: str) -> List[str]:
    masked = _blind_tokens(raw_masked)
    hits: List[str] = []
    shielded: List[Tuple[int, int]] = []
    for sh in SHIELDS:
        for m in sh.pattern.finditer(masked):
            if sh.validator and not sh.validator(m.group()):
                continue
            shielded.append((m.start(), m.end()))

    def is_shielded(a: int, b: int) -> bool:
        return any(a < e and s < b for s, e in shielded)

    for name, pat, check in RESIDUAL:
        for m in pat.finditer(masked):
            if is_shielded(m.start(), m.end()):
                continue
            tok = m.group(1) if pat.groups else m.group()
            if name == "호칭앞어절" and (tok in NAME_STOP or not is_name(tok)):
                continue
            if name == "영문2어절" and tok.split()[0] in {"dot", "at", "and", "the", "for"}:
                continue
            if check and not check(m.group()):
                continue
            hits.append(f"{name}: {m.group().strip()}")

    for m in _LABEL_RESIDUAL.finditer(masked):
        word = m.group(1)
        if word[0] in MASK_TOKEN_START or word in NAME_STOP or word.rstrip("은는이가") in NAME_STOP:
            continue
        if is_shielded(*m.span(1)):
            continue
        hits.append(f"라벨뒤미마스킹: {m.group().strip()}")

    latin = [m.group() for m in _LATIN_WORD.finditer(masked)
             if m.group().upper() not in BRAND_CAPS and not is_shielded(*m.span())]
    if len(latin) >= LATIN_WORD_LIMIT:
        hits.append(f"영문레코드: 영문 {len(latin)}단어 — 한글 규칙 미적용, 사람 검토 필요")
    return hits


# ======================================================================
# 엔진
# ======================================================================

@dataclass
class MaskResult:
    text: str
    spans: List[Span] = field(default_factory=list)
    residual: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "QUARANTINE" if self.residual else "PASS"

    @property
    def counts(self) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for s in self.spans:
            c[s.label] = c.get(s.label, 0) + 1
        return c


class RailMasker:
    """mode: 'label' | 'token' | 'partial'   (MASKING_POLICY.md §5)"""

    def __init__(self, mode: str = "token", failsafe: bool = True):
        self.mode = mode
        self.failsafe = failsafe

    # ---------- 탐지 ----------
    def detect(self, text: str) -> List[Span]:
        cands: List[Span] = []
        for rule in SHIELDS + RULES:
            if not self.failsafe and rule.prio == 30:
                continue
            for m in rule.pattern.finditer(text):
                s, e = m.span(rule.group)
                raw = m.group(rule.group)
                if raw is None or s < 0:
                    continue
                if rule.validator and not rule.validator(raw):
                    continue
                if rule.label == "이름" and not is_name(raw):
                    continue
                if rule.label == "소속" and raw in NAME_STOP:
                    continue
                cands.append(Span(s, e, rule.label, raw, rule.prio, rule.name))
        # 우선순위 → 길이 → 위치 순으로 선점. 겹치면 먼저 선점한 쪽이 이긴다.
        kept = self._resolve(cands)
        if self.failsafe:
            extra = self._propagate(text, kept)
            if extra:
                kept = self._resolve(kept + extra)
        return sorted(kept, key=lambda sp: sp.start)

    @staticmethod
    def _resolve(cands: List[Span]) -> List[Span]:
        """우선순위 → 길이 → 위치 순 선점. 겹치면 먼저 선점한 쪽이 이긴다."""
        cands = sorted(cands, key=lambda sp: (-sp.prio, -(sp.end - sp.start), sp.start))
        kept: List[Span] = []
        for sp in cands:
            if any(sp.start < k.end and k.start < sp.end for k in kept):
                continue
            kept.append(sp)
        return kept

    @staticmethod
    def _propagate(text: str, kept: List[Span]) -> List[Span]:
        """2차 패스 — 근접 확산.

        '승차권 9876543210, 남궁민수, 010-2222-3333' 처럼
        쉼표로 나열된 한글 어절이 이미 탐지된 식별자 사이에 끼어 있으면 이름으로 본다.
        조사도 호칭도 없어 1차 패스가 구조적으로 잡을 수 없는 자리다.
        """
        pii = [s for s in kept if not s.is_shield]
        if not pii:
            return []
        found: List[Span] = []
        for m in re.finditer(r"[가-힣]{2,5}", text):
            s, e = m.span()
            word = m.group()
            if not is_name(word) or any(s < k.end and k.start < e for k in kept):
                continue
            left, right = text[max(0, s - 2):s], text[e:e + 2]
            listed = bool(re.search(r"[,/·|]", left + right))   # 쉼표 나열
            # 연락처 바로 앞 자리는 쉼표가 없어도 이름 자리다 ('조현우 010-...')
            touches_contact = any(
                0 <= p.start - e <= 2 and p.label in CONTACT_LABELS for p in pii)
            if not (listed or touches_contact):
                continue
            near = any(0 <= s - p.end <= 3 or 0 <= p.start - e <= 3 for p in pii)
            if near:
                found.append(Span(s, e, "이름", word, 40, "근접확산"))
        return found

    # ---------- 치환 ----------
    def _replace(self, sp: Span, ctx: "_Ctx") -> str:
        if self.mode == "label":
            return f"[{sp.label}]"
        if self.mode == "partial":
            digits = re.sub(r"\D", "", sp.raw)
            if len(digits) >= 7:
                return re.sub(r"\d", "*", sp.raw[:-4]) + sp.raw[-4:]
            if len(sp.raw) >= 3:
                return sp.raw[0] + "*" * (len(sp.raw) - 2) + sp.raw[-1]
            return sp.raw[0] + "*"
        # token 모드: 같은 원문 → 같은 토큰 (쌍 내 일관성)
        key = (sp.label, re.sub(r"\s+", "", sp.raw))
        if key not in ctx.assigned:
            ctx.seq[sp.label] = ctx.seq.get(sp.label, 0) + 1
            ctx.assigned[key] = f"<{sp.label}_{ctx.seq[sp.label]}>"
            ctx.mapping[ctx.assigned[key]] = sp.raw
        return ctx.assigned[key]

    def mask(self, text: str, ctx: "Optional[_Ctx]" = None) -> MaskResult:
        ctx = ctx or _Ctx()
        norm = normalize(text)
        spans = [s for s in self.detect(norm) if not s.is_shield]
        out: List[str] = []
        cur = 0
        for sp in spans:
            out.append(norm[cur:sp.start])
            out.append(self._replace(sp, ctx))
            cur = sp.end
        out.append(norm[cur:])
        masked = "".join(out)
        return MaskResult(masked, spans, residual_scan(masked))

    def mask_record(self, record: Dict[str, str],
                    fields: Sequence[str] = ("민원", "답변")) -> Dict[str, object]:
        """민원·답변을 한 컨텍스트로 처리해 동일 인물에 같은 토큰을 부여한다."""
        ctx = _Ctx()
        out: Dict[str, object] = {k: v for k, v in record.items() if k not in fields}
        residual: List[str] = []
        counts: Dict[str, int] = {}
        for f in fields:
            if f not in record:
                continue
            res = self.mask(record[f], ctx)
            out[f] = res.text
            residual += [f"{f}/{h}" for h in res.residual]
            for k, v in res.counts.items():
                counts[k] = counts.get(k, 0) + v
        out["_mapping"] = dict(ctx.mapping)
        out["_counts"] = counts
        out["_residual"] = residual
        out["_status"] = "QUARANTINE" if residual else "PASS"
        return out


@dataclass
class _Ctx:
    seq: Dict[str, int] = field(default_factory=dict)
    assigned: Dict[Tuple[str, str], str] = field(default_factory=dict)
    mapping: Dict[str, str] = field(default_factory=dict)


# ======================================================================
# 컬럼 정책 — 운영자가 고치는 유일한 표
# ======================================================================

KEEP, FIXED, IDNUM, DATE, TEXT = "KEEP", "FIXED", "IDNUM", "DATE", "TEXT"


@dataclass
class Policy:
    action: str
    arg: str = ""
    note: str = ""


POLICY: Dict[str, Policy] = {
    "접수번호":     Policy(IDNUM, note="형식만 남김. --id-mode 로 고유값 유지 가능"),
    "민원유형":     Policy(KEEP,  note="분석 핵심 축"),
    "유입경로":     Policy(KEEP),
    "등록일":       Policy(DATE,  note="--date-mode 로 시간축 보존 가능"),
    "접수일":       Policy(KEEP,  note="⚠ 등록일을 가려도 여기서 새어나간다 (--all-dates 참고)"),
    "처리기한":     Policy(KEEP),
    "완료일":       Policy(KEEP,  note="⚠ 상동"),
    "고객요구유형": Policy(KEEP),
    "제목":         Policy(TEXT),
    "질문":         Policy(TEXT),
    "답변":         Policy(TEXT),
    "상위완료유형": Policy(KEEP),
    "완료유형":     Policy(KEEP),
    "처리부서":     Policy(KEEP,  note="부서는 개인정보가 아님"),
    "처리자":       Policy(FIXED, arg="OOO"),
}

TEXT_FIELDS = [c for c, p in POLICY.items() if p.action == TEXT]
DATE_FIELDS_ALL = ["등록일", "접수일", "처리기한", "완료일"]

ID_FORMAT = "000000-00000000"
DATE_FORMAT = "YYYY-MM-DD hh:mm:ss"
DATE_ONLY_FORMAT = "YYYY-MM-DD"

_DT_PATTERNS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y.%m.%d %H:%M:%S", "%Y.%m.%d"]


def parse_dt(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    for p in _DT_PATTERNS:
        try:
            return datetime.strptime(s, p)
        except ValueError:
            continue
    return None


# ======================================================================
# 컬럼별 변환기
# ======================================================================

class Transformer:
    def __init__(self, id_mode: str = "fixed", date_mode: str = "format",
                 salt: str = "", all_dates: bool = False, mode: str = "token",
                 failsafe: bool = True):
        self.id_mode = id_mode
        self.date_mode = date_mode
        self.salt = salt
        self.all_dates = all_dates
        self.masker = RailMasker(mode=mode, failsafe=failsafe)
        self._n = 0

    # ---------- 접수번호 ----------
    def transform_id(self, value: str) -> str:
        if self.id_mode == "fixed":
            return ID_FORMAT
        self._n += 1
        if self.id_mode == "surrogate":
            return f"000000-{self._n:08d}"
        if self.id_mode == "hash":
            h = hashlib.sha256((self.salt + "|" + (value or "")).encode()).hexdigest()
            return f"000000-{int(h[:12], 16) % 10**8:08d}"
        raise ValueError(self.id_mode)

    # ---------- 날짜 ----------
    def transform_date(self, value: str, column: str) -> str:
        if not (value or "").strip():
            return value
        if self.date_mode == "keep":
            return value
        has_time = bool(re.search(r"\d{1,2}:\d{2}", value))
        if self.date_mode == "format":
            return DATE_FORMAT if has_time else DATE_ONLY_FORMAT
        dt = parse_dt(value)
        if dt is None:
            return DATE_FORMAT if has_time else DATE_ONLY_FORMAT
        if self.date_mode == "hour":
            return dt.strftime("%Y-%m-%d %H:00:00") if has_time else dt.strftime("%Y-%m-%d")
        if self.date_mode == "date":
            return dt.strftime("%Y-%m-%d")
        if self.date_mode == "month":
            return dt.strftime("%Y-%m")
        raise ValueError(self.date_mode)

    # ---------- 행 ----------
    def transform_row(self, row: Dict[str, str]) -> Dict[str, object]:
        ctx = _Ctx()                      # 행 단위 — 같은 사람은 제목·질문·답변에서 같은 토큰
        out: Dict[str, object] = {}
        residual: List[str] = []
        counts: Dict[str, int] = {}

        for col, value in row.items():
            pol = POLICY.get(col, Policy(KEEP))
            action = pol.action
            if self.all_dates and col in DATE_FIELDS_ALL:
                action = DATE

            if action == FIXED:
                out[col] = pol.arg
            elif action == IDNUM:
                out[col] = self.transform_id(value)
            elif action == DATE:
                out[col] = self.transform_date(value, col)
            elif action == TEXT:
                res = self.masker.mask(value or "", ctx)
                out[col] = res.text
                residual += [f"{col}/{h}" for h in res.residual]
                for k, v in res.counts.items():
                    counts[k] = counts.get(k, 0) + v
            else:
                out[col] = value

        out["_status"] = "QUARANTINE" if residual else "PASS"
        out["_residual"] = " | ".join(residual)
        out["_counts"] = json.dumps(counts, ensure_ascii=False)
        out["_mapping"] = dict(ctx.mapping)
        return out


# ======================================================================
# 정합성 점검 — 한 컬럼만 가리고 옆 컬럼으로 새는 것을 막는다
# ======================================================================

def consistency_warnings(t: Transformer, columns: List[str]) -> List[str]:
    warn: List[str] = []
    if t.date_mode != "keep" and not t.all_dates:
        leaks = [c for c in DATE_FIELDS_ALL[1:]
                 if c in columns and POLICY.get(c, Policy(KEEP)).action == KEEP]
        if leaks and POLICY.get("등록일", Policy(KEEP)).action == DATE:
            warn.append(
                f"등록일은 가렸지만 {', '.join(leaks)} 이(가) 원본 그대로입니다. "
                f"접수일은 보통 등록일과 몇 시간 차이라 등록일 마스킹이 무의미해집니다. "
                f"→ --all-dates 로 함께 처리하거나, --date-mode keep 으로 시간축을 아예 보존하세요.")
    if t.id_mode == "fixed":
        warn.append(
            "접수번호가 전부 동일한 값이 되어 행 식별이 불가능합니다. "
            "중복 민원 집계나 다른 테이블과의 조인이 필요하면 --id-mode surrogate 를 쓰세요.")
    if t.date_mode == "format":
        warn.append(
            "등록일이 전부 'YYYY-MM-DD hh:mm:ss' 문자열이 되어 시간대별 민원 예측이 불가능합니다. "
            "시간축이 필요하면 --date-mode hour 를 쓰세요 (시 단위 일반화, 분·초만 제거).")
    return warn


COLUMNS = ["접수번호", "민원유형", "유입경로", "등록일", "접수일", "처리기한", "완료일",
           "고객요구유형", "제목", "질문", "답변", "상위완료유형", "완료유형",
           "처리부서", "처리자"]

민원유형 = ["지연보상", "환불", "분실물", "시설", "직원응대", "예약발권", "할인적용", "주차", "앱오류", "단체"]
유입경로 = ["홈페이지", "모바일앱", "고객센터", "역사접수", "국민신문고", "이메일"]
고객요구유형 = ["환불요청", "개선요청", "설명요구", "사과요구", "정보문의", "재발방지"]
상위완료유형 = ["처리완료", "부분수용", "불수용", "안내종결"]
완료유형 = ["보상지급", "규정안내", "시설개선", "교육실시", "단순안내", "환불처리"]
처리부서 = ["여객사업본부", "고객만족팀", "역무팀", "시설관리팀", "단체운송팀", "법인영업팀"]

성 = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오", "서", "신", "황", "남궁"]
이름2 = ["민수", "지훈", "서연", "하늘", "수경", "성호", "도윤", "지안", "현우", "윤아", "민재", "세훈",
         "지원", "선영", "민호", "예진", "준서", "다인", "태현", "소은"]
역 = ["서울", "용산", "광명", "수원", "천안아산", "오송", "대전", "동대구", "부산", "익산",
      "광주송정", "목포", "강릉", "청량리", "포항", "경주", "울산", "전주", "순천", "여수엑스포"]
열차 = ["KTX", "SRT", "ITX-새마을", "무궁화호", "KTX-이음", "ITX-청춘"]
은행 = ["국민은행", "신한은행", "우리은행", "하나은행", "농협은행", "기업은행", "카카오뱅크"]


def 사람(rnd: random.Random) -> str:
    return rnd.choice(성) + rnd.choice(이름2)


def 휴대폰(rnd: random.Random) -> str:
    return f"010-{rnd.randint(1000,9999)}-{rnd.randint(1000,9999)}"


def 유선(rnd: random.Random) -> str:
    국번 = rnd.choice(["02", "031", "032", "033", "041", "042", "043", "051", "052", "053", "054", "055", "061", "062", "063"])
    return f"{국번}-{rnd.randint(200,999)}-{rnd.randint(1000,9999)}"


def 좌석(rnd: random.Random) -> str:
    return f"{rnd.randint(1,18)}호차 {rnd.randint(1,20)}{rnd.choice('ABCD')}"


def 열차편(rnd: random.Random) -> str:
    return f"{rnd.choice(열차)} {rnd.randint(1,999)}"


def 예약번호(rnd: random.Random) -> str:
    return "R" + "".join(str(rnd.randint(0, 9)) for _ in range(10))


def 계좌(rnd: random.Random) -> str:
    return f"{rnd.randint(100,999)}-{rnd.randint(10,99)}-{rnd.randint(100000,999999)}"


def 이메일(rnd: random.Random) -> str:
    아이디 = rnd.choice(["user", "traveler", "hong", "kimdh", "rail", "ticket", "gd.hong"])
    return f"{아이디}{rnd.randint(1,99)}@{rnd.choice(['naver.com','gmail.com','daum.net','example.co.kr'])}"


def 주소(rnd: random.Random) -> str:
    시도 = rnd.choice(["서울특별시", "부산광역시", "경기", "인천광역시", "대구광역시"])
    구 = rnd.choice(["강남구", "마포구", "해운대구", "성남시 분당구", "남동구", "수성구"])
    로 = rnd.choice(["테헤란로", "월드컵북로", "센텀중앙로", "판교로", "구월로", "동대구로"])
    return f"{시도} {구} {로} {rnd.randint(1,300)}"


# (민원유형, 제목템플릿, 질문템플릿, 답변템플릿)
TEMPLATES = [
    ("지연보상",
     "{열차} 지연 보상 요청합니다",
     "{열차} 열차가 {역}역에서 {분}분 지연 출발해 환승을 놓쳤습니다. 저는 {이름}이고 연락처는 {폰}입니다. "
     "예약번호 {예약}, 좌석은 {좌석}이었습니다. 지연 보상 신청합니다.",
     "{이름} 고객님, 지연 사실을 확인하였습니다. 지연보상 규정에 따라 운임의 {율}%인 {금액}원을 환불 처리하였습니다. "
     "추가 문의는 대표번호 1544-7788 또는 담당자 직통 {유선}으로 연락 주십시오. 담당 {직원} (사번 {사번})"),

    ("환불",
     "현금 결제분 계좌 환불 요청",
     "현금으로 결제했으니 계좌로 환불해 주세요. {계좌} {은행}, 예금주 {이름}입니다. "
     "주소는 {주소} {동호}입니다. 연락처 {폰}.",
     "{이름} 고객님, 환불 계좌 접수되었습니다. 영업일 기준 3일 내 {금액}원이 입금됩니다. "
     "처리 결과는 등록된 연락처로 안내드립니다."),

    ("분실물",
     "{열차} 유실물 신고합니다",
     "{열차} {호차}호차에 가방을 두고 내렸습니다. {역}역 하차였고 좌석은 {좌석}입니다. "
     "찾으시면 {폰}으로 연락 주세요. {이름}입니다.",
     "{이름} 고객님, {역}역 유실물센터에 해당 물품이 접수되어 있습니다. 보관번호는 안내 문자로 발송하였습니다. "
     "수령 시 신분증이 필요하며 09:00부터 18:00 사이 수령 가능합니다."),

    ("직원응대",
     "{역}역 창구 직원 응대 불만",
     "{역}역 매표창구 직원이 불친절했습니다. 명찰에 {직원}이라고 적혀 있었습니다. "
     "저는 {이름}이고 {폰}입니다. 사과를 요구합니다.",
     "{이름} 고객님, 불편을 드려 죄송합니다. 해당 직원에 대해 고객응대 교육을 실시하였습니다. "
     "추가 의견은 고객만족팀 {직원2} {유선}으로 주시면 반영하겠습니다."),

    ("예약발권",
     "좌석 중복 발권 오류",
     "{열차} 탑승했는데 제 자리 {좌석}에 다른 분이 앉아 계셨습니다. 예약번호 {예약} 확인 부탁드립니다. "
     "제 이름은 {이름}, 연락처 {폰}입니다.",
     "{이름} 고객님, 중복 발권 오류를 확인했습니다. 발권 시스템 장애로 일부 좌석이 이중 배정되었습니다. "
     "전액 {금액}원을 환불하였으며 다음 탑승 시 사용 가능한 10% 할인쿠폰을 등록해 드렸습니다."),

    ("할인적용",
     "경로 할인이 적용되지 않았습니다",
     "할인이 적용되지 않았습니다. {이름}이고 생년월일 {생일}로 등록되어 있습니다. "
     "회원번호 {회원}이고 메일은 {메일}입니다. {열차} 이용했습니다.",
     "{이름} 고객님, 회원정보 불일치로 할인이 미적용되었습니다. 확인 후 차액 {금액}원을 환불하였습니다. "
     "재발 방지를 위해 회원정보 수정을 부탁드립니다."),

    ("주차",
     "{역}역 주차장 출차 불가",
     "{역}역 주차장에서 출차가 안 됩니다. 차량번호 {차량}이고 연락처 {폰}입니다. 주차권을 분실했습니다.",
     "주차 관제 시스템 오류를 확인하였습니다. 차량 출차 처리 완료하였으며 주차요금 {금액}원은 면제 처리되었습니다."),

    ("앱오류",
     "모바일 앱 결제 반복 실패",
     "모바일 앱에서 결제가 세 번 실패했습니다. 로그인 계정은 {메일}이고 접속 IP는 {ip}로 표시됐습니다. "
     "오류코드 E-5021이 떴습니다.",
     "E-5021은 카드사 승인 지연 시 발생하는 코드입니다. 중복 결제분은 자동 취소되었으며 실제 청구는 1건만 발생합니다. "
     "앱 버전 4.2.1 이상으로 업데이트 부탁드립니다."),

    ("단체",
     "단체 예약 인원 변경 요청",
     "수학여행 단체 예약 변경하려고 합니다. 인솔교사 {이름}, {폰}입니다. "
     "{학교} 2학년 {인원}명이고 {열차} 출발입니다.",
     "단체 예약 변경은 출발 3일 전까지 가능합니다. 좌석은 {인원}석 확보되어 있으며 "
     "인원 변경 시 단체운송팀 {직원2} {폰2}로 연락 주십시오."),

    ("시설",
     "{역}역 화장실 청결 상태 개선 요청",
     "{역}역 화장실 상태가 좋지 않습니다. 청소 주기를 늘려주세요. 특정 민원인 정보 없이 접수합니다.",
     "시설 점검을 실시하였습니다. 청소 주기를 기존 2시간에서 1시간으로 단축하고 점검 기록을 게시하도록 조치하였습니다. "
     "의견 감사합니다."),

    ("기타",
     "입석 승차권 지연보상 기준 문의",
     "입석 승차권도 지연 보상 대상인가요? 환불 기준이 궁금합니다.",
     "입석 승차권도 동일하게 지연보상 대상입니다. 20분 이상 40분 미만 지연 시 운임의 12.5%, "
     "40분 이상 60분 미만은 25%, 60분 이상은 50%가 보상됩니다."),

    ("기타",
     "{역}역 첫차 시간 문의",
     "{역}역에서 출발하는 첫차 시간이 몇 시인지 알려주세요. 주말에도 같은 시간인가요?",
     "평일 첫차는 05:11, 주말은 05:30입니다. 자세한 시간표는 홈페이지에서 확인하실 수 있습니다."),
]


def build(rnd: random.Random, base: datetime, seq: int) -> dict:
    유형, 제목t, 질문t, 답변t = rnd.choice(TEMPLATES)
    이름 = 사람(rnd)
    직원 = 사람(rnd)
    직원2 = 사람(rnd)
    vals = {
        "열차": 열차편(rnd), "역": rnd.choice(역), "분": rnd.choice([20, 25, 30, 40, 55, 70]),
        "이름": 이름, "폰": 휴대폰(rnd), "폰2": 휴대폰(rnd), "유선": 유선(rnd),
        "예약": 예약번호(rnd), "좌석": 좌석(rnd), "호차": rnd.randint(1, 18),
        "율": rnd.choice([12.5, 25, 50]), "금액": f"{rnd.randint(3,60)*100:,}",
        "직원": 직원, "직원2": 직원2, "사번": rnd.randint(10000, 99999),
        "계좌": 계좌(rnd), "은행": rnd.choice(은행), "주소": 주소(rnd),
        "동호": f"{rnd.randint(101,305)}동 {rnd.randint(101,2203)}호",
        "생일": f"{rnd.randint(1955,2005)}년 {rnd.randint(1,12)}월 {rnd.randint(1,28)}일",
        "회원": "K" + "".join(str(rnd.randint(0, 9)) for _ in range(10)),
        "메일": 이메일(rnd), "차량": f"{rnd.randint(10,99)}{rnd.choice('가나다라마거너더러머버서')}{rnd.randint(1000,9999)}",
        "ip": f"{rnd.randint(1,223)}.{rnd.randint(0,255)}.{rnd.randint(0,255)}.{rnd.randint(1,254)}",
        "학교": rnd.choice(["한빛", "새봄", "동백", "푸른", "해송"]) + rnd.choice(["고등학교", "중학교"]),
        "인원": rnd.choice([40, 80, 120, 150]),
    }

    등록 = base + timedelta(days=rnd.randint(0, 180), hours=rnd.randint(7, 21),
                           minutes=rnd.randint(0, 59), seconds=rnd.randint(0, 59))
    접수 = 등록 + timedelta(minutes=rnd.randint(1, 600))
    기한 = 접수 + timedelta(days=rnd.choice([3, 5, 7, 14]))
    완료 = 접수 + timedelta(days=rnd.randint(0, 12), hours=rnd.randint(0, 20))
    미완료 = rnd.random() < 0.12          # 일부 레코드는 완료일 없음

    return {
        "접수번호": f"{등록.strftime('%Y%m')}-{seq:08d}",
        "민원유형": 유형 if 유형 != "기타" else rnd.choice(민원유형),
        "유입경로": rnd.choice(유입경로),
        "등록일": 등록.strftime("%Y-%m-%d %H:%M:%S"),
        "접수일": 접수.strftime("%Y-%m-%d %H:%M:%S"),
        "처리기한": 기한.strftime("%Y-%m-%d"),
        "완료일": "" if 미완료 else 완료.strftime("%Y-%m-%d %H:%M:%S"),
        "고객요구유형": rnd.choice(고객요구유형),
        "제목": 제목t.format(**vals),
        "질문": 질문t.format(**vals),
        "답변": 답변t.format(**vals),
        "상위완료유형": "" if 미완료 else rnd.choice(상위완료유형),
        "완료유형": "" if 미완료 else rnd.choice(완료유형),
        "처리부서": rnd.choice(처리부서),
        "처리자": 직원,
    }


# ======================================================================
# 서브커맨드
# ======================================================================

def _write_csv(path: str, cols: List[str], rows: List[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def cmd_dummy(args: argparse.Namespace) -> int:
    rnd = random.Random(args.seed)
    base = datetime(2026, 4, 1)
    rows = [build(rnd, base, i + 1) for i in range(args.rows)]
    rows.sort(key=lambda r: r["등록일"])
    for i, r in enumerate(rows, 1):
        r["접수번호"] = f"{r['등록일'][:4]}{r['등록일'][5:7]}-{i:08d}"
    _write_csv(args.out, COLUMNS, rows)
    print(f"{args.out} — {len(rows)}행 생성 (컬럼 {len(COLUMNS)}개)")
    return 0


def cmd_mask(args: argparse.Namespace) -> int:
    t = Transformer(id_mode=args.id_mode, date_mode=args.date_mode, salt=args.salt,
                    all_dates=args.all_dates, mode=args.mask_mode,
                    failsafe=not args.no_failsafe)
    with open(args.input, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("입력이 비어 있습니다.", file=sys.stderr)
        return 1
    columns = list(rows[0].keys())

    unknown = [c for c in columns if c not in POLICY]
    if unknown:
        print(f"[주의] 정책에 없는 컬럼은 원본 유지됩니다: {', '.join(unknown)}", file=sys.stderr)
    missing = [c for c in POLICY if c not in columns]
    if missing:
        print(f"[주의] 정책에 있으나 입력에 없는 컬럼: {', '.join(missing)}", file=sys.stderr)

    out_cols = columns + (["_status", "_residual", "_counts"] if args.keep_meta else [])
    passed: List[dict] = []
    quar: List[dict] = []
    mappings: List[dict] = []

    for i, row in enumerate(rows):
        r = t.transform_row(row)
        mapping = r.pop("_mapping")
        if mapping:
            mappings.append({"행": i + 1, "접수번호": r.get("접수번호"), "mapping": mapping})
        rec = {c: r.get(c, "") for c in out_cols}
        (quar if r["_status"] == "QUARANTINE" else passed).append(rec)

    _write_csv(args.output, out_cols, passed if args.quarantine else passed + quar)
    if args.quarantine:
        _write_csv(args.quarantine, out_cols, quar)
    if args.mapping:
        with open(args.mapping, "w", encoding="utf-8") as f:
            for m in mappings:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    print(f"입력 {len(rows)}행 → PASS {len(passed)}행 / QUARANTINE {len(quar)}행", file=sys.stderr)
    print(f"  마스킹 본문 컬럼 : {', '.join(TEXT_FIELDS)}", file=sys.stderr)
    print(f"  접수번호 모드     : {args.id_mode}", file=sys.stderr)
    print(f"  날짜 모드         : {args.date_mode}"
          f"{' (전체 날짜 컬럼)' if args.all_dates else ''}", file=sys.stderr)
    if args.mapping:
        print(f"  매핑표            : {args.mapping} — 원본과 분리·암호화 보관 필수", file=sys.stderr)
    for w in consistency_warnings(t, columns):
        print(f"\n[정합성 경고] {w}", file=sys.stderr)
    return 0


def cmd_text(args: argparse.Namespace) -> int:
    masker = RailMasker(mode=args.mask_mode, failsafe=not args.no_failsafe)
    src = args.text if args.text else sys.stdin.read()
    for line in src.splitlines():
        if not line.strip():
            continue
        res = masker.mask(line)
        print(res.text)
        if args.verbose:
            print(f"  게이트: {res.status}", file=sys.stderr)
            if res.counts:
                print(f"  탐지  : {res.counts}", file=sys.stderr)
            if res.residual:
                print(f"  잔존  : {res.residual}", file=sys.stderr)
    return 0


AUDIT_PATTERNS = [
    ("휴대전화", re.compile(r"01[016789]-?\d{3,4}-?\d{4}")),
    ("이메일", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+")),
    ("주민등록번호", re.compile(r"\d{6}\s*-\s*[1-8]\d{6}")),
    ("연속숫자7+", re.compile(r"(?<![\d.,])\d{7,}(?![\d.,])")),
    ("IP주소", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")),
    ("계좌형", re.compile(r"\d{3}-\d{2}-\d{6}")),
]


def cmd_audit(args: argparse.Namespace) -> int:
    """마스킹 결과물을 독립적으로 재검사한다. 마스커 자신을 믿지 않는 교차 검증."""
    with open(args.input, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    cols = args.columns.split(",") if args.columns else TEXT_FIELDS
    cols = [c for c in cols if rows and c in rows[0]]
    hits: List[str] = []
    for i, r in enumerate(rows, 1):
        blob = " ".join(r.get(c, "") for c in cols)
        for name, pat in AUDIT_PATTERNS:
            for m in pat.finditer(blob):
                hits.append(f"  {i}행 [{name}] {m.group()}")
    print(f"검사 {len(rows)}행 × 컬럼 {', '.join(cols)}")
    if hits:
        print(f"잔존 의심 {len(hits)}건")
        for h in hits[:40]:
            print(h)
        if len(hits) > 40:
            print(f"  ... 외 {len(hits)-40}건")
    else:
        print("잔존 의심 0건")
    return 1 if hits else 0


SELFTEST = [
    # (입력, 반드시 사라져야 하는 문자열, 반드시 남아야 하는 문자열, 기대 게이트)
    ("저는 김성호이고 연락처는 010-2345-6789입니다.", ["김성호", "010-2345-6789"], [], "PASS"),
    ("KTX 101 열차가 동대구역에서 40분 지연됐습니다.", [], ["KTX 101", "동대구역", "40분"], "PASS"),
    ("문의는 1544-7788 또는 담당자 직통 053-123-4567로 주세요.",
     ["053-123-4567"], ["1544-7788"], "PASS"),
    ("좌석 7호차 12A 였습니다.", ["7호차 12A"], [], "PASS"),
    ("3호차 자유석이 어디인가요?", [], ["3호차"], "PASS"),
    ("환불계좌 110-234-567890 신한은행, 예금주 오세훈입니다.",
     ["110-234-567890", "오세훈"], ["신한은행"], "PASS"),
    ("접수자 즙세빈 / 회신 요청", ["즙세빈"], [], "PASS"),
    ("제 승차권 식별자는 TKT7A9F2B4C8D1E6 입니다.", [], [], "QUARANTINE"),
    ("번호는 0 1 0 2 3 4 5 6 7 8 9 입니다.", [], [], "QUARANTINE"),
    ("메일주소는 gildong dot hong at gmail dot com 입니다.", [], [], "QUARANTINE"),
    ("For further assistance call 1599-7777.", [], ["call", "1599-7777"], "QUARANTINE"),
]


def cmd_selftest(args: argparse.Namespace) -> int:
    masker = RailMasker(mode="label")
    ok = bad = 0
    for text, must_go, must_stay, expect in SELFTEST:
        res = masker.mask(text)
        errs = []
        for s in must_go:
            if s in res.text:
                errs.append(f"미마스킹 '{s}'")
        for s in must_stay:
            if s not in res.text:
                errs.append(f"보존실패 '{s}'")
        if res.status != expect:
            errs.append(f"게이트 {res.status} (기대 {expect})")
        if errs:
            bad += 1
            print(f"✗ {text}")
            print(f"   → {res.text}")
            print(f"   {'; '.join(errs)}")
        else:
            ok += 1
            print(f"✓ {text[:46]}{'…' if len(text) > 46 else ''}")

    # 행 단위 토큰 일관성
    tm = RailMasker(mode="token")
    rec = tm.mask_record({"질문": "저는 김성호입니다. 010-1111-2222",
                          "답변": "김성호 고객님 안내드립니다."}, ("질문", "답변"))
    consistent = "<이름_1>" in rec["질문"] and "<이름_1>" in rec["답변"]
    print(f"{'✓' if consistent else '✗'} 행 단위 토큰 일관성 (같은 사람 → 같은 토큰)")
    ok, bad = (ok + 1, bad) if consistent else (ok, bad + 1)

    print(f"\n{ok}/{ok+bad} 통과")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="pii_mask.py",
        description="열차 민원 개인정보 마스킹 (단일 파일, 외부 의존성 없음)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dummy", help="더미데이터 생성")
    p.add_argument("-n", "--rows", type=int, default=50)
    p.add_argument("-o", "--out", default="dummy_complaints.csv")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_dummy)

    p = sub.add_parser("mask", help="테이블 컬럼별 마스킹")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--quarantine", default=None, help="격리 행 출력 경로")
    p.add_argument("--mapping", default=None, help="매핑표 출력 경로 (분리·암호화 보관)")
    p.add_argument("--id-mode", default="fixed", choices=["fixed", "surrogate", "hash"])
    p.add_argument("--date-mode", default="format",
                   choices=["format", "hour", "date", "month", "keep"])
    p.add_argument("--all-dates", action="store_true", help="접수일·처리기한·완료일도 동일 처리")
    p.add_argument("--salt", default="", help="--id-mode hash 용 솔트 (외부 보관)")
    p.add_argument("--mask-mode", default="token", choices=["label", "token", "partial"])
    p.add_argument("--no-failsafe", action="store_true")
    p.add_argument("--keep-meta", action="store_true", help="_status 등 메타 컬럼 포함")
    p.set_defaults(func=cmd_mask)

    p = sub.add_parser("text", help="본문 한 줄 마스킹 (stdin 또는 인자)")
    p.add_argument("text", nargs="?", default=None)
    p.add_argument("--mask-mode", default="label", choices=["label", "token", "partial"])
    p.add_argument("--no-failsafe", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_text)

    p = sub.add_parser("audit", help="마스킹 결과 잔존 검사 (교차 검증)")
    p.add_argument("input")
    p.add_argument("--columns", default=None, help="검사할 컬럼 (쉼표 구분, 기본 제목·질문·답변)")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("selftest", help="내장 검증")
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
