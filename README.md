# 민원 조회 콘솔

열차 민원 데이터를 **마스킹 → 조회 → 다운로드 → 예측**까지 처리하는 정적 웹 콘솔.
서버 없이 GitHub Pages 로 배포된다.

```
index.html              조회 화면 (의존성 없는 단일 HTML)
data/
  masked_seasonal.csv   마스킹 완료 데이터 — 이 폴더에는 audit 0건인 파일만 둔다
  forecast.json         추석 실적 기반 설 연휴 예측 결과
tools/
  pii_mask.py           마스킹 엔진 + 테이블 파이프라인 (표준 라이브러리만)
  make_seasonal.py      계절성 더미데이터 생성기
  forecast.py           추석 → 설 예측 모듈
  MASKING_POLICY.md     마스킹 기준 (T1/T2/T3 분류, 우선순위, 운영 게이트)
```

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

# 4) 예측 산출
python3 tools/forecast.py -i data/masked_seasonal.csv -o data/forecast.json
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
