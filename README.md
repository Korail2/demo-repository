# 민원 조회 콘솔

열차 민원 데이터를 **마스킹 → 조회 → 다운로드 → 예측**까지 처리하는 정적 웹 콘솔.
서버 없이 GitHub Pages 로 배포된다.

```
index.html              조회·다운로드 페이지   (빌드 산출물 — 직접 고치지 말 것)
forecast.html           민원 예측 페이지       (빌드 산출물 — 직접 고치지 말 것)
src/
  console.html          조회 페이지 소스 (아티팩트용 조각)
  forecast.html         예측 페이지 소스 (아티팩트용 조각)
assets/
  console.css           두 페이지 공유 스타일
  common.js             두 페이지 공유 유틸 (CSV 파서, 저장, 이력, 상태 판정)
data/
  masked_seasonal.csv   마스킹 완료 데이터 — 이 폴더에는 audit 0건인 파일만 둔다
  model.json            예측 모델 파라미터 (기준선·요일·시기·공휴일 계수 + 명절 프로파일)
tools/
  pii_mask.py           마스킹 엔진 + 테이블 파이프라인 (표준 라이브러리만)
  make_seasonal.py      계절성 더미데이터 생성기
  forecast.py           추석 → 설 예측 모듈
  build.py              src/ 조각 → 정적 호스팅용 완전한 HTML
  MASKING_POLICY.md     마스킹 기준 (T1/T2/T3 분류, 우선순위, 운영 게이트)
```

### 페이지 분리

| 페이지 | 하는 일 | 받는 데이터 |
|---|---|---|
| `index.html` | 기간·유형별 조회, 조회분만 CSV 다운로드, 상세 열람 | `data/masked_seasonal.csv` (6.5MB) |
| `forecast.html` | 날짜를 고르면 요일·시기·공휴일을 분해해 예측. 명절이면 추석 실적으로 투영 | `data/model.json` (8KB) |

예측 페이지는 원본 CSV를 받지 않는다. **모델 파라미터만 받아 브라우저에서 계산**하므로
6.5MB 대신 8KB로 열리고, 미리 계산해 둔 날짜가 아니어도 사용자가 고른 어떤 날짜든 예측한다.
두 페이지는 `assets/` 를 공유해 중복이 없다.

### 예측 모델

```
예측(d) = 기준선(d) × 요일계수 × 시기(월)계수 × 명절·공휴일계수
```

| 요소 | 추정 방법 |
|---|---|
| 기준선 | 명절·공휴일 영향권을 뺀 평시 일별 건수의 선형 추세. 데이터 범위 밖은 외삽 |
| 요일계수 | 평시 실측 ÷ 기준선 의 요일별 중앙값 |
| 시기계수 | 평시 실측 ÷ (기준선×요일) 의 월별 중앙값. 관측 8일 미만인 달은 1.00 (미적용) |
| 공휴일 | 당일은 접수 감소, 다음 영업일은 적체 해소. 두 계수를 실측에서 추정 |
| 명절 | 설·추석은 성격이 달라 **추석 상대일 프로파일**을 투영하고, 예매기 실적비로 증감 보정 |

연휴 기간(D−1~D+2)에는 요일계수를 적용하지 않는다. 명절 당일에는 그날이
일요일이든 금요일이든 사람이 움직이기 때문이다.

### 개인정보

예측은 **마스킹을 통과한 파일만** 입력으로 받는다. `tools/forecast.py` 가 직접 검사하고,
잔존 개인정보가 발견되면 계산을 거부한다. 또 예측이 읽는 컬럼은
`등록일 · 민원유형 · 처리부서 · 처리기한 · 완료일` 뿐이고
**제목·질문·답변 본문은 마스킹된 상태로도 읽지 않는다.**

### 화면을 고칠 때

`index.html` / `forecast.html` 은 빌드 산출물이다. **`src/` 를 고치고 다시 빌드한다.**

```bash
python3 tools/build.py src/console.html  index.html
python3 tools/build.py src/forecast.html forecast.html
```

`src/` 조각에는 `<!doctype>` 와 `<head>` 가 없다. claude.ai 아티팩트는 발행 시
플랫폼이 그 부분을 붙여주지만 GitHub Pages 에는 아무것도 붙지 않기 때문에
`build.py` 가 그 차이를 메운다. **이 단계를 건너뛰면 charset 선언이 없어
JavaScript 안의 한글 컬럼명이 깨지고 표가 통째로 빈다.**

---

## ⚠ 커밋하기 전에

`.gitignore` 가 막고 있는 파일을 **절대 강제로 커밋하지 말 것.**

