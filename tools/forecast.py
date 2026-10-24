#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
forecast.py — 추석 실적으로 설 연휴 민원을 예측한다

방법: 상대일 프로파일 투영 (rule-based profile projection). 머신러닝이 아니다.

  1) 평시 기준선   명절 영향권 밖 날짜로 일평균의 선형 추세를 잡는다.
                   설 시점(데이터 범위 밖)은 이 추세를 외삽한다.
  2) 추석 프로파일  추석 당일 기준 상대일(D-21~D+10)마다
                   uplift[D] = 추석 당일 건수 / 그날의 평시 기준선
  3) 선행지표 보정  설 예매기(1월) 실적 ÷ 추석 예매기(9월) 실적 = 수요 증감률.
                   예매기는 연휴 본행사의 선행지표다.
  4) 요일 보정      추석 당일은 금요일, 설 당일은 일요일이라 상대일의 요일이 2일 어긋난다.
                   연휴 바깥 구간에만 요일 계수비를 곱해 보정한다.
                   (연휴 중에는 요일 효과가 사실상 사라지므로 보정하지 않는다)
  5) 구간           추석 기간 일별 잔차의 변동계수로 ±구간을 만든다.

출력 forecast.json — 조회 콘솔이 그대로 읽어 그린다.

사용법
    python3 forecast.py -i masked_seasonal.csv -o forecast.json
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

CHUSEOK = date(2026, 9, 25)
CHUSEOK_HOLIDAY = (date(2026, 9, 24), date(2026, 9, 27))
SEOLLAL = date(2027, 2, 7)
SEOLLAL_HOLIDAY = (date(2027, 2, 6), date(2027, 2, 9))

REL_FROM, REL_TO = -21, 10                 # 프로파일 범위
CHUSEOK_WINDOW = (CHUSEOK + timedelta(days=REL_FROM), CHUSEOK + timedelta(days=REL_TO))
BOOK_CHUSEOK = (date(2026, 9, 6), date(2026, 9, 12))    # 추석 예매 국면
BOOK_SEOLLAL = (date(2027, 1, 3), date(2027, 1, 9))     # 설 예매 국면
HANDLE_PER_PERSON_DAY = 18                 # 1인 1일 처리 가능 건수(가정)


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def dkey(s):
    return (s or "")[:10]


