'use strict';
/* suggest.js — 조회 조건 추천 (규칙 기반)
 *
 * 왜 규칙인가
 *   이 페이지는 GitHub Pages 에서 정적으로 돈다. API 키를 여기에 적으면
 *   소스보기로 누구나 가져간다. 그래서 공개 페이지는 키가 필요 없는
 *   규칙으로 돌리고, 사내 서버 모드에서만 Claude API 를 쓴다.
 *   돌려주는 모양은 서버의 /api/suggest-query 와 같게 맞췄다.
 *
 * 무엇을 보는가
 *   이미 브라우저에 올라온 마스킹 데이터의 집계뿐이다. 본문은 읽지 않는다.
 *   급증 구간은 달력에 적어 둔 명절 날짜가 아니라 일별 접수량에서 찾는다.
 *   데이터가 바뀌면 추천도 따라 바뀐다.
 */

/* ── 집계 ─────────────────────────────────────────────────────────── */
function sgAgg(DATA){
  const daily = new Map();
  const ax = { '민원유형': new Map(), '처리부서': new Map(), '유입경로': new Map() };
  for (const r of DATA){
    const d = day(r['등록일']);
    if (!d) continue;
    daily.set(d, (daily.get(d) || 0) + 1);
    for (const k in ax){
      const v = r[k];
      if (v) ax[k].set(v, (ax[k].get(v) || 0) + 1);
    }
  }
  const days = [...daily.keys()].sort();
  const srt = days.map(d => daily.get(d)).sort((a, b) => a - b);
  // 평균이 아니라 중앙값을 기준선으로 쓴다. 명절 피크가 평균을 끌어올려
  // "급증"의 기준 자체가 올라가 버리기 때문이다.
  const med = srt.length ? srt[Math.floor(srt.length / 2)] : 0;
  return { daily, days, med, ax, lo: days[0] || '', hi: days[days.length - 1] || '' };
}

/* ── 급증 구간 ────────────────────────────────────────────────────── */
/* 중앙값의 1.3 배를 넘는 날들의 연속 구간을 모아, 접수량 합이 가장 큰 것을
   고른다. 길이를 미리 정하지 않으므로 사흘짜리 급증도 2주짜리도 그대로 잡힌다. */
function sgRuns(agg, lo, hi){
  const t = agg.med * 1.3, runs = [];
  let cur = null;
  for (const d of agg.days){
    if (lo && d < lo) continue;
    if (hi && d > hi) continue;
    const n = agg.daily.get(d);
    if (n > t){
      if (!cur) cur = { from: d, to: d, sum: 0, len: 0 };
      cur.to = d; cur.sum += n; cur.len++;
    } else if (cur){ runs.push(cur); cur = null; }
  }
  if (cur) runs.push(cur);
  runs.sort((a, b) => b.sum - a.sum);
  return runs;
}

/* ── 기간 ─────────────────────────────────────────────────────────── */
/* '원인'·'이슈' 같은 말은 단서가 약해 넣지 않았다. 그것만으로 구간을 좁히면
   "전체 현황 원인 분석" 같은 문장에서 엉뚱하게 피크만 잡힌다. */
const SG_PEAK = /급증|피크|몰리|몰렸|많았|폭증|집중|쏠림|늘어|늘었|증가|치솟|명절|추석|설날|설\s|연휴|귀성|귀경/;
const SG_PART = { '초': [1, 10], '상순': [1, 10], '중순': [11, 20], '중반': [11, 20],
                  '말': [21, 31], '하순': [21, 31] };

/* 한국어 조사. 받침 유무로 갈린다. 이게 틀리면 읽는 사람이 바로 걸린다. */
function sgJo(w, kind){
  const c = (w || '').charCodeAt((w || '').length - 1);
  const h = c >= 0xAC00 && c <= 0xD7A3;
  const jong = h ? (c - 0xAC00) % 28 : -1;          // 0 = 받침 없음
  if (kind === '은') return jong === 0 || !h ? '는' : '은';
  if (kind === '을') return jong === 0 || !h ? '를' : '을';
  return jong === 0 || jong === 8 || !h ? '로' : '으로';   // 8 = ㄹ
}

