/* analysis.js — 민원 분석 보기 (index.html#analysis)
 *
 *   언제 몰렸나 · 무엇이 몰렸나 · 누가 받았나 · 어디로 들어왔나 · 무슨 말이 늘었나.
 *   건수·처리상태·시간대·유형 상위(조회 보기)와 예측·재현오차(예측 페이지)는 반복하지 않는다.
 *   급증 기준은 suggest.js 의 sgAgg / sgRuns 를 그대로 쓴다. 본문은 제목만 읽는다.
 */
(function(){
'use strict';
let DATA = [], AGG = null, SEL = null, CUR = null;
const MIN_CELL = 5;
const p1 = v => (v * 100).toFixed(1) + '%';
const md = s => `${+s.slice(5, 7)}/${+s.slice(8, 10)}`;
const small = n => n < MIN_CELL ? '&lt;5' : nf(n);
function count(rows, key){
  const m = new Map();
  for (const r of rows){ const v = r[key]; if (v) m.set(v, (m.get(v) || 0) + 1); }
  return m;
}
const overRate = rows => rows.length ? rows.filter(r => status(r)[1] === 'over').length / rows.length : 0;
function addDays(s, n){
  const d = new Date(s + 'T00:00:00'); d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/* ── 계산 ─────────────────────────────────────────────────────────
   급증 기준은 suggest.js 의 sgAgg / sgRuns 를 그대로 쓴다.
   중앙값은 범위와 무관하게 전체 데이터로 잡는다 — 기간을 좁힐 때마다
   기준이 흔들리면 같은 날이 급증이었다가 아니었다가 한다. */
function compute(a, b){
  const rows = DATA.filter(r => { const d = day(r['등록일']); return d >= a && d <= b; });
  const runs = sgRuns(AGG, a, b).filter(r => r.len >= 2)
    .sort((x, y) => x.from < y.from ? -1 : 1);
  const inRun = d => runs.some(r => d >= r.from && d <= r.to);
  const runRows = [], calmRows = [];
  for (const r of rows) (inRun(day(r['등록일'])) ? runRows : calmRows).push(r);
  const days = AGG.days.filter(d => d >= a && d <= b);
  return { a, b, rows, runs, inRun, runRows, calmRows, days };
}

/* ── 요약 지표 ──────────────────────────────────────────────────────
   읽는 사람이 가장 먼저 봐야 할 다섯 가지를 위에 올린다.
   값 하나만 두면 좋은지 나쁜지 알 수 없으므로 평시(급증 구간을 뺀 날)
   대비 차이를 함께 적는다. 평시가 비교 기준이고, 색은 그 차이가
   나쁜 쪽일 때만 쓴다. */
function doneRate(rows){
  return rows.length ? rows.filter(r => r['완료일']).length / rows.length : 0;
}
function avgDays(rows){
  let s = 0, n = 0;
  for (const r of rows){
    if (!r['완료일']) continue;
    const t = (new Date(day(r['완료일']) + 'T00:00:00') - new Date(day(r['등록일']) + 'T00:00:00')) / 86400000;
    if (t >= 0){ s += t; n++; }
  }
  return n ? s / n : 0;
}
function renderKpi(C){
  const host = $('an-kpis');
  if (!C.rows.length){ host.innerHTML = ''; return; }

  const runDays = C.runs.reduce((s, r) => s + r.len, 0);
  const calmDays = Math.max(1, C.days.length - runDays);
  const perDay = C.rows.length / C.days.length;
  const calmPerDay = C.calmRows.length / calmDays;

  // 델타는 '평시 대비'다. tone 은 나쁜 쪽일 때만 붙인다.
  const d = (v, tone) => v ? `<em class="${tone}">${v}</em>` : '';
  const pp = x => (x >= 0 ? '+' : '−') + Math.abs(x).toFixed(1) + '%p';
  const over = overRate(C.rows), overCalm = overRate(C.calmRows);
  const done = doneRate(C.rows), doneCalm = doneRate(C.calmRows);
  const dur = avgDays(C.rows), durCalm = avgDays(C.calmRows);
  const lift = calmPerDay ? perDay / calmPerDay - 1 : 0;

  const cards = [
    { k: '접수 민원', v: nf(C.rows.length) + '건', tone: '',
      s: `평시 일 ${calmPerDay.toFixed(1)}건 · <b>일평균 ${perDay.toFixed(1)}건</b>`,
      e: d(lift > .02 ? `(+${(lift * 100).toFixed(0)}%)` : '', 'up') },
    { k: '완료율', v: p1(done), tone: '',
      s: `평시 ${p1(doneCalm)}`, e: d(pp((done - doneCalm) * 100), done < doneCalm ? 'bad' : 'good') },
    { k: '기한초과율', v: p1(over), tone: over > overCalm * 1.2 ? 'bad' : '',
      s: `평시 ${p1(overCalm)}`, e: d(pp((over - overCalm) * 100), over > overCalm ? 'bad' : 'good') },
    { k: '평균 처리일', v: dur.toFixed(1) + '일', tone: '',
      s: `평시 ${durCalm.toFixed(1)}일`,
      e: d((dur - durCalm >= 0 ? '+' : '−') + Math.abs(dur - durCalm).toFixed(1) + '일', dur > durCalm ? 'bad' : 'good') },
  ];
  if (C.runs.length){
    const share = C.runRows.length / C.rows.length, dshare = runDays / C.days.length;
    cards.push({ k: `급증 ${runDays}일 비중`, v: p1(share), tone: 'evt',
      s: `평시 기간의 ${p1(dshare)}`,
      e: d(`${(share / dshare).toFixed(1)}배 집중`, 'evt') });
  }
  host.innerHTML = cards.map(c =>
    `<div class="kpi${c.tone ? ' ' + c.tone : ''}"><div class="k">${c.k}</div>`
    + `<div class="v">${c.v}</div><div class="s">${c.s} ${c.e}</div></div>`).join('');
}

/* ── 한눈에 ─────────────────────────────────────────────────────── */
function renderBrief(C, depts, rise){
  const li = [];
  if (!C.rows.length){ $('an-brief').innerHTML = '<li>이 기간에는 데이터가 없습니다.</li>'; return; }
  const runDays = C.runs.reduce((s, r) => s + r.len, 0);
  if (C.runs.length){
    li.push(`급증 구간 <b>${C.runs.length}개</b> — 기간 날짜의 <b>${p1(runDays / C.days.length)}</b>에 접수의 <span class="hot">${p1(C.runRows.length / C.rows.length)}</span>가 몰렸습니다.`);
    li.push(`기한초과율은 급증 구간 <span class="hot">${p1(overRate(C.runRows))}</span>, 그 외 <b>${p1(overRate(C.calmRows))}</b>입니다.`);
    const top = depts[0];
    if (top) li.push(`부하가 가장 한쪽으로 쏠린 부서는 <b>${esc(top.k)}</b> — 이 기간 업무의 <b>${p1(top.conc)}</b>를 급증 구간에 처리했습니다.`);
  } else {
    li.push(`이 기간에는 2일 이상 이어진 급증 구간이 없습니다 (기준: 일별 중앙값 ${nf(AGG.med)}건 × 1.3).`);
  }
  if (rise && rise[0]) li.push(`최근 4주에 가장 늘어난 제목 키워드는 <b>${esc(rise[0].w)}</b>입니다 (${rise[0].label}).`);
  $('an-brief').innerHTML = li.map(x => `<li>${x}</li>`).join('');
}

/* ── 일별 추이 ──────────────────────────────────────────────────── */
function renderTrend(C){
  const days = C.days, n = days.length;
  if (!n){ $('an-trend').innerHTML = '<p class="empty-note">데이터가 없습니다.</p>'; return; }
  const vals = days.map(d => AGG.daily.get(d) || 0);
  const th = AGG.med * 1.3, mx = Math.max(th, ...vals) * 1.1;
  const W = 960, H = 240, PL = 38, PR = 8, PT = 26, PB = 24, bw = (W - PL - PR) / n;
  const Y = v => PT + (H - PT - PB) * (1 - v / mx), X = i => PL + i * bw;
  const idx = d => days.indexOf(d);
  let g = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="일별 접수량과 급증 구간">`;
  C.runs.forEach(r => {
    const i0 = idx(r.from), i1 = idx(r.to);
    g += `<rect x="${X(i0).toFixed(1)}" y="${PT - 18}" width="${((i1 - i0 + 1) * bw).toFixed(1)}" height="${H - PB - PT + 18}"`
      + ` fill="var(--warn-bg)" stroke="var(--warn-line)"/>`;
    const lab = `${md(r.from)}–${md(r.to)} · ${nf(r.sum)}`, wpx = (i1 - i0 + 1) * bw;
    const txt = wpx >= lab.length * 6.2 ? lab : wpx >= nf(r.sum).length * 6.2 + 4 ? nf(r.sum) : '';
    if (txt) g += `<text x="${(X(i0) + 3).toFixed(1)}" y="${PT - 7}" font-size="10" font-weight="700"`
      + ` font-family="var(--mono)" fill="var(--warn)"><title>${lab}건</title>${txt}</text>`;
  });
  [0, Math.round(mx / 2)].forEach(t => {
    g += `<line x1="${PL}" x2="${W - PR}" y1="${Y(t).toFixed(1)}" y2="${Y(t).toFixed(1)}" stroke="var(--border)"/>`
      + `<text x="${PL - 5}" y="${(Y(t) + 3.2).toFixed(1)}" text-anchor="end" font-size="9.5" font-family="var(--mono)" fill="var(--text-3)">${nf(t)}</text>`;
  });
  vals.forEach((v, i) => {
    const d = days[i], h = Math.max(1.5, (H - PT - PB) * v / mx), x = X(i) + bw * 0.12;
    const hot = C.inRun(d), sel = d === SEL;
    g += `<rect x="${x.toFixed(2)}" y="${(H - PB - h).toFixed(1)}" width="${(bw * 0.76).toFixed(2)}" height="${h.toFixed(1)}" rx="1"`
      + ` fill="${sel ? 'var(--text)' : 'var(--brand)'}" opacity="${sel ? 1 : hot ? .9 : .38}"/>`;
    if (v > th && !hot) g += `<circle cx="${(X(i) + bw / 2).toFixed(1)}" cy="${(H - PB - h - 4).toFixed(1)}" r="2" fill="var(--crit)"/>`;
  });
  g += `<line x1="${PL}" x2="${W - PR}" y1="${Y(AGG.med).toFixed(1)}" y2="${Y(AGG.med).toFixed(1)}" stroke="var(--text-3)" stroke-dasharray="4 3"/>`
    + `<line x1="${PL}" x2="${W - PR}" y1="${Y(th).toFixed(1)}" y2="${Y(th).toFixed(1)}" stroke="var(--crit)" stroke-dasharray="2 3" opacity=".7"/>`
    + `<text x="${W - PR}" y="${(Y(th) - 4).toFixed(1)}" text-anchor="end" font-size="9.5" font-family="var(--mono)" fill="var(--crit)">${nf(Math.round(th))}</text>`;
  days.forEach((d, i) => {
    if (d.slice(8) === '01' || i === 0)
      g += `<text x="${X(i).toFixed(1)}" y="${H - 7}" font-size="9.5" font-family="var(--mono)" fill="var(--text-3)">${md(d)}</text>`;
    g += `<rect class="hit" data-d="${d}" x="${X(i).toFixed(2)}" y="${PT}" width="${bw.toFixed(2)}" height="${H - PT - PB}" fill="transparent">`
      + `<title>${d} (${dowOf(d)}) ${nf(vals[i])}건</title></rect>`;
  });
  g += `<line x1="${PL}" x2="${W - PR}" y1="${H - PB}" y2="${H - PB}" stroke="var(--border-2)"/></svg>`;
  $('an-trend').innerHTML = g;
  $('an-trend-sub').textContent = `중앙값 ${nf(AGG.med)}건/일 · 급증 기준 ${nf(Math.round(th))}건`;
}

/* ── 날짜 상세 ──────────────────────────────────────────────────── */
function renderDay(C){
  const d = SEL;
  if (!d){ $('an-day').innerHTML = '<p class="empty-note">날짜를 고르세요.</p>'; return; }
  $('an-dsel').value = d;
  const rows = C.rows.filter(r => day(r['등록일']) === d);
  const n = rows.length;
  if (!n){ $('an-day').innerHTML = `<p class="empty-note">${d}에는 접수가 없습니다.</p>`; return; }
  const lift = n / AGG.med;
  const run = C.runs.find(r => d >= r.from && d <= r.to);
  const allT = count(C.rows, '민원유형'), dayT = count(rows, '민원유형');
  const types = [...dayT].sort((a, b) => b[1] - a[1]).slice(0, 7).map(([k, v]) => {
    const l = (v / n) / ((allT.get(k) || 1) / C.rows.length);
    return `<tr><td>${esc(k)}</td><td class="n">${small(v)}</td>`
      + `<td class="n">${v < MIN_CELL ? '—' : `<span class="lift ${l >= 2 ? 'up' : 'eq'}">${l.toFixed(1)}배</span>`}</td></tr>`;
  }).join('');
  const dep = [...count(rows, '처리부서')].sort((a, b) => b[1] - a[1]).slice(0, 3)
    .map(([k, v]) => `${esc(k)} ${small(v)}`).join(' · ');
  $('an-day').innerHTML = `
    <div class="hd"><span class="bign">${nf(n)}건</span>
      <span class="lx${lift < 1.3 ? ' calm' : ''}">중앙값의 ${lift.toFixed(2)}배</span></div>
    <p class="lede" style="margin:0 0 12px">${d} (${dowOf(d)})${run ? ` · <b>급증 구간 ${md(run.from)}–${md(run.to)}</b>` : ''}</p>
    <table class="daily" style="min-width:0"><thead><tr><th>유형</th><th style="text-align:right">건수</th>
      <th style="text-align:right">기간 평균 대비</th></tr></thead><tbody>${types}</tbody></table>
    <p class="hint">많이 받은 부서 · ${dep}</p>`;
}

/* ── 급증 구간 비교 ─────────────────────────────────────────────── */
function renderRuns(C){
  if (!C.runs.length){
    $('an-runs').innerHTML = `<tr><td colspan="7" style="color:var(--text-3);padding:16px 10px">이 기간에는 2일 이상 이어진 급증 구간이 없습니다.</td></tr>`;
    return;
  }
  const baseT = count(C.calmRows, '민원유형'), baseN = C.calmRows.length || 1;
  const line = (label, rows, len, cls) => {
    const t = count(rows, '민원유형'), N = rows.length || 1;
    const up = [...t].filter(([k, v]) => v >= MIN_CELL && (v / N) >= 2 * ((baseT.get(k) || 0) / baseN))
      .sort((a, b) => b[1] - a[1]).slice(0, 4)
      .map(([k, v]) => `${esc(k)} <span class="m" style="color:var(--text-3)">${nf(v)}</span>`).join(', ');
    return `<tr${cls ? ` class="${cls}"` : ''}><td class="m">${label}</td><td class="n">${nf(len)}</td><td class="n">${nf(rows.length)}</td>`
      + `<td class="n">${p1(rows.length / C.rows.length)}</td><td class="n">${(rows.length / len / AGG.med).toFixed(2)}배</td>`
      + `<td class="n"${overRate(rows) > overRate(C.calmRows) * 1.5 ? ' style="color:var(--crit);font-weight:700"' : ''}>${p1(overRate(rows))}</td>`
      + `<td>${up || '<span style="color:var(--text-3)">—</span>'}</td></tr>`;
  };
  let h = C.runs.map(r => line(`${r.from} ~ ${md(r.to)}`,
    C.runRows.filter(x => { const d = day(x['등록일']); return d >= r.from && d <= r.to; }), r.len)).join('');
  const calmDays = C.days.length - C.runs.reduce((s, r) => s + r.len, 0);
  h += `<tr><td colspan="7" style="padding:0;border-bottom:2px solid var(--border-2)"></td></tr>`;
  const calmT = `<tr><td style="color:var(--text-2)">그 외 기간</td><td class="n">${nf(calmDays)}</td><td class="n">${nf(C.calmRows.length)}</td>`
    + `<td class="n">${p1(C.calmRows.length / C.rows.length)}</td><td class="n">${calmDays ? (C.calmRows.length / calmDays / AGG.med).toFixed(2) + '배' : '—'}</td>`
    + `<td class="n">${p1(overRate(C.calmRows))}</td><td style="color:var(--text-3)">비교 기준</td></tr>`;
  $('an-runs').innerHTML = h + calmT;
}

/* ── 유형 지도 ──────────────────────────────────────────────────── */
function quadModel(C){
  const tot = count(C.rows, '민원유형'), hot = count(C.runRows, '민원유형');
  const share = C.rows.length ? C.runRows.length / C.rows.length : 0;
  const T = Math.min(0.9, share * 2);                 // 고르게 들어왔다면 집중도 ≈ share
  const ns = [...tot.values()].sort((a, b) => a - b), nMed = ns[Math.floor(ns.length / 2)] || 0;
  const items = [...tot].map(([k, n]) => ({ k, n, c: (hot.get(k) || 0) / n }));
  return { share, T, nMed, items };
}
function renderQuad(C, M){
  const { share, T, nMed, items } = M;
  const Q = [
    { t:'시기 집중 · 다량', a:'급증 전에 앱·홈페이지 공지와 자동응답으로 미리 흡수', hot:true, f:x => x.c >= T && x.n >= nMed },
    { t:'시기 집중 · 소량', a:'급증 기간 현장·역사 안내로 대응', hot:true, f:x => x.c >= T && x.n < nMed },
    { t:'상시 · 다량', a:'시기와 무관 — 처리 역량과 근본 원인 개선 대상', f:x => x.c < T && x.n >= nMed },
    { t:'상시 · 소량', a:'추이만 모니터링', f:x => x.c < T && x.n < nMed }
  ];
  $('an-quad').innerHTML = C.runs.length ? Q.map(q => {
    const its = items.filter(q.f).sort((a, b) => b.c - a.c || b.n - a.n);
    return `<div class="q${q.hot ? ' hot' : ''}"><h4>${q.t} <span style="font-weight:400;color:var(--text-3)">${its.length}종</span></h4>`
      + `<div class="act">${q.a}</div>`
      + (its.length ? its.map(x => `<div class="row"><span class="nm" title="${esc(x.k)}">${esc(x.k)}</span>`
        + `<span class="tr"><i style="width:${(x.c * 100).toFixed(1)}%"></i></span>`
        + `<span class="c">${p1(x.c).replace('.0%', '%')}</span><span class="c">${small(x.n)}</span></div>`).join('')
        : '<div class="empty">해당 유형 없음</div>') + '</div>';
  }).join('') : '<div class="q"><div class="empty">급증 구간이 없어 시기 집중도를 계산하지 않았습니다.</div></div>';
  $('an-quad-sub').textContent = C.runs.length ? `집중도 기준 ${p1(T)} · 건수 기준 ${nf(nMed)}건` : '';
  $('an-quad-hint').innerHTML = C.runs.length
    ? `막대는 그 유형 전체 중 <b>급증 구간에 접수된 비율</b>입니다. 이 기간 접수의 ${p1(share)}가 급증 구간에 있으므로, 고르게 들어오는 유형이라면 막대가 그 정도에 머뭅니다. 그 두 배(${p1(T)}) 이상이면 시기 집중으로 봅니다. 건수는 유형별 건수의 중앙값(${nf(nMed)}건)으로 나눴습니다.`
    : '';
}

/* ── 대응 방안 ─────────────────────────────────────────────────────
   분석 결과에서 규칙으로 만든다. 조치 계획표 한 줄 = 우선 · 이슈 · 근거 수치 · 조치 · 담당 · 시점.
   긴 설명(발견 · 조치 · 확인 지표)은 이슈를 눌러 펼친 줄에만 보인다.
   숫자는 모두 지금 고른 기간에서 계산한다.
   1인 처리량 18건/일은 예측 모델(model.json ops.handlePerPersonDay)과 같은 가정이다. */
const HANDLE = 18;
const PRI = { 1:['긴급','crit'], 2:['높음','warn'], 3:['보통','plain'] };
const CH_ACT = {
  '모바일앱':   ['앱 푸시', '앱 푸시 알림과 첫 화면 배너로 사전 안내'],
  '고객센터':   ['ARS 멘트', 'ARS 첫 멘트를 해당 안내로 교체하고 콜백 예약을 열어 대기를 분산'],
  '홈페이지':   ['공지 팝업', '메인 공지 팝업과 FAQ 상단 고정'],
  '역사접수':   ['현장 안내', '혼잡 역에 안내 인력과 안내문 배치'],
  '국민신문고': ['정형 답변', '정형 답변을 미리 준비해 처리 기한 안에 회신'],
  '이메일':     ['자동 회신', '자동 회신에 안내 링크를 넣어 1차 응대']
};
function topOf(rows, key){
  const t = [...count(rows, key)].sort((a, b) => b[1] - a[1])[0];
  return t ? t[0] : '—';
}
function cleanTitle(t){ return (t || '').replace(/<[^>]*>/g, '○').replace(/\s+/g, ' ').trim(); }

/* 근거 칸의 작은 막대 — 회색 = 이전(평소), 색 = 지금. 비율 하나만 그릴 때는 solo. */
function spark(base, tone, solo){
  const w = Math.max(0, Math.min(1, base)) * 100;
  return `<span class="spark ${tone}" aria-hidden="true">${solo ? `<i style="width:${w.toFixed(0)}%"></i>`
    : `<i style="width:100%"></i><u style="width:${w.toFixed(0)}%"></u>`}</span>`;
}
const CH_SHORT = { '모바일앱':'앱', '고객센터':'콜', '홈페이지':'웹', '역사접수':'역', '국민신문고':'신문고', '이메일':'메일' };

function buildActions(C, depts, rise, M){
  const A = [];
  if (!C.rows.length) return A;
  const runDays = C.runs.reduce((s, r) => s + r.len, 0);
  const calmDays = C.days.length - runDays;
  const big = C.runs.slice().sort((a, b) => b.sum - a.sum)[0];
  const span = r => `${md(r.from)}~${md(r.to)}`;
  const inR = (r, x) => { const d = day(x['등록일']); return d >= r.from && d <= r.to; };

  // ① 부하 쏠림 → 한시 지원
  if (C.runs.length && calmDays > 0){
    const runAvg = C.runRows.length / runDays, calmAvg = C.calmRows.length / calmDays;
    const extra = Math.ceil((runAvg - calmAvg) / HANDLE);
    const hotD = depts.filter(d => d.conc > 0.5)[0] || depts[0];
    const help = depts[depts.length - 1];
    if (extra > 0 && hotD){
      const rows = C.runRows.filter(r => r['처리부서'] === hotD.k);
      A.push({ p:1, tag:'부하 쏠림', go:'an-sec-heat',
        num: `<s>${calmAvg.toFixed(0)}</s> → <b>${runAvg.toFixed(0)}</b>건/일` + spark(calmAvg / runAvg, 'crit'),
        head:`급증 구간에 ${esc(hotD.k)} <b>+${extra}명</b> 보강`,
        who:`${esc(hotD.k)} ← ${esc(help.k)}`, when: big ? `${span(big)} 구간` : '급증 시작일부터',
        find:`${esc(hotD.k)}${sgJo(hotD.k, '은')} 이 기간 업무의 <b>${p1(hotD.conc)}</b>를 급증 구간에 처리했습니다. 급증 구간 하루 평균은 <b>${runAvg.toFixed(0)}건</b>으로 평소(${calmAvg.toFixed(0)}건)보다 ${(runAvg - calmAvg).toFixed(0)}건 많습니다.`,
        act:`급증 구간 동안 약 ${extra}명을 한시 증원합니다. 쏠림이 가장 적은 ${esc(help.k)}(${p1(help.conc)})에서 지원 인력을 보내는 것부터 검토합니다.`,
        kpi:`${esc(hotD.k)} 급증 구간 기한초과율 (이번 ${p1(overRate(rows))})` });
    }
  }

  // ② 기한초과 급증 → 기한 분리·임박 알림
  if (C.runs.length && C.calmRows.length){
    const oR = overRate(C.runRows), oC = overRate(C.calmRows);
    if (oR >= oC * 1.5 && oR >= 0.2){
      A.push({ p:1, tag:'처리 지연', go:'an-sec-runs',
        num: `<s>${p1(oC)}</s> → <b>${p1(oR)}</b>` + spark(oC / oR, 'crit'),
        head:'급증 기간 별도 처리 기한 + 매일 임박 알림',
        who:'민원 총괄', when:'급증 기간 매일',
        find:`급증 구간 기한초과율이 <b>${p1(oR)}</b>로 평소(${p1(oC)})의 ${(oR / Math.max(oC, 0.001)).toFixed(1)}배입니다. 접수량이 몇 배인 구간에 평소 처리 기한을 그대로 적용하고 있습니다.`,
        act:'급증 구간에는 별도 처리 기한을 운영하고, 매일 아침 부서장에게 "기한 D-1 미완료 건수"를 알립니다.',
        kpi:`급증 구간 기한초과율 ${p1(oR)} → 평소 수준(${p1(oC)})에 근접` });
    }
  }

  // ③ 시기 집중·다량 유형 → 사전 공지·자동응답
  if (C.runs.length){
    M.items.filter(x => x.c >= M.T && x.n >= M.nMed).sort((a, b) => b.n - a.n).slice(0, 2).forEach(x => {
      const rows = C.rows.filter(r => r['민원유형'] === x.k);
      const r0 = C.runs.map(r => [r, rows.filter(q => inR(r, q)).length]).sort((a, b) => b[1] - a[1])[0][0];
      A.push({ p:2, tag:'시기 집중', go:'an-sec-quad',
        num: `${esc(x.k)} <b>${Math.round(x.c * 100)}%</b>` + spark(x.c, 'warn', true),
        head:'급증 오기 전에 공지·자동응답으로 흡수',
        who:esc(topOf(rows, '처리부서')), when:`시작 1~3주 전 (이번 ${md(r0.from)})`,
        find:`<b>${esc(x.k)}</b> ${nf(x.n)}건 중 <b>${p1(x.c)}</b>가 급증 구간에 몰렸습니다. 가장 많았던 구간은 ${span(r0)}입니다.`,
        act:'같은 시기가 오기 전에 앱·홈페이지 공지와 자동응답으로 미리 흡수합니다. 자주 묻는 내용을 FAQ 상단에 고정합니다.',
        kpi:`다음 같은 시기 ${esc(x.k)} 건수 (이번 ${nf(x.n)}건)` });
    });
  }

  // ④ 상시 반복 민원 → 근본 원인 개선
  const rowsByTitle = new Map();
  for (const r of C.rows){ const t = cleanTitle(r['제목']); if (t) (rowsByTitle.get(t) || rowsByTitle.set(t, []).get(t)).push(r); }
  const minRep = Math.max(30, Math.round(C.rows.length * 0.02));
  const calmType = new Set(M.items.filter(x => !C.runs.length || x.c < M.T).map(x => x.k));
  const reps = [...rowsByTitle].filter(([, rs]) => rs.length >= minRep && calmType.has(topOf(rs, '민원유형')))
    .sort((a, b) => b[1].length - a[1].length).slice(0, 3);
  if (reps.length){
    const all = reps.flatMap(([, rs]) => rs);
    A.push({ p:2, tag:'반복 민원', go:'an-sec-kw',
      num: `<b>${nf(all.length)}</b>건 · ${reps.length}종`,
      head:'원인 부서에 개선 과제, 그동안 FAQ',
      who:'원인 부서(IT·시설)', when:'이번 달',
      find:`같은 제목이 시기와 상관없이 계속 들어옵니다 — ${reps.map(([t, rs]) =>
        `"<b>${esc(t)}</b>" ${nf(rs.length)}건 (${esc(topOf(rs, '민원유형'))})`).join(', ')}.`,
      act:`안내로는 줄지 않는 민원입니다. 원인 부서에 개선 과제로 넘기고, 해결 전까지 FAQ·자동응답 답변을 준비합니다. 접수 부서: ${[...new Set(reps.map(([, rs]) => topOf(rs, '처리부서')))].map(esc).join(', ')}.`,
      kpi:`같은 제목 월별 건수 (이번 기간 합계 ${nf(all.length)}건)` });
  }

  // ⑤ 시기 집중·소량 유형 → 명절 체크리스트
  if (C.runs.length){
    const its = M.items.filter(x => x.c >= M.T && x.n < M.nMed && x.c >= 0.9).sort((a, b) => b.n - a.n);
    if (its.length){
      const rows = C.rows.filter(r => its.some(x => x.k === r['민원유형']));
      const tot = its.reduce((s, x) => s + x.n, 0);
      A.push({ p:3, tag:'시기 전용', go:'an-sec-quad',
        num: `<b>${nf(tot)}</b>건 · ${its.length}종`,
        head:'명절 대응 체크리스트로 고정',
        who:esc(topOf(rows, '처리부서')), when:'명절 D-7 점검',
        find:`${its.map(x => `<b>${esc(x.k)}</b>`).join(', ')}은 90% 이상이 급증 구간에만 접수됐습니다 (합계 ${nf(tot)}건).`,
        act:'해마다 같은 시기에 반복되므로 명절 대응 체크리스트로 고정합니다 — 현장 안내 인력, 안내문, 수하물·혼잡 동선.',
        kpi:`다음 명절 같은 유형 합계 (이번 ${nf(tot)}건)` });
    }
  }

  // ⑥ 새로 뜨는 말 → 원인 확인
  (rise || []).slice(0, 1).forEach(x => {
    const r1 = addDays(C.b, -27);
    const rs = C.rows.filter(r => day(r['등록일']) >= r1 && words(r['제목']).includes(x.w));
    A.push({ p:3, tag:'새 이슈', go:'an-sec-kw',
      num: `'${esc(x.w)}' <s>${nf(x.p)}</s> → <b>${nf(x.v)}</b>`,
      head:'샘플 민원을 읽고 원인 확인',
      who:esc(topOf(rs, '처리부서')), when:'이번 주',
      find:`최근 4주에 '<b>${esc(x.w)}</b>' 키워드가 들어간 민원이 ${x.label}으로 늘었습니다. 주로 ${esc(topOf(rs, '민원유형'))} 유형입니다.`,
      act:`조회 화면에서 '${esc(x.w)}' 키워드로 민원 몇 건을 직접 읽고 원인을 확인합니다. 제도·시스템 문제라면 안내문을 먼저 냅니다.`,
      kpi:`'${esc(x.w)}' 주간 건수 (최근 4주 ${nf(x.v)}건)` });
  });

  // ⑦ 경로 변화 → 채널 집중 안내
  if (C.runs.length && C.calmRows.length){
    const a = count(C.calmRows, '유입경로'), b = count(C.runRows, '유입경로');
    const ch = [...b.keys()].map(k => [k, (b.get(k) || 0) / C.runRows.length - (a.get(k) || 0) / C.calmRows.length])
      .sort((x, y) => y[1] - x[1]).filter(([, d]) => d >= 0.03);
    if (ch.length){
      A.push({ p:3, tag:'채널', go:'an-sec-chs',
        num: ch.map(([k, d]) => `${CH_SHORT[k] || esc(k)} <b>▲${(d * 100).toFixed(1)}</b>`).join(' · ') + '%p',
        head:`급증 때 ${ch.map(([k]) => CH_ACT[k] ? CH_ACT[k][0] : esc(k)).join('·')}로 먼저 안내`,
        who:'채널 운영', when:'급증 전 준비',
        find:`급증 구간에는 ${ch.map(([k, d]) => `<b>${esc(k)}</b>(+${(d * 100).toFixed(1)}%p)`).join(', ')} 비중이 커집니다.`,
        act: ch.map(([k]) => CH_ACT[k] ? `${esc(k)} — ${CH_ACT[k][1]}` : '').filter(Boolean).join('<br>'),
        kpi:`급증 구간 ${esc(ch[0][0])} 비중·응대 대기` });
    }
  }
  return A.sort((x, y) => x.p - y.p);
}
function renderActions(A){
  if (!A.length){
    $('an-acts').innerHTML = '<p class="empty-note" style="padding:14px 16px">이 기간에서는 따로 제안할 조치가 없습니다.</p>';
    $('an-acts-sub').textContent = '';
    return;
  }
  /* 표로 7건을 늘어놓으면 어느 것이 급한지 눈에 들어오지 않는다.
     카드로 끊고 우선순위를 색과 칩으로 먼저 보이게 한다. */
  $('an-acts').innerHTML = `<div class="pcards">${A.map((a, i) => {
    const [pl, pc] = PRI[a.p];
    return `<article class="pcard ${pc}">
      <div class="ph"><span class="pchip">${pl}</span><b>${a.tag}</b>
        <span class="sp"></span>
        <button type="button" class="ago" data-go="${a.go}" aria-label="${a.tag} 근거 섹션으로">근거 ↓</button></div>
      <div class="pnum">${a.num}</div>
      <p class="pact">${a.head}</p>
      <div class="pmeta"><span class="who">${a.who}</span><span class="when">${a.when}</span></div>
      <button type="button" class="is" aria-expanded="false" aria-controls="an-pd-${i}">자세히</button>
      <div class="pd" id="an-pd-${i}" hidden>
        <p>${a.find}</p><p><b>조치</b> ${a.act}</p><p><b>확인 지표</b> ${a.kpi}</p></div>
    </article>`;
  }).join('')}</div>`;
  $('an-acts-sub').textContent = `${A.length}건 · 우선순위 순`;
}

/* ── 부서 × 주 ──────────────────────────────────────────────────── */
function monday(s){ const d = new Date(s + 'T00:00:00'); const k = (d.getDay() + 6) % 7; return addDays(s, -k); }
function renderHeat(C){
  const weeks = [...new Set(C.days.map(monday))].sort();
  const wIdx = new Map(weeks.map((w, i) => [w, i]));
  const runWeek = weeks.map(w => C.runs.some(r => r.from <= addDays(w, 6) && r.to >= w));
  const depts = [...count(C.rows, '처리부서')].sort((a, b) => b[1] - a[1]).map(([k]) => k);
  const cell = {};
  for (const r of C.rows){
    const k = r['처리부서'] + '|' + wIdx.get(monday(day(r['등록일'])));
    (cell[k] = cell[k] || []).push(r);
  }
  let mx = 1;
  for (const k in cell) mx = Math.max(mx, cell[k].length);
  const shades = ['var(--surface-3)', 'color-mix(in srgb,var(--brand) 18%,var(--surface))',
    'color-mix(in srgb,var(--brand) 38%,var(--surface))', 'color-mix(in srgb,var(--brand) 62%,var(--surface))', 'var(--brand)'];
  const out = [];
  let h = `<thead><tr><th></th>${weeks.map((w, i) => `<th${runWeek[i] ? ' class="run"' : ''}>${md(w)}</th>`).join('')}`
    + `<th style="text-align:right">급증 구간 처리 비중</th></tr></thead><tbody>`;
  for (const dp of depts){
    const all = C.rows.filter(r => r['처리부서'] === dp);
    const conc = all.length ? all.filter(r => C.inRun(day(r['등록일']))).length / all.length : 0;
    out.push({ k: dp, conc });
    h += `<tr><th class="dn" scope="row">${esc(dp)}</th>`;
    weeks.forEach((w, i) => {
      const rs = cell[dp + '|' + i] || [], n = rs.length;
      const lvl = n ? Math.min(4, Math.floor(n / mx * 5)) : 0;
      const txt = n < MIN_CELL ? (n ? '&lt;5' : '') : Math.round(overRate(rs) * 100) + '%';
      h += `<td style="background:${shades[lvl]};color:${lvl >= 3 ? 'var(--brand-ink)' : 'var(--text)'}"`
        + ` title="${esc(dp)} · ${md(w)} 주 · ${n < MIN_CELL ? '5건 미만' : nf(n) + '건 · 기한초과 ' + txt}">${txt}</td>`;
    });
    h += `<td class="tot">${C.runs.length ? (conc > 0.5 ? `<b>${p1(conc)}</b>` : p1(conc)) : '—'}</td></tr>`;
  }
  $('an-heat').innerHTML = h + '</tbody>';
  $('an-sw').innerHTML = shades.map(s => `<i style="background:${s}"></i>`).join('');
  return out.sort((a, b) => b.conc - a.conc);
}

/* ── 유입경로 ───────────────────────────────────────────────────── */
function renderCh(C){
  if (!C.runs.length || !C.calmRows.length){
    $('an-chs').innerHTML = `<tr><td colspan="4" style="color:var(--text-3);padding:16px 10px">급증 구간이 없어 비교하지 않았습니다.</td></tr>`;
    return;
  }
  const a = count(C.calmRows, '유입경로'), b = count(C.runRows, '유입경로');
  const keys = [...count(C.rows, '유입경로')].sort((x, y) => y[1] - x[1]).map(([k]) => k);
  $('an-chs').innerHTML = keys.map(k => {
    const sa = (a.get(k) || 0) / C.calmRows.length, sb = (b.get(k) || 0) / C.runRows.length, df = (sb - sa) * 100;
    const w = Math.min(60, Math.abs(df) * 6);
    return `<tr><td>${esc(k)}</td><td class="n">${p1(sa)}</td><td class="n">${p1(sb)}</td>`
      + `<td class="nw"><span class="dbar" style="width:${w.toFixed(0)}px;background:${df >= 0 ? 'var(--crit)' : 'var(--text-3)'}"></span>`
      + ` <span class="m" style="font-size:11px;color:${df >= 0 ? 'var(--crit)' : 'var(--text-3)'}">${df >= 0 ? '+' : ''}${df.toFixed(1)}%p</span></td></tr>`;
  }).join('');
}

/* ── 제목 키워드 ────────────────────────────────────────────────────
   제목만 읽는다. 마스킹 토큰(<이름_1> 등)은 지우고, 어절 끝 조사를 떼어 센다. */
const STOP = new Set(['요청','문의','요청합니다','신고합니다','않았습니다','적용되지','합니다','있습니다','했습니다',
  '바랍니다','부탁드립니다','관련','건','등','중','및','대한','때문에','상태','기준','단계','시간','불가']);
const KEEP1 = new Set(['설','앱']);
const JOSA = /(에서|으로|에게|까지|부터|이요|이|가|을|를|은|는|에|의|로|도|과|와)$/;
function words(title){
  const out = [];
  for (let w of (title || '').replace(/<[^>]*>/g, ' ').replace(/[^\p{L}\p{N}\-]+/gu, ' ').split(/\s+/)){
    if (!w) continue;
    if (w.length > 2) w = w.replace(JOSA, '');
    if (STOP.has(w) || /^\d+$/.test(w)) continue;
    if (w.length < 2 && !KEEP1.has(w)) continue;
    out.push(w);
  }
  return out;
}
function kwCount(rows){
  const m = new Map();
  for (const r of rows) for (const w of new Set(words(r['제목']))) m.set(w, (m.get(w) || 0) + 1);
  return m;
}
function renderKw(C){
  const all = [...kwCount(C.rows)].filter(([, v]) => v >= MIN_CELL).sort((a, b) => b[1] - a[1]).slice(0, 12);
  const tm = all.length ? all[0][1] : 1;
  $('an-kw-top').innerHTML = all.length ? all.map(([k, v]) =>
    `<div class="rk"><span class="nm" title="${esc(k)}">${esc(k)}</span><span class="tr"><i style="width:${(v / tm * 100).toFixed(1)}%"></i></span><span class="ct">${nf(v)}</span></div>`).join('')
    : '<p class="empty-note" style="padding:8px 0">표시할 키워드가 없습니다.</p>';

  // 최근 28일 vs 그 직전 28일
  const span = C.days.length, end = C.b;
  if (span < 56){
    $('an-kw-rise').innerHTML = '<p class="empty-note" style="padding:8px 0">비교하려면 기간이 8주 이상이어야 합니다.</p>';
    $('an-kw-rise-h').textContent = '최근 4주에 늘어난 말';
    return null;
  }
  const r1 = addDays(end, -27), r0 = addDays(end, -55), p1e = addDays(end, -28);
  const rec = kwCount(C.rows.filter(r => day(r['등록일']) >= r1));
  const prv = kwCount(C.rows.filter(r => { const d = day(r['등록일']); return d >= r0 && d <= p1e; }));
  const rise = [...rec].filter(([, v]) => v >= 10).map(([w, v]) => {
    const p = prv.get(w) || 0;
    return { w, v, p, g: (v - p) / Math.max(p, 1), label: p < MIN_CELL ? `직전 4주 ${p < 1 ? '0' : '&lt;5'} → ${nf(v)}건` : `${nf(p)} → ${nf(v)}건` };
  }).filter(x => x.v > x.p * 1.2).sort((a, b) => b.g - a.g).slice(0, 7);
  $('an-kw-rise-h').textContent = `최근 4주(${md(r1)}~${md(end)})에 늘어난 말`;
  $('an-kw-rise').innerHTML = rise.length ? rise.map(x =>
    `<div class="rise"><span>${esc(x.w)}</span><span class="s">${x.label}</span>`
    + `<span class="g${x.p < MIN_CELL ? ' new' : ''}">${x.p < MIN_CELL ? '신규' : '▲ ' + (x.v / x.p).toFixed(1) + '배'}</span></div>`).join('')
    : '<p class="empty-note" style="padding:8px 0">직전 4주보다 뚜렷하게 늘어난 말이 없습니다.</p>';
  return rise;
}

/* ── 렌더 ──────────────────────────────────────────────────────── */
function render(){
  const a = $('an-d1').value || AGG.lo, b = $('an-d2').value || AGG.hi;
  const C = compute(a <= b ? a : b, a <= b ? b : a);
  CUR = C;
  if (!SEL || SEL < C.a || SEL > C.b){
    // 기본 선택 — 범위 안에서 가장 많이 들어온 날
    SEL = C.days.reduce((m, d) => (AGG.daily.get(d) || 0) > (AGG.daily.get(m) || 0) ? d : m, C.days[0] || null);
  }
  $('an-dsel').min = C.a; $('an-dsel').max = C.b;
  const M = quadModel(C);
  renderTrend(C); renderDay(C); renderRuns(C); renderQuad(C, M);
  const depts = renderHeat(C); renderCh(C);
  const rise = renderKw(C);
  renderKpi(C);
  renderBrief(C, depts, rise);
  renderActions(buildActions(C, depts, rise, M));
}

/* ── 이벤트 ────────────────────────────────────────────────────── */
$('an-btn-go').onclick = () => { markQuick(); render(); };
$('an-d1').onchange = $('an-d2').onchange = () => markQuick();
$('an-dsel').onchange = () => { if ($('an-dsel').value){ SEL = $('an-dsel').value; renderTrend(CUR); renderDay(CUR); } };
$('an-trend').addEventListener('click', e => {
  const t = e.target.closest('[data-d]');
  if (t){ SEL = t.dataset.d; renderTrend(CUR); renderDay(CUR); }
});
$('an-acts').addEventListener('click', e => {
  const x = e.target.closest('button.is');
  if (x){
    const open = x.getAttribute('aria-expanded') !== 'true';
    x.setAttribute('aria-expanded', String(open));
    $(x.getAttribute('aria-controls')).hidden = !open;
    const box = x.closest('.pcard') || x.closest('tr');
    if (box) box.classList.toggle('open', open);
    return;
  }
  const b = e.target.closest('[data-go]'); if (!b) return;
  const t = $(b.dataset.go); if (!t) return;
  t.scrollIntoView({ behavior: 'smooth', block: 'start' });
  t.classList.remove('flash'); void t.offsetWidth; t.classList.add('flash');
});
function markQuick(){
  document.querySelectorAll('#an-quick button').forEach(c =>
    c.setAttribute('aria-pressed', String(c.dataset.a === $('an-d1').value && c.dataset.b === $('an-d2').value)));
}

/* ── 시작 ──────────────────────────────────────────────────────────
   조회 보기가 이미 받은 DATA 를 넘겨받는다. CSV 를 두 번 받지 않는다. */
function init(rows){
  DATA = rows || [];
  if (!DATA.length){
    $('an-brief').innerHTML = '<li>분석할 데이터가 없습니다. 서버 모드에서는 원본 행이 브라우저로 오지 않으므로, 분석 집계 API(/api/analytics)가 붙어야 이 보기가 동작합니다.</li>';
    return;
  }
  AGG = sgAgg(DATA);
  $('an-rng-hint').textContent = `데이터 ${AGG.lo} ~ ${AGG.hi}`;
  ['an-d1', 'an-d2'].forEach(id => { $(id).min = AGG.lo; $(id).max = AGG.hi; });
  const months = [...new Set(AGG.days.map(d => d.slice(0, 7)))];
  const q = [['전체', AGG.lo, AGG.hi]].concat(months.map(m => {
    const ds = AGG.days.filter(d => d.startsWith(m));
    return [`${+m.slice(5)}월`, ds[0], ds[ds.length - 1]];
  }));
  $('an-quick').innerHTML = q.map(([k, a, b]) => `<button type="button" data-a="${a}" data-b="${b}">${k}</button>`).join('');
  $('an-quick').addEventListener('click', e => {
    const bt = e.target.closest('button[data-a]'); if (!bt) return;
    $('an-d1').value = bt.dataset.a; $('an-d2').value = bt.dataset.b; markQuick(); render();
  });
  $('an-d1').value = AGG.lo; $('an-d2').value = AGG.hi; markQuick();
  render();
}
window.Analysis = { init };
})();
