#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
forecast.py — 민원 예측 모델을 추정해 model.json 으로 내보낸다

개인정보 처리
    입력은 반드시 마스킹을 통과한 파일이어야 한다. 스크립트가 직접 검사하며,
    잔존 개인정보가 발견되면 계산을 거부한다 (--allow-unmasked 로만 우회 가능).
    또한 예측에 쓰는 컬럼은 구조화 메타데이터뿐이고
    제목·질문·답변 본문은 마스킹된 상태로도 읽지 않는다.

모델
    예측(d) = 기준선(d) × 요일계수 × 월계수 × 명절·공휴일계수

      기준선   명절·공휴일 영향권을 뺀 '평시' 일별 건수의 선형 추세.
               데이터 범위 밖(예측 대상)은 이 추세를 외삽한다.
      요일계수 평시 실측 / 기준선 의 요일별 중앙값.
      월계수   평시 실측 / (기준선 × 요일) 의 월별 중앙값.
               관측일이 부족한 달(기본 8일 미만)은 1.00 으로 두고 표시한다.
      공휴일   일반 공휴일 당일은 접수가 줄고(고객센터 휴무),
               다음 영업일에 적체가 풀린다. 두 계수를 실측에서 뽑는다.
      명절     설·추석은 성격이 달라 '상대일 프로파일'을 쓴다.
               명절 당일(D0) 기준 상대일마다 명절 배수를 뽑아 대상 명절에 투영하고,
               예매기 실적비(선행지표)로 수요 증감을 보정한다.

    상대일 구간(D-1~D+2, 연휴 기간)에는 요일계수를 적용하지 않는다.
    명절 당일에는 그날이 일요일이든 금요일이든 사람이 움직이기 때문이다.

출력 model.json — 웹 페이지가 파라미터를 받아 임의 날짜를 직접 계산한다.
(날짜마다 미리 계산해 두는 대신 모델을 넘기므로, 사용자가 어떤 날짜를 골라도 된다)

사용법
    python3 tools/forecast.py -i data/masked_seasonal.csv -o data/model.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
from collections import Counter
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 기준 명절 (실적을 뽑아올 원본) ───────────────────────────────────
ANCHOR_FESTIVAL = {"name": "추석", "dday": date(2026, 9, 25),
                   "range": (date(2026, 9, 24), date(2026, 9, 27))}
REL_FROM, REL_TO = -21, 10
BOOK_ANCHOR = (date(2026, 9, 6), date(2026, 9, 12))     # 추석 예매 국면
BOOK_TARGET = (date(2027, 1, 3), date(2027, 1, 9))      # 설 예매 국면 (선행지표)

# ── 2027년 공휴일 ────────────────────────────────────────────────────
#  근로자의 날(5/1)과 제헌절(7/17)은 법정공휴일이 아니라 제외.
#  현충일(6/6, 일)은 대체공휴일 대상이 아니다.
HOLIDAYS_2027 = [
    ("2027-01-01", "신정"),
    ("2027-02-06", "설날 연휴"), ("2027-02-07", "설날"),
    ("2027-02-08", "설날 연휴"), ("2027-02-09", "대체공휴일(설날)"),
    ("2027-03-01", "삼일절"),
    ("2027-05-05", "어린이날"), ("2027-05-13", "부처님오신날"),
    ("2027-06-06", "현충일"),
    ("2027-08-15", "광복절"), ("2027-08-16", "대체공휴일(광복절)"),
    ("2027-09-14", "추석 연휴"), ("2027-09-15", "추석"), ("2027-09-16", "추석 연휴"),
    ("2027-10-03", "개천절"), ("2027-10-04", "대체공휴일(개천절)"),
    ("2027-10-09", "한글날"), ("2027-10-11", "대체공휴일(한글날)"),
    ("2027-12-25", "성탄절"), ("2027-12-27", "대체공휴일(성탄절)"),
]
# 대상 명절 — 상대일 프로파일을 적용할 구간
FESTIVALS_2027 = [
    {"name": "설",   "dday": "2027-02-07", "range": ["2027-02-06", "2027-02-09"]},
    {"name": "추석", "dday": "2027-09-15", "range": ["2027-09-14", "2027-09-16"]},
]