function sgPeriod(goal, agg){
  const notes = [];
  // 데이터 범위 밖을 가리키면 잘라야 하는데, 완전히 밖이면 자르다가
  // 시작>종료 가 되어 조회가 0건이 된다. 그때는 전체 기간으로 되돌린다.
  const clamp = (a, b) => {
    if (b < agg.lo || a > agg.hi){
      notes.push(`요청한 기간이 데이터(${agg.lo}~${agg.hi}) 밖이라 전체 기간으로 두었습니다`);
      return [agg.lo, agg.hi];
    }
    const r = [a < agg.lo ? agg.lo : a, b > agg.hi ? agg.hi : b];
    if (r[0] !== a || r[1] !== b) notes.push(`데이터가 있는 ${agg.lo}~${agg.hi} 안으로 잘랐습니다`);
    return r;
  };

  // 1) 연-월이 적혀 있으면 그 달
  let m = goal.match(/(20\d{2})\s*[-.\/년]\s*(\d{1,2})\s*월?/);
  if (!m){
    // 2) "9월" 처럼 달만 적힌 경우 — 데이터에 그 달이 있는 해를 쓴다
    const mm = goal.match(/(^|[^\d])(\d{1,2})\s*월/);
    if (mm){
      const mo = String(+mm[2]).padStart(2, '0');
      const yr = [...new Set(agg.days.map(d => d.slice(0, 4)))]
        .filter(y => agg.days.some(d => d.startsWith(`${y}-${mo}`)))
        .sort((a, b) => sgCount(agg, `${b}-${mo}`) - sgCount(agg, `${a}-${mo}`))[0];
      if (yr) m = [null, yr, mm[2]];
      else notes.push(`${+mm[2]}월 데이터가 없어 전체 기간으로 두었습니다`);
    }
  }
  if (m){
    const y = m[1], mo = String(+m[2]).padStart(2, '0');
    const last = new Date(Date.UTC(+y, +mo, 0)).getUTCDate();
    let d1 = 1, d2 = last, part = '';
    // "1월 초", "9월 말" — 달 전체가 아니라 그 열흘
    const pm = goal.match(new RegExp(`${+m[2]}\\s*월\\s*(초|상순|중순|중반|말|하순)`));
    if (pm){ part = pm[1]; [d1, d2] = SG_PART[part]; if (d2 > last) d2 = last; }
    const pad = n => String(n).padStart(2, '0');
    let [a, b] = clamp(`${y}-${mo}-${pad(d1)}`, `${y}-${mo}-${pad(d2)}`);
    if (part){
      const lab = `${+m[2]}월 ${part}`;
      notes.push(`'${lab}'${sgJo(part, '을')} ${+m[2]}/${d1}~${d2} 로 잡았습니다`);
    }
    // 그 안에서 급증을 묻는다면 구간을 더 좁힌다
    if (SG_PEAK.test(goal)){
      const r = sgRuns(agg, a, b)[0];
      if (r && !(r.from === a && r.to === b)){
        notes.push(`접수량이 몰린 ${r.len}일 구간으로 더 좁혔습니다`);
        return { from: r.from, to: r.to, notes };
      }
    }
    return { from: a, to: b, notes };
  }

  // 3) 분기
  const q = goal.match(/([1-4])\s*분기/);
  if (q){
    const s = (+q[1] - 1) * 3 + 1;
    const yr = agg.lo.slice(0, 4);
    const last = new Date(Date.UTC(+yr, s + 2, 0)).getUTCDate();
    const [a, b] = clamp(`${yr}-${String(s).padStart(2, '0')}-01`,
                         `${yr}-${String(s + 2).padStart(2, '0')}-${last}`);
    return { from: a, to: b, notes };
  }

  // 4) 최근 N일 / N주 / N개월
  const rc = goal.match(/(?:최근|지난)\s*(\d+)\s*(일|주|개월|달)/);
  if (rc){
    const unit = { '일': 1, '주': 7, '개월': 30, '달': 30 }[rc[2]];
    const end = new Date(agg.hi + 'T00:00:00Z');
    const st = new Date(end); st.setUTCDate(st.getUTCDate() - (+rc[1] * unit - 1));
    const [a, b] = clamp(st.toISOString().slice(0, 10), agg.hi);
    return { from: a, to: b, notes };
  }

  // 5) 급증·명절을 묻는다면 데이터에서 찾은 최대 구간
  if (SG_PEAK.test(goal)){
    const r = sgRuns(agg)[0];
    if (r){
      notes.push('달력이 아니라 일별 접수량에서 찾은 구간입니다');
      return { from: r.from, to: r.to, notes };
    }
  }

  // 6) 아무 단서가 없으면 전체
  return { from: agg.lo, to: agg.hi, notes };
}

