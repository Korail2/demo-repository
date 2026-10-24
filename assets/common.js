/* 민원 조회 콘솔 — 두 페이지가 공유하는 유틸 */
'use strict';

const COLS = ["접수번호","민원유형","유입경로","등록일","접수일","처리기한","완료일","고객요구유형",
              "제목","질문","답변","상위완료유형","완료유형","처리부서","처리자"];
const DW = '월화수목금토일';

const $ = id => document.getElementById(id);
const nf = n => Number(n).toLocaleString('ko-KR');
const day = s => (s || '').slice(0, 10);

function esc(s){
  return (s == null ? '' : String(s)).replace(/[&<>"]/g, m =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));
}
/* 마스킹 토큰 <이름_1> 을 강조 표시한다 */
function tokens(s){
  return esc(s).replace(/&lt;([^&]{1,18}?_\d+)&gt;/g, '<span class="tok">&lt;$1&gt;</span>');
}
function dowOf(iso){
  const d = new Date(iso + 'T00:00:00');
  return DW[(d.getDay() + 6) % 7];
}

/* 처리기한 경과 여부로 상태를 판정한다 */
function status(r){
  if (r['완료일']) return (day(r['완료일']) > day(r['처리기한'])) ? ['기한초과','over'] : ['완료','done'];
  return ['미완료','open'];
}

/* RFC4180 — 질문·답변 본문에 쉼표와 줄바꿈이 들어있어 따옴표 처리가 필수다 */
function parseCSV(text){
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1);
  const rows = []; let row = [], f = '', q = false;
  for (let i = 0; i < text.length; i++){
    const c = text[i];
    if (q){
      if (c === '"'){ if (text[i+1] === '"'){ f += '"'; i++; } else q = false; }
      else f += c;
    } else if (c === '"') q = true;
    else if (c === ','){ row.push(f); f = ''; }
    else if (c === '\n'){ row.push(f); f = ''; rows.push(row); row = []; }
    else if (c !== '\r') f += c;
  }
  if (f.length || row.length){ row.push(f); rows.push(row); }
  if (!rows.length) return [];
  const head = rows[0].map(h => h.trim());
  return rows.slice(1).filter(r => r.length > 1)
    .map(r => { const o = {}; head.forEach((h, i) => o[h] = (r[i] ?? '').trim()); return o; });
}
function toCSV(rows, cols){
  const q = v => { v = v == null ? '' : String(v);
    return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
  const out = [cols.join(',')];
  for (const r of rows) out.push(cols.map(c => q(r[c])).join(','));
  return '﻿' + out.join('\r\n') + '\r\n';   /* BOM 이 없으면 엑셀에서 한글이 깨진다 */
}

/* 파일 저장 — 아티팩트 뷰어에서는 뷰어가 중재하고, 일반 웹에서는 브라우저가 직접 받는다 */
let SAVER = null;
(async () => { try { SAVER = await window.claude?.use?.('downloads'); } catch (e) { SAVER = null; } })();
const SAVE_MSG = {
  declined:'저장을 취소했습니다.', too_large:'파일이 너무 큽니다. 범위를 좁혀 주세요.',
  rate_limited:'저장 요청이 많습니다. 잠시 후 다시 시도해 주세요.',
  rejected_extension:'이 형식은 저장할 수 없습니다.', bad_request:'저장 요청이 잘못되었습니다.'
};
async function saveFile(name, text, mime){
  const blob = new Blob([text], {type: mime || 'text/csv'});
  if (SAVER){
    const r = await SAVER.save({ filename: name, data: blob });
    return r.status === 'delivered' ? '전달했습니다.' : '저장했습니다.';
  }
  const url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  return '브라우저 다운로드를 확인하세요.';
}
function saveError(err){
  return SAVE_MSG[(err && err.code) || ''] || '저장할 수 없습니다. 복사를 사용해 주세요.';
}
async function copyText(text, btn, label){
  try { await navigator.clipboard.writeText(text); btn.textContent = '복사됨'; }
  catch (e) { btn.textContent = '복사 실패'; }
  setTimeout(() => { btn.textContent = label; }, 1800);
}

/* 다운로드 이력 — 운영계에서는 서버 로그여야 한다 (안전성 확보조치 기준 제8조) */
const HK = 'cs-dl-hist';
const readHist = () => { try { return JSON.parse(localStorage.getItem(HK) || '[]'); } catch (e) { return []; } };
function pushHist(entry){
  const h = readHist();
  h.unshift(Object.assign({ t:new Date().toISOString() }, entry));
  try { localStorage.setItem(HK, JSON.stringify(h.slice(0, 30))); } catch (e) {}
  return h;
}
function stamp(iso){
  const d = new Date(iso), p = v => String(v).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} `
       + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/* 데이터 로드 — 두 페이지가 같은 경로를 본다 */
async function loadCSV(path){
  try { const r = await fetch(path); if (r.ok) return parseCSV(await r.text()); } catch (e) {}
  return null;
}
async function loadJSON(path){
  try { const r = await fetch(path); if (r.ok) return await r.json(); } catch (e) {}
  return null;
}
