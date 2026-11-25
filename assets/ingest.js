'use strict';
/* ingest.js — CSV 추가와 데이터 초기화
 *
 * 무엇을 하나
 *   화면에 올라와 있는 데이터에 CSV 를 더하고, 더한 것을 전부 되돌린다.
 *   더한 데이터는 이 브라우저에만 쌓인다. 서버가 없으므로 다른 PC 에는
 *   보이지 않고, 올린 사람 외에는 아무도 볼 수 없다.
 *
 * 받기 전에 두 번 본다
 *   1) 컬럼 — 화면이 쓰는 열이 하나라도 없으면 받지 않는다. 받아 놓고
 *      빈 화면을 보여주느니 이유를 말하고 거절하는 편이 낫다.
 *   2) 개인정보 — pii_mask.py 의 잔존검사와 같은 규칙으로 본문을 훑는다.
 *      이름·연락처가 남아 있는 파일은 받지 않는다. 이 콘솔은 마스킹을
 *      통과한 데이터만 다루기로 하고 만든 것이고, 한 번 브라우저에
 *      들어오면 그 PC 에 남는다.
 *
 * 어디에 쌓이나
 *   IndexedDB. 용량이 커서 수만 행도 들어간다. 더블클릭(file://)으로 열면
 *   브라우저가 막는 경우가 있는데, 그때는 이번 세션에만 들고 있다가
 *   창을 닫으면 사라진다 — 화면에 그렇게 적는다.
 */