function sgCount(agg, prefix){
  let n = 0;
  for (const d of agg.days) if (d.startsWith(prefix)) n += agg.daily.get(d);
  return n;
}

/* ── 유형·부서·경로 ──────────────────────────────────────────────── */
/* 목적 문장에 쓰인 말을 실제 축 값에 맞춘다. 값이 그대로 들어 있는 경우와,
   일상어로 쓴 경우(앱→앱오류, 짐→명절수하물) 둘 다 본다. */
const SG_SYN = {
  '앱': ['앱오류', '모바일앱'], '어플': ['앱오류'], '모바일': ['모바일앱'], '오류': ['앱오류'],
  '지연': ['지연보상', '귀성지연'], '보상': ['지연보상'],
  '환불': ['환불', '반환위약금'], '위약금': ['반환위약금'], '취소': ['반환위약금', '환불'],
  '예매': ['귀성표예매', '예약발권'], '발권': ['예약발권'], '매표': ['귀성표예매'],
  '분실': ['분실물'], '유실': ['분실물'], '잃': ['분실물'],
  '혼잡': ['입석혼잡', '역사혼잡'], '입석': ['입석혼잡'], '붐빔': ['입석혼잡'],
  '수하물': ['명절수하물'], '짐': ['명절수하물'], '화물': ['명절수하물'],
  '할인': ['할인적용'], '우대': ['경로우대'], '노인': ['경로우대'], '고령': ['경로우대'],
  '단체': ['단체', '단체귀성', '단체운송팀'],
  '응대': ['직원응대'], '직원': ['직원응대'], '친절': ['직원응대'], '불친절': ['직원응대'],
  '임시': ['임시열차'], '증편': ['임시열차'],
  '주차': ['주차'], '시설': ['시설', '시설관리팀'], '청결': ['시설'],
  '콜센터': ['고객센터'], '전화': ['고객센터'], '상담': ['고객센터'],
  '홈페이지': ['홈페이지'], '웹': ['홈페이지'], '신문고': ['국민신문고'],
  '민원서류': ['국민신문고'], '메일': ['이메일'], '창구': ['역사접수'], '역무': ['역무팀'],
};

/* skip: 앞선 축이 이미 써 버린 일상어. "앱 오류" 의 '앱' 은 유형(앱오류)과
   경로(모바일앱) 양쪽에 걸리는데, 둘 다 걸면 앱으로 접수된 건만 남아
   정작 보려던 앱오류 민원의 대부분이 빠진다. 구체적인 축이 먼저 가져간다. */
function sgAxis(goal, key, agg, rows, skip){
  const vals = [...agg.ax[key].keys()];
  const hit = new Set(), via = new Map();
  for (const v of vals) if (goal.includes(v)) hit.add(v);
  for (const k in SG_SYN){
    if (!goal.includes(k) || (skip && skip.has(k))) continue;
    for (const v of SG_SYN[k]){
      if (!agg.ax[key].has(v)) continue;
      hit.add(v);
      if (!via.has(v)) via.set(v, []);
      via.get(v).push(k);
    }
  }
  if (!hit.size) return { value: null, note: null, keys: [] };

  // 기간 안에서 실제로 몇 건인지로 고른다. 전체 기간 기준으로 고르면
  // 그 기간에는 거의 없는 값이 뽑혀 조회 결과가 비는 일이 생긴다.
  const cnt = v => rows.reduce((a, r) => a + (r[key] === v ? 1 : 0), 0);
  const cand = [...hit].map(v => [v, cnt(v)]).sort((a, b) => b[1] - a[1]);
  const tot = cand.reduce((a, c) => a + c[1], 0);
  if (!tot) return { value: null, keys: [],
                     note: `이 기간에 해당하는 ${key} 값이 없어 전체로 두었습니다` };
  if (cand.length > 1 && cand[0][1] / tot < 0.55)
    return { value: null, keys: [],
             note: `${key} 후보가 갈려 전체로 두었습니다 — ${cand.map(c => c[0]).join(', ')}` };
  const v = cand[0][0];
  let note = null;
  if (cand.length > 1)
    note = `${key}${sgJo(key, '은')} ${v}${sgJo(v, '로')} 두었습니다 — `
         + `${cand.slice(1).map(c => c[0]).join(', ')}도 후보였습니다`;
  return { value: v, note, keys: via.get(v) || [] };
}