# 데이터 범위 안의 일반 공휴일 (명절 연휴 제외) — 계수를 실측으로 뽑는다
HOLIDAYS_IN_DATA = [date(2026, 10, 3), date(2026, 10, 9),
                    date(2026, 12, 25), date(2027, 1, 1)]

MONTH_MIN_OBS = 8
SINGLE_SAMPLE_FLOOR = 0.22
HANDLE_PER_PERSON_DAY = 18

# 예측에 쓰는 컬럼 — 본문(제목·질문·답변)은 포함하지 않는다
COLUMNS_USED = ["등록일", "민원유형", "처리부서", "처리기한", "완료일"]

# ── 입력 마스킹 검사 ─────────────────────────────────────────────────
AUDIT = [
    ("휴대전화", re.compile(r"01[016789]-?\d{3,4}-?\d{4}")),
    ("이메일", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+")),
    ("주민등록번호", re.compile(r"\d{6}\s*-\s*[1-8]\d{6}")),
    ("연속숫자7+", re.compile(r"(?<![\d.,])\d{7,}(?![\d.,])")),
    ("IP주소", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")),
    ("계좌형", re.compile(r"\d{3}-\d{2}-\d{6}")),
]
TEXT_COLUMNS = ["제목", "질문", "답변"]


def audit_input(path: str) -> int:
    """본문 컬럼에 잔존 개인정보가 있는지 검사한다. 건수를 돌려준다."""
    hits = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            blob = " ".join(row.get(c, "") or "" for c in TEXT_COLUMNS)
            if not blob.strip():
                continue
            for _, pat in AUDIT:
                hits += len(pat.findall(blob))
    return hits


def load(path: str):
    """예측에 필요한 컬럼만 읽는다. 본문은 메모리에 올리지 않는다."""
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            out.append({c: (row.get(c) or "").strip() for c in COLUMNS_USED})
    return out


def dkey(s): return (s or "")[:10]
def within(d, a, b): return a <= d <= b


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", default=os.path.join(here, "..", "data", "masked_seasonal.csv"))
    ap.add_argument("-o", "--output", default=os.path.join(here, "..", "data", "model.json"))
    ap.add_argument("--allow-unmasked", action="store_true",
                    help="마스킹 검사 실패에도 강행 (개인정보 사고 위험. 권장하지 않음)")
    args = ap.parse_args()

    # ── 0) 개인정보 게이트 ──────────────────────────────────────────
    residuals = audit_input(args.input)
    if residuals and not args.allow_unmasked:
        sys.exit(
            f"\n[중단] 입력 파일의 본문에 개인정보로 보이는 패턴이 {residuals}건 남아 있습니다.\n"
            f"       {args.input}\n"
            f"  예측은 마스킹을 통과한 데이터로만 수행합니다. 먼저 마스킹하세요:\n"
            f"    python3 tools/pii_mask.py mask -i <원본> -o {args.input} \\\n"
            f"        --id-mode surrogate --date-mode hour --all-dates\n"
            f"    python3 tools/pii_mask.py audit {args.input}\n")
    rows = load(args.input)
    if not rows:
        sys.exit("입력이 비어 있습니다.")

    daily = Counter()
    for r in rows:
        k = dkey(r["등록일"])
        if k:
            daily[k] += 1
    days = sorted(daily)
    d0, d1 = date.fromisoformat(days[0]), date.fromisoformat(days[-1])

    af = ANCHOR_FESTIVAL
    anchor_win = (af["dday"] + timedelta(days=REL_FROM), af["dday"] + timedelta(days=REL_TO))
    hol_rels = {(af["range"][0] + timedelta(days=i) - af["dday"]).days
                for i in range((af["range"][1] - af["range"][0]).days + 1)}
    catchup_in_data = set()
    for h in HOLIDAYS_IN_DATA:
        n = h + timedelta(days=1)
        while n.weekday() >= 5 or n in HOLIDAYS_IN_DATA:
            n += timedelta(days=1)
        catchup_in_data.add(n)

    def is_normal(d: date) -> bool:
        """명절·공휴일 영향권 밖 = 기준선·요일·월 계수를 뽑는 모집단"""
        return not (within(d, *anchor_win) or within(d, *BOOK_TARGET)
                    or d in HOLIDAYS_IN_DATA or d in catchup_in_data
                    or within(d, date(2026, 12, 22), date(2027, 1, 2))
                    or within(d, date(2027, 1, 17), date(2027, 1, 23)))

    normal = [(date.fromisoformat(k), v) for k, v in daily.items()
              if is_normal(date.fromisoformat(k))]
    normal.sort()

    # ── 1) 기준선: 평시 일별 건수의 선형 추세 ───────────────────────
    xs = [(d - d0).days for d, _ in normal]
    ys = [v for _, v in normal]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var if var else 0.0
    icept = my - slope * mx
    def base(d: date) -> float:
        return max(1.0, icept + slope * (d - d0).days)

    # ── 2) 요일 계수 ────────────────────────────────────────────────
    by_dow = {}
    for d, v in normal:
        by_dow.setdefault(d.weekday(), []).append(v / base(d))
    DOWF = {w: round(statistics.median(vs), 4) for w, vs in sorted(by_dow.items())}
    dowf = lambda d: DOWF.get(d.weekday(), 1.0)

    # ── 3) 월 계수 (관측 부족한 달은 1.00) ──────────────────────────
    by_m = {}
    for d, v in normal:
        by_m.setdefault(d.month, []).append(v / (base(d) * dowf(d)))
    MONTH = {}
    for m, vs in sorted(by_m.items()):
        enough = len(vs) >= MONTH_MIN_OBS
        MONTH[m] = {"f": round(statistics.median(vs), 4) if enough else 1.0,
                    "raw": round(statistics.median(vs), 4), "n": len(vs), "applied": enough}
    monthf = lambda d: MONTH.get(d.month, {}).get("f", 1.0)

    def expected_plain(d: date, use_dow: bool = True) -> float:
        return base(d) * (dowf(d) if use_dow else 1.0) * monthf(d)

    # ── 4) 공휴일 계수 (실측) ───────────────────────────────────────
    def ratio(dates):
        out = []
        for d in dates:
            k = d.isoformat()
            if k in daily:
                e = expected_plain(d)
                if e > 0:
                    out.append(daily[k] / e)
        return out
    hr, cr = ratio(HOLIDAYS_IN_DATA), ratio(sorted(catchup_in_data))
    HOLF = round(statistics.median(hr), 4) if hr else 1.0
    CATF = round(statistics.median(cr), 4) if cr else 1.0

    # ── 5) 명절 상대일 프로파일 ─────────────────────────────────────
    # 기준 명절의 상대일 구간에 '일반 공휴일'이 끼면 그날의 접수 급감(또는 적체 급증)이
    # 명절 배수로 잘못 잡힌다. 실제로 추석 D+8 은 개천절, D+10 은 신정이 아닌
    # 개천절 다음 영업일이라 둘 다 오염된다.
    # 중앙값 계수로 나눠 보정하면 그 계수 자체의 오차가 끼므로,
    # 오염된 상대일은 값을 버리고 양옆의 깨끗한 값으로 선형 보간한다.
    uplift, actual, contaminated = {}, {}, []
    for rel in range(REL_FROM, REL_TO + 1):
        d = af["dday"] + timedelta(days=rel)
        cnt = daily.get(d.isoformat(), 0)
        dirty = (d in HOLIDAYS_IN_DATA or d in catchup_in_data) and rel not in hol_rels
        actual[rel] = {"date": d.isoformat(), "dow": d.weekday(), "n": cnt,
                       "contaminated": dirty}
        uplift[rel] = cnt / expected_plain(d, rel not in hol_rels)
        if dirty:
            contaminated.append(rel)

    clean = [r for r in range(REL_FROM, REL_TO + 1) if r not in contaminated]
    for rel in contaminated:
        lo = max((r for r in clean if r < rel), default=None)
        hi = min((r for r in clean if r > rel), default=None)
        if lo is None and hi is None:
            continue
        if lo is None:
            uplift[rel] = uplift[hi]
        elif hi is None:
            uplift[rel] = uplift[lo]
        else:
            w = (rel - lo) / (hi - lo)
            uplift[rel] = uplift[lo] * (1 - w) + uplift[hi] * w
    uplift = {k: round(v, 4) for k, v in uplift.items()}

    # ── 6) 선행지표: 예매기 실적비 ──────────────────────────────────
    def win(a, b, fn):
        s, cur = 0.0, a
        while cur <= b:
            s += fn(cur)
            cur += timedelta(days=1)
        return s
    obs = lambda d: daily.get(d.isoformat(), 0)

    def expected_with_holiday(d: date) -> float:
        """공휴일·적체 효과까지 포함한 기대값.

        예매기 창에 공휴일이나 그 다음 영업일이 끼면(설 예매기에 신정 적체일 1/4 포함)
        기대값에서 그 효과를 빼지 않을 경우 공휴일 적체가 '명절 수요 증가'로 잘못 잡힌다.
        """
        e = expected_plain(d)
        if d in HOLIDAYS_IN_DATA:
            return e * HOLF
        if d in catchup_in_data:
            return e * CATF
        return e

    lift_a = win(*BOOK_ANCHOR, obs) / win(*BOOK_ANCHOR, expected_with_holiday)
    lift_t = win(*BOOK_TARGET, obs) / win(*BOOK_TARGET, expected_with_holiday)
    growth = round(lift_t / lift_a, 4) if lift_a else 1.0

    # ── 7) 검증: 평시 구간 재현 오차 ────────────────────────────────
    errs = [abs(v - expected_plain(d)) / v for d, v in normal if v > 0]
    mape = round(statistics.mean(errs) * 100, 1)
    worst = max(((abs(v - expected_plain(d)) / v, d, v) for d, v in normal if v > 0),
                default=(0, d0, 0))
    band = max(SINGLE_SAMPLE_FLOOR, min(0.40, statistics.pstdev(errs) + mape / 100 + 0.05))

    # ── 8) 구성비 (명절 구간 / 평시) ────────────────────────────────
    fest_rows = [r for r in rows if within(date.fromisoformat(dkey(r["등록일"])), *anchor_win)]
    norm_rows = [r for r in rows if is_normal(date.fromisoformat(dkey(r["등록일"])))]
    def mix(rs, key, top=None):
        c = Counter(r[key] for r in rs if r[key])
        t = sum(c.values()) or 1
        items = c.most_common(top)
        return [{"k": k, "n": v, "share": round(v / t * 100, 1)} for k, v in items]
    def overdue(rs):
        done = [r for r in rs if r["완료일"]]
        return round(sum(1 for r in done if dkey(r["완료일"]) > dkey(r["처리기한"]))
                     / len(done) * 100, 1) if done else 0.0
    def openrate(rs):
        return round(sum(1 for r in rs if not r["완료일"]) / (len(rs) or 1) * 100, 1)

    model = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": "기준선 × 요일 × 시기(월) × 명절·공휴일 계수. 명절은 상대일 프로파일 투영. 머신러닝 아님.",
        "privacy": {
            "input": os.path.basename(args.input),
            "auditResiduals": residuals,
            "columnsUsed": COLUMNS_USED,
            "textColumnsRead": False,
            "note": "예측은 마스킹을 통과한 파일만 입력으로 받으며, 제목·질문·답변 본문은 읽지 않는다.",
        },
        "source": {"rows": len(rows), "from": d0.isoformat(), "to": d1.isoformat(),
                   "days": len(days), "normalDays": n},
        "baseline": {"anchor": d0.isoformat(), "intercept": round(icept, 4),
                     "slopePerDay": round(slope, 5),
                     "atStart": round(base(d0), 1), "atEnd": round(base(d1), 1)},
        "dow": {str(k): v for k, v in DOWF.items()},
        "month": {str(k): v for k, v in MONTH.items()},
        "monthMinObs": MONTH_MIN_OBS,
        "publicHoliday": {
            "factor": HOLF, "n": len(hr), "catchupFactor": CATF, "catchupN": len(cr),
            "observed": [d.isoformat() for d in HOLIDAYS_IN_DATA],
            "catchupObserved": sorted(d.isoformat() for d in catchup_in_data),
        },
        "festival": {
            "anchorName": af["name"], "anchorDate": af["dday"].isoformat(),
            "anchorRange": [af["range"][0].isoformat(), af["range"][1].isoformat()],
            "relFrom": REL_FROM, "relTo": REL_TO,
            "holidayRels": sorted(hol_rels),
            "contaminatedRels": contaminated,
            "uplift": {str(k): v for k, v in uplift.items()},
            "actual": {str(k): v for k, v in actual.items()},
            "booking": {
                "anchor": {"window": [x.isoformat() for x in BOOK_ANCHOR],
                           "n": int(win(*BOOK_ANCHOR, obs)), "lift": round(lift_a, 3)},
                "target": {"window": [x.isoformat() for x in BOOK_TARGET],
                           "n": int(win(*BOOK_TARGET, obs)), "lift": round(lift_t, 3)},
                "growth": growth,
                # 선행지표는 '설' 예매기에서만 관측됐다. 2027 추석 예매기(8월)는 데이터 밖이라
                # 추석 예측에는 증감 보정을 적용하지 않는다.
                "targetFestival": "설",
            },
        },
        "holidays": [{"date": d, "name": nm} for d, nm in HOLIDAYS_2027],
        "festivals": FESTIVALS_2027,
        "band": round(band, 3),
        "validation": {"normalMape": mape, "normalDays": n,
                       "worstDate": worst[1].isoformat(),
                       "worstErrPct": round(worst[0] * 100, 1)},
        "mix": {
            "festivalTypes": mix(fest_rows, "민원유형", 12),
            "normalTypes": mix(norm_rows, "민원유형", 12),
            "festivalDepts": mix(fest_rows, "처리부서"),
            "normalDepts": mix(norm_rows, "처리부서"),
        },
        "ops": {
            "festivalOverdue": overdue(fest_rows), "normalOverdue": overdue(norm_rows),
            "festivalOpen": openrate(fest_rows), "normalOpen": openrate(norm_rows),
            "handlePerPersonDay": HANDLE_PER_PERSON_DAY,
        },
        "caveats": [
            f"기준선·요일·월 계수는 평시 {n}일로 추정했다. 평시 구간 재현 오차는 평균 {mape}% 다.",
            "월 계수는 관측일이 8일 미만인 달에는 적용하지 않는다(1.00). 데이터가 9월~1월뿐이라 2월~8월은 모두 여기 해당한다.",
            f"데이터 범위({d0.isoformat()} ~ {d1.isoformat()}) 밖의 기준선은 추세 외삽값이다. 멀어질수록 오차가 커진다.",
            "명절 관측이 추석 1회뿐이라 상대일 프로파일은 계절 평균이 아니라 단일 표본이다. 설 실적이 쌓이면 평균으로 바꿔야 한다.",
            "±구간은 평시 재현 오차에 단일 표본 투영의 불확실성을 더한 바닥값이다. 잔차만으로 계산하면 실제보다 좁게 나온다.",
            "추석 연휴는 4일(D-1~D+2), 2027 추석 연휴는 3일(D-1~D+1)로 길이가 달라 투영 정확도가 떨어진다.",
            "기준 명절 구간에 일반 공휴일이 끼어 오염된 상대일은 값을 버리고 양옆에서 선형 보간했다. 그 구간의 예측은 다른 날보다 근거가 약하다.",
            "예매 오픈일이 바뀌면 D-30 부근 봉우리 위치가 통째로 이동한다. 실제 예매일 확정 후 재계산이 필요하다.",
            "운행중지·폭설·운임 개정 같은 외생 변수는 반영하지 않는다.",
        ],
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=1)

    print(f"{args.output} 생성")
    print(f"  개인정보 검사   본문 잔존 {residuals}건 · 읽은 컬럼 {', '.join(COLUMNS_USED)}")
    print(f"  기준선          {base(d0):.1f} → {base(d1):.1f} 건/일 (평시 {n}일)")
    print(f"  요일 계수       " + " ".join(f"{'월화수목금토일'[k]}{v:.2f}" for k, v in DOWF.items()))
    print(f"  월 계수         " + " ".join(
        f"{m}월 {v['f']:.2f}{'' if v['applied'] else '(미적용)'}" for m, v in MONTH.items()))
    print(f"  공휴일 계수     당일 {HOLF:.2f} ({len(hr)}일 관측) · 다음영업일 {CATF:.2f} ({len(cr)}일)")
    print(f"  명절 배수       D0 {uplift[0]:.2f}배 · 최대 {max(uplift.values()):.2f}배")
    print(f"  예매기 선행     추석 lift {lift_a:.2f} → 설 lift {lift_t:.2f} ⇒ ×{growth}")
    print(f"  검증            평시 재현 오차 평균 {mape}% · 최악 {worst[1]} {worst[0]*100:.0f}%")
    print(f"  구간            ±{band*100:.0f}%")


if __name__ == "__main__":
    main()