const Ingest = (function (){

  const DB_NAME = 'korail-cs', STORE = 'added', DB_VER = 1;
  const MAX_BYTES = 20 * 1024 * 1024;          // 20MB — 그 이상은 서버가 받을 일이다

  /* 화면이 반드시 읽는 열. slim.csv 와 같은 구성이다. */
  const REQUIRED = ['접수번호', '민원유형', '유입경로', '등록일',
                    '처리기한', '완료일', '제목', '처리부서', '완료유형'];

  /* 잔존 개인정보 — tools/pii_mask.py 의 audit 와 같은 규칙.
     토큰(<이름_1>)은 이미 가려진 값이므로 걸리지 않아야 한다. */
  const LEAK = [
    ['휴대전화', /01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}/],
    ['유선전화', /0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70)[-.\s]?\d{3,4}[-.\s]?\d{4}/],
    ['이메일', /[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/],
    ['주민등록번호', /\d{6}\s*-\s*[1-8]\d{6}/],
    ['카드번호', /\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}/],
    ['계좌번호', /\d{2,6}-\d{2,6}-\d{2,8}/],
    ['IP주소', /(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])/],
    ['연속숫자7자리이상', /(?<![\d.,-])\d{7,}(?![\d.,-])/],
  ];
  const SCAN_COLS = ['제목', '질문', '답변'];

  let memOnly = false;      // IndexedDB 를 못 쓰는 환경
  let memRows = [];

  /* 기본 더미를 숨겼는지. 지우는 것이 아니라 읽지 않는 것이다 —
     data/slim.csv 는 배포물의 일부라 브라우저가 지울 수 없고, 지울
     이유도 없다. 플래그만 세워 두고 다시 켤 수 있게 둔다. */
  const BASE_KEY = 'korail-cs.base-hidden';
  let memBaseHidden = false;
  function baseHidden(){
    try { return localStorage.getItem(BASE_KEY) === '1'; }
    catch (e){ return memBaseHidden; }
  }
  function setBaseHidden(v){
    memBaseHidden = !!v;
    try { v ? localStorage.setItem(BASE_KEY, '1') : localStorage.removeItem(BASE_KEY); }
    catch (e){ /* 저장소를 막아 둔 환경에서는 이번 세션에만 적용된다 */ }
  }

  /* ── 저장소 ─────────────────────────────────────────────────────── */
  function open(){
    return new Promise((res, rej) => {
      let rq;
      try { rq = indexedDB.open(DB_NAME, DB_VER); }
      catch (e){ return rej(e); }
      rq.onupgradeneeded = () => {
        const d = rq.result;
        if (!d.objectStoreNames.contains(STORE)) d.createObjectStore(STORE, { autoIncrement: true });
      };
      rq.onsuccess = () => res(rq.result);
      rq.onerror = () => rej(rq.error);
      rq.onblocked = () => rej(new Error('blocked'));
    });
  }

  async function load(){
    if (memOnly) return memRows;
    try {
      const d = await open();
      return await new Promise((res, rej) => {
        const rq = d.transaction(STORE, 'readonly').objectStore(STORE).getAll();
        rq.onsuccess = () => res(rq.result.flat());
        rq.onerror = () => rej(rq.error);
      });
    } catch (e){ memOnly = true; return memRows; }
  }

  async function append(rows){
    if (!memOnly){
      try {
        const d = await open();
        await new Promise((res, rej) => {
          const tx = d.transaction(STORE, 'readwrite');
          tx.objectStore(STORE).add(rows);
          tx.oncomplete = res; tx.onerror = () => rej(tx.error);
        });
        return;
      } catch (e){ memOnly = true; }
    }
    memRows = memRows.concat(rows);   // 저장소를 못 쓰면 이번 세션에만 들고 있는다
  }

  async function clear(){
    memRows = [];
    if (memOnly) return;
    try {
      const d = await open();
      await new Promise((res, rej) => {
        const tx = d.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).clear();
        tx.oncomplete = res; tx.onerror = () => rej(tx.error);
      });
    } catch (e){ /* 지울 게 없으면 그걸로 된 것이다 */ }
  }

  /* ── 검사 ───────────────────────────────────────────────────────── */
  function checkColumns(rows){
    if (!rows.length) return '행이 하나도 없습니다.';
    const have = Object.keys(rows[0]);
    const miss = REQUIRED.filter(c => !have.includes(c));
    if (miss.length) return `필요한 열이 없습니다 — ${miss.join(', ')}`;
    return null;
  }

  /* 본문에 가려지지 않은 개인정보가 남아 있는지. 걸린 행 번호까지 돌려준다. */
  /* 전화번호는 계좌번호 모양(2~6-2~6-2~8)에도 들어맞는다. 먼저 걸린
     전화번호 자리를 지우고 나머지를 본다 — 없는 항목을 알려주면
     읽는 사람이 파일에서 그걸 찾느라 헤맨다. */
  const PHONE = [LEAK[0][1], LEAK[1][1]];
  function strip(t){
    let out = t;
    for (const re of PHONE) out = out.replace(new RegExp(re.source, 'g'), ' ');
    return out;
  }

  function scan(rows){
    const hits = new Map();
    rows.forEach((r, i) => {
      for (const col of SCAN_COLS){
        const raw = r[col];
        if (!raw) continue;
        const rest = strip(raw);
        for (const [name, re] of LEAK){
          const t = (name === '휴대전화' || name === '유선전화') ? raw : rest;
          if (!re.test(t)) continue;
          if (!hits.has(name)) hits.set(name, []);
          const at = hits.get(name);
          if (at.length < 3) at.push(i + 2);     // 헤더가 1행
        }
      }
    });
    return [...hits].map(([k, at]) => ({ kind: k, at }));
  }

  /* ── 읽기 ───────────────────────────────────────────────────────── */
  function readFile(file){
    return new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(fr.result);
      fr.onerror = () => rej(fr.error);
      fr.readAsText(file, 'utf-8');
    });
  }

  /* 파일 하나를 받아 검사하고, 통과하면 저장한다.
     { ok, rows, added, error, leaks, memOnly } 를 돌려준다. */
  async function addFile(file){
    if (!file) return { ok: false, error: '파일을 고르지 않았습니다.' };
    if (!/\.csv$/i.test(file.name))
      return { ok: false, error: 'CSV 파일만 받습니다.' };
    if (file.size > MAX_BYTES)
      return { ok: false, error: `파일이 너무 큽니다 (${(file.size / 1048576).toFixed(1)}MB). `
        + `${MAX_BYTES / 1048576}MB 까지만 받습니다. 이보다 크면 서버 구성이 필요합니다.` };

    let rows;
    try { rows = parseCSV(await readFile(file)); }
    catch (e){ return { ok: false, error: 'CSV 를 읽지 못했습니다. UTF-8 로 저장된 파일인지 확인해 주세요.' }; }

    const colErr = checkColumns(rows);
    if (colErr) return { ok: false, error: colErr };

    const leaks = scan(rows);
    if (leaks.length)
      return { ok: false, leaks,
               error: '마스킹을 통과하지 않은 것으로 보입니다. 받지 않았습니다.' };

    await append(rows);
    return { ok: true, added: rows.length, rows, memOnly };
  }

  /* 전부 비우기 — 더한 것을 지우고 기본 더미도 읽지 않게 한다. */
  async function wipeAll(){
    await clear();
    setBaseHidden(true);
  }

  return { load, addFile, clear, wipeAll,
           baseHidden, restoreBase: () => setBaseHidden(false),
           isMemOnly: () => memOnly, REQUIRED };
})();