/* ── 본체 ─────────────────────────────────────────────────────────── */
function sgSuggest(goal, DATA){
  goal = (goal || '').trim();
  if (!goal) throw new Error('목적을 한 줄로 적어 주세요. 예: 추석 기간 민원 급증 원인 보고서');
  if (!DATA || !DATA.length) throw new Error('데이터가 아직 올라오지 않았습니다.');

  const agg = sgAgg(DATA);
  const per = sgPeriod(goal, agg);
  const notes = per.notes.slice();

  const rows = DATA.filter(r => {
    const d = day(r['등록일']);
    return d >= per.from && d <= per.to;
  });

  // 구체적인 축이 먼저 고른다 — 유형 → 부서 → 경로
  const used = new Set();
  const type = sgAxis(goal, '민원유형', agg, rows, used);
  type.keys.forEach(k => used.add(k));
  const dept = sgAxis(goal, '처리부서', agg, rows, used);
  dept.keys.forEach(k => used.add(k));
  const ch   = sgAxis(goal, '유입경로', agg, rows, used);
  for (const a of [type, dept, ch]) if (a.note) notes.push(a.note);

  // 처리상태 — '지연'은 유형(지연보상)이라 상태로 보지 않는다
  let stat = '';
  if (/기한\s*초과|기한을?\s*넘|미처리|처리\s*안|밀린|지체/.test(goal)) stat = 'over';
  else if (/미완료|진행\s*중|처리\s*중/.test(goal)) stat = 'open';
  else if (/완료된|처리\s*완료|종결/.test(goal)) stat = 'done';

  // 키워드는 따옴표로 분명히 적었을 때만 건다. 본문 검색은 결과를 크게
  // 줄여서, 짐작으로 넣으면 0건이 되기 쉽다.
  const kw = goal.match(/[「"'“]([^」"'”]{2,20})[」"'”]/);
  const q = kw ? kw[1] : '';
  if (kw) notes.push(`따옴표 안의 말을 키워드로 넣었습니다`);

  return {
    from: per.from, to: per.to,
    type: type.value, dept: dept.value, ch: ch.value, stat, q,
    reason: sgReason(agg, rows, per, { type: type.value, dept: dept.value, ch: ch.value }),
    notes,
  };
}

function sgReason(agg, rows, per, sel){
  const dcnt = new Map();
  for (const r of rows){
    const d = day(r['등록일']);
    dcnt.set(d, (dcnt.get(d) || 0) + 1);
  }
  const nd = dcnt.size || 1;
  const avg = rows.length / nd;
  const lift = agg.med ? avg / agg.med : 0;
  const whole = per.from === agg.lo && per.to === agg.hi;

  // 기간과 건수는 화면이 이미 보여 준다. 여기서는 그걸로 알 수 없는 것만 쓴다.
  const p = [];
  if (!whole) p.push(`일평균 ${avg.toFixed(1)}건`);
  if (!whole && lift >= 1.25) p.push(`전체 일별 중앙값 ${nf(agg.med)}건의 ${lift.toFixed(2)}배`);
  else if (!whole && lift <= 0.8) p.push(`중앙값의 ${lift.toFixed(2)}배로 오히려 한산한 구간`);

  // 고른 축이 없으면 이 기간에서 가장 두드러진 유형을 알려준다
  if (!sel.type){
    const t = new Map();
    for (const r of rows) t.set(r['민원유형'], (t.get(r['민원유형']) || 0) + 1);
    const top = [...t].sort((a, b) => b[1] - a[1])[0];
    if (top && rows.length)
      p.push(`가장 많은 유형은 ${top[0]} ${nf(top[1])}건(${(top[1] / rows.length * 100).toFixed(1)}%)`);
  } else {
    const n = rows.reduce((a, r) => a + (r['민원유형'] === sel.type ? 1 : 0), 0);
    p.push(`${sel.type}${sgJo(sel.type, '은')} 이 기간 민원의 `
         + `${(n / (rows.length || 1) * 100).toFixed(1)}%`);
  }
  return p.join(' · ') + '.';
}