def in_win(d, win):
    return win[0] <= d <= win[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", default="masked_seasonal.csv")
    ap.add_argument("-o", "--output", default="forecast.json")
    args = ap.parse_args()

    rows = load(args.input)
    daily = Counter()
    for r in rows:
        k = dkey(r.get("등록일"))
        if k:
            daily[k] += 1
    days = sorted(daily)
    d0, d1 = date.fromisoformat(days[0]), date.fromisoformat(days[-1])

    # ── 1) 평시 기준선: 명절 영향권 제외 후 선형 추세 ───────────────
    def is_normal(d):
        return not (in_win(d, CHUSEOK_WINDOW) or in_win(d, BOOK_SEOLLAL)
                    or in_win(d, (date(2026, 12, 22), date(2027, 1, 2)))
                    or in_win(d, (date(2027, 1, 17), date(2027, 1, 23))))

    pts = [((date.fromisoformat(k) - d0).days, v) for k, v in daily.items()
           if is_normal(date.fromisoformat(k))]
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    var = sum((p[0] - mx) ** 2 for p in pts)
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / var if var else 0.0
    icept = my - slope * mx

    def baseline(d):
        """요일 무관 평시 일평균(추세 반영). 설 시점은 외삽."""
        return max(1.0, icept + slope * (d - d0).days)

    # 요일 계수: 평시 실측 / 기준선
    dow_ratio = defaultdict(list)
    for k, v in daily.items():
        d = date.fromisoformat(k)
        if is_normal(d):
            dow_ratio[d.weekday()].append(v / baseline(d))
    DOWF = {w: statistics.median(vs) for w, vs in sorted(dow_ratio.items())}

    # ── 2) 추석 상대일 프로파일 ─────────────────────────────────────
    # 연휴 안에서는 요일 효과가 사라지므로 요일 계수를 빼고 보고,
    # 연휴 밖에서는 요일 계수를 기대값에 포함시켜 uplift 를 '순수 명절 배수'로 만든다.
    # 이렇게 분해해야 요일이 2일 어긋나는 설에 투영할 때 보정 계수가 따로 필요 없다.
    hol_rel = {(CHUSEOK_HOLIDAY[0] + timedelta(days=i) - CHUSEOK).days
               for i in range((CHUSEOK_HOLIDAY[1] - CHUSEOK_HOLIDAY[0]).days + 1)}

    def expected(d, rel):
        e = baseline(d)
        return e if rel in hol_rel else e * DOWF.get(d.weekday(), 1.0)

    uplift, chuseok_actual = {}, {}
    for rel in range(REL_FROM, REL_TO + 1):
        d = CHUSEOK + timedelta(days=rel)
        cnt = daily.get(d.isoformat(), 0)
        chuseok_actual[rel] = {"date": d.isoformat(), "dow": d.weekday(), "n": cnt}
        uplift[rel] = cnt / expected(d, rel)

    # ── 3) 선행지표: 예매기 실적 비교 ───────────────────────────────
    def win_sum(win):
        s, cur = 0, win[0]
        while cur <= win[1]:
            s += daily.get(cur.isoformat(), 0)
            cur += timedelta(days=1)
        return s
    book_c, book_s = win_sum(BOOK_CHUSEOK), win_sum(BOOK_SEOLLAL)
    # 예매기 사이의 평시 성장분은 기준선이 이미 흡수하므로 기준선 대비로 비교한다
    def win_base(win):
        s, cur = 0.0, win[0]
        while cur <= win[1]:
            s += baseline(cur)
            cur += timedelta(days=1)
        return s
    lift_c = book_c / win_base(BOOK_CHUSEOK)
    lift_s = book_s / win_base(BOOK_SEOLLAL)
    growth = lift_s / lift_c if lift_c else 1.0

    # ── 5) 구간: 추석 기간 잔차 변동계수 ────────────────────────────
    res = []
    for rel in range(REL_FROM, REL_TO + 1):
        d = CHUSEOK + timedelta(days=rel)
        exp = expected(d, rel) * uplift[rel]
        if exp > 0:
            res.append(daily.get(d.isoformat(), 0) / exp)
    # 프로파일을 추석 데이터 자체에서 뽑았으므로 잔차는 노이즈만 남아 과소평가된다.
    # 명절 관측이 1회뿐인 투영의 실제 불확실성은 그보다 훨씬 크므로 바닥값을 둔다.
    cv = statistics.pstdev(res) if len(res) > 1 else 0.12
    SINGLE_SAMPLE_FLOOR = 0.22
    band = max(SINGLE_SAMPLE_FLOOR, min(0.40, cv + 0.08))

    # ── 4) 설 예측 ──────────────────────────────────────────────────
    fc = []
    for rel in range(REL_FROM, REL_TO + 1):
        src = CHUSEOK + timedelta(days=rel)
        tgt = SEOLLAL + timedelta(days=rel)
        # 연휴 상대일은 설 연휴 날짜에 맞춰 판정한다 (요일 계수 적용 여부가 갈린다)
        is_hol = SEOLLAL_HOLIDAY[0] <= tgt <= SEOLLAL_HOLIDAY[1]
        exp = baseline(tgt) * (1.0 if is_hol else DOWF.get(tgt.weekday(), 1.0))
        pred = exp * uplift[rel] * growth
        fc.append({
            "rel": rel, "date": tgt.isoformat(), "dow": tgt.weekday(),
            "pred": round(pred), "lo": round(pred * (1 - band)), "hi": round(pred * (1 + band)),
            "holiday": is_hol, "srcDate": src.isoformat(), "srcDow": src.weekday(),
            "base": round(exp, 1), "uplift": round(uplift[rel], 2),
        })

    peak = max(fc, key=lambda x: x["pred"])
    total_pred = sum(x["pred"] for x in fc)
    total_chuseok = sum(v["n"] for v in chuseok_actual.values())

    # ── 유형·부서 구성 (추석 영향권 실적 → 설에 투영) ───────────────
    hol_rows = [r for r in rows if in_win(date.fromisoformat(dkey(r["등록일"])), CHUSEOK_WINDOW)]
    tcnt = Counter(r["민원유형"] for r in hol_rows)
    dcnt = Counter(r["처리부서"] for r in hol_rows)
    nhol = len(hol_rows) or 1
    normal_rows = [r for r in rows if is_normal(date.fromisoformat(dkey(r["등록일"])))]
    ncnt = Counter(r["민원유형"] for r in normal_rows)
    nnorm = len(normal_rows) or 1

    types = []
    for t, c in tcnt.most_common(12):
        share = c / nhol
        nshare = ncnt.get(t, 0) / nnorm
        types.append({"type": t, "chuseok": c, "share": round(share * 100, 1),
                      "normalShare": round(nshare * 100, 1),
                      "pred": round(total_pred * share),
                      "ratio": round(share / nshare, 1) if nshare > 0 else None})
    depts = [{"dept": d, "pred": round(total_pred * c / nhol), "share": round(c / nhol * 100, 1)}
             for d, c in dcnt.most_common()]

    # 추석 기간 처리 지표 (설에 같은 일이 반복될 거라는 근거)
    def overdue_rate(rs):
        done = [r for r in rs if r["완료일"]]
        if not done:
            return 0.0
        return sum(1 for r in done if dkey(r["완료일"]) > dkey(r["처리기한"])) / len(done) * 100
    def open_rate(rs):
        return sum(1 for r in rs if not r["완료일"]) / (len(rs) or 1) * 100

    peak_mult = peak["pred"] / baseline(date.fromisoformat(peak["date"]))
    out = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": "상대일 프로파일 투영 (추석 실적 → 설 예측). 머신러닝 아님.",
        "source": {
            "file": args.input, "rows": len(rows),
            "from": d0.isoformat(), "to": d1.isoformat(),
        },
        "anchors": {
            "chuseok": CHUSEOK.isoformat(), "chuseokHoliday": [x.isoformat() for x in CHUSEOK_HOLIDAY],
            "seollal": SEOLLAL.isoformat(), "seollalHoliday": [x.isoformat() for x in SEOLLAL_HOLIDAY],
        },
        "baseline": {
            "atChuseok": round(baseline(CHUSEOK), 1), "atSeollal": round(baseline(SEOLLAL), 1),
            "slopePerDay": round(slope, 3),
            "dowFactor": {str(k): round(v, 2) for k, v in DOWF.items()},
        },
        "leadIndicator": {
            "chuseokBooking": {"window": [x.isoformat() for x in BOOK_CHUSEOK], "n": book_c, "lift": round(lift_c, 2)},
            "seollalBooking": {"window": [x.isoformat() for x in BOOK_SEOLLAL], "n": book_s, "lift": round(lift_s, 2)},
            "growth": round(growth, 3),
        },
        "band": round(band, 3),
        "chuseokActual": chuseok_actual,
        "forecast": fc,
        "summary": {
            "totalChuseok": total_chuseok,
            "totalPred": total_pred,
            "deltaPct": round((total_pred / total_chuseok - 1) * 100, 1) if total_chuseok else None,
            "peakDate": peak["date"], "peakPred": peak["pred"],
            "peakLo": peak["lo"], "peakHi": peak["hi"],
            "peakMultiple": round(peak_mult, 1),
            "staffNeeded": -(-peak["pred"] // HANDLE_PER_PERSON_DAY),
            "handlePerPersonDay": HANDLE_PER_PERSON_DAY,
        },
        "chuseokOps": {
            "overdueRate": round(overdue_rate(hol_rows), 1),
            "openRate": round(open_rate(hol_rows), 1),
            "normalOverdueRate": round(overdue_rate(normal_rows), 1),
            "normalOpenRate": round(open_rate(normal_rows), 1),
        },
        "types": types,
        "depts": depts,
        "caveats": [
            "명절 관측이 추석 1회뿐이라 계절 평균이 아니라 단일 표본이다. 설 실적이 쌓이면 프로파일을 평균으로 바꿔야 한다.",
            "±구간은 잔차에서 계산한 값이 아니라 단일 표본 투영에 두는 바닥값이다. 프로파일을 추석 데이터 자체에서 뽑았으므로 잔차 기반 구간은 실제보다 좁게 나온다.",
            "추석 당일은 금요일, 설 당일은 일요일이라 상대일의 요일이 2일 어긋난다. 연휴 바깥 구간만 요일 계수로 보정했다.",
            "설 시점 평시 기준선은 데이터 범위 밖이라 추세 외삽값이다.",
            "운행중지·폭설·운임 개정 같은 외생 변수는 반영하지 않는다.",
            "예매 오픈일이 바뀌면 D-30 부근 봉우리 위치가 통째로 이동한다. 실제 예매일 확정 후 재계산이 필요하다.",
        ],
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    s = out["summary"]
    print(f"{args.output} 생성")
    print(f"  평시 기준선   추석 {out['baseline']['atChuseok']}건/일 → 설 {out['baseline']['atSeollal']}건/일")
    print(f"  예매기 선행   추석 {book_c:,}건(lift {lift_c:.2f}) → 설 {book_s:,}건(lift {lift_s:.2f}) ⇒ 증감률 {growth:.3f}")
    print(f"  추석 실적     {s['totalChuseok']:,}건 (D{REL_FROM}~D+{REL_TO})")
    print(f"  설 예측       {s['totalPred']:,}건 ({s['deltaPct']:+.1f}%)  구간 ±{band*100:.0f}%")
    print(f"  피크          {s['peakDate']} {s['peakPred']:,}건 (평시 대비 {s['peakMultiple']}배, "
          f"{s['peakLo']:,}~{s['peakHi']:,})")
    print(f"  필요 인력     피크일 {s['staffNeeded']}명 (1인 {HANDLE_PER_PERSON_DAY}건/일 가정)")
    print(f"  추석 기한초과 {out['chuseokOps']['overdueRate']}% (평시 {out['chuseokOps']['normalOverdueRate']}%)")


if __name__ == "__main__":
    main()