git 은 한 번 커밋된 파일을 히스토리에 영구 보관한다. 나중에 삭제 커밋을 올려도
과거 커밋에서 그대로 복원되고, GitHub Pages 로 배포했다면 그 시점에 인터넷 전체에
공개된 것으로 봐야 한다. 되돌리려면 히스토리 재작성과 강제 푸시가 필요하고,
이미 클론·포크된 사본은 회수할 수 없다.

커밋 금지 대상:

| 대상 | 이유 |
|---|---|
| `*_raw.csv` | 마스킹 전 원본. 민원 본문에 실명·연락처·주민번호가 그대로 있다 |
| `map*.jsonl` | 가명처리 매핑표 = 개인정보보호법상 **'추가정보'**. 유출되면 가명처리가 무효가 된다 |
| `review*.csv` | 마스킹에 실패해 사람이 검토해야 하는 격리 레코드 |

**공개 리포로 두면 `data/` 도 인터넷에 공개된다.** 마스킹을 통과했더라도
사내 데이터를 외부에 공개할지는 별개 판단이다. 실데이터를 다룬다면
**Private 리포 + GitHub Pages(유료 플랜) 또는 사내 호스팅**을 쓰는 쪽이 맞다.

새 데이터를 `data/` 에 넣기 전에 반드시:

```bash
python3 tools/pii_mask.py audit data/<파일>.csv   # 잔존 의심 0건이어야 한다
```

---

## 데이터 파이프라인

```bash
# 1) 더미 생성 (실데이터라면 이 단계를 건너뛴다)
python3 tools/make_seasonal.py -o seasonal_raw.csv

# 2) 마스킹 — 날짜·접수번호 모드가 핵심이다
python3 tools/pii_mask.py mask \
    -i seasonal_raw.csv -o data/masked_seasonal.csv \
    --id-mode surrogate --date-mode hour --all-dates \
    --quarantine review.csv --mapping map.jsonl

# 3) 교차 검증 — 마스커와 무관한 정규식으로 재검사
python3 tools/pii_mask.py audit data/masked_seasonal.csv

# 4) 예측 모델 추정 (입력이 마스킹을 통과하지 않으면 거부된다)
python3 tools/forecast.py -i data/masked_seasonal.csv -o data/model.json
```

`--id-mode fixed`, `--date-mode format` 으로 마스킹하면 **이 화면이 성립하지 않는다.**
접수번호가 전부 같아져 행 식별이 불가능하고, 등록일이 형식 문자열이 되어
기간 조회도 시간대별 집계도 할 수 없다. `tools/MASKING_POLICY.md` §7 참고.

---

## 로컬 실행

`fetch()` 로 `data/` 를 읽으므로 `file://` 로 직접 열면 동작하지 않는다.
간단한 서버를 띄운다.

```bash
python3 -m http.server 8000
# http://localhost:8000
```

---

## 배포 (GitHub Pages)

`.github/workflows/pages.yml` 이 들어 있어 `main` 에 푸시하면 자동 배포된다.
최초 1회만 설정이 필요하다.

1. GitHub 리포 생성 후 푸시

   ```bash
   git remote add origin git@github.com:<계정>/<리포>.git
   git push -u origin main
   ```

2. 리포 **Settings → Pages → Build and deployment → Source** 를 **GitHub Actions** 로 변경

3. Actions 탭에서 배포 완료 확인 → `https://<계정>.github.io/<리포>/`

데이터가 6MB 대라 첫 로딩에 수 초 걸린다. 더 키울 거라면
CSV 를 월별로 쪼개고 화면에서 필요한 월만 받아오는 쪽으로 바꾸는 게 낫다.

---

## 다음 단계

이 리포는 **정적 프로토타입**이다. 실제 운영으로 가려면:

- `data/*.csv` → DB (조회 조건이 늘어나면 브라우저에서 전건 필터링이 한계에 닿는다)
- 다운로드 이력 → 브라우저 `localStorage` 가 아니라 **서버 로그**.
  「개인정보의 안전성 확보조치 기준」 제8조는 접속기록을 1년 이상(고유식별정보·민감정보
  처리 시 2년 이상) 보관하고 다운로드 상황을 확인·점검하도록 한다
- 예측 산출 → 스케줄 실행 (n8n 등). 단, **마스킹 전 데이터를 n8n 에 통과시키지 말 것** —
  실행 데이터가 기본 14일간 보관되어 새로운 개인정보 보관소가 된다
- 권한별 컬럼 마스킹, 조회 기간 상한, 다운로드 사유 입력

---

본 리포의 데이터는 전부 합성이며 실제 개인정보가 아니다.
마스킹 기준은 법률 자문이 아니므로 실제 적용 전 사내 개인정보보호 담당자 검토를 받을 것.
