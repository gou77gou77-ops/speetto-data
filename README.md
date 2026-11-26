# speetto-data

스피또 오라클 앱(com.susuim.speetto)이 받는 공개 데이터와 그 수집기.
동행복권 공개 페이지에서 스피또 발행내역·판매점·1등 배출점을 모아 JSON으로 만들고 GitHub Pages에 올린다.
본 저장소와 데이터는 개인이 만든 것이며 동행복권과 무관합니다.

> 지금은 앱 저장소(speetto)의 `server/` 폴더에 있다. 이 폴더의 내용을 그대로 speetto-data 저장소의 루트로 옮긴다(앱 저장소 docs/github-setup.md).

## 폴더

```
collector/
  check.py        동행복권 접속 확인 (워크플로 첫 단계)
  rounds.py       회차 목록 + 상세 + 회차별 1등 배출점 → rounds_latest.json
  stores.py       전국 판매점 → stores.json (시군구 하나만 받는 시험 옵션)
  scoring.py      오라클 지수, 상태(active/endgame/ended), 라벨(grade) — 점수 규칙의 기준
  contract.py     출력 형식 검사. 어긋나면 파일을 쓰지 않는다
  dhlottery.py    요청 공통: User-Agent, 1초 1건, 재시도, 차단·형식 오류 구분
  juso.py         행안부 도로명주소 API 지오코딩 (키가 없으면 건너뜀)
  geo.py          좌표 범위, 거리, 도로명 비교, UTM-K → WGS84
  output.py       파일을 다 만든 뒤 한 번에 바꾸기, Actions 오류 표시
tests/            python -m unittest discover -s tests (동행복권을 부르지 않음)
tests/fixtures/   2026-11-26 실제 수집 결과 (배출점·판매점 수만 줄임)
public/config.json  앱 원격 설정. 손으로 고치는 유일한 공개 파일
.github/workflows/  rounds.yml, stores.yml, test.yml
requirements.txt  (지금은 표준 라이브러리만)
```

## 공개 파일 (gh-pages 브랜치 → GitHub Pages)

| 파일 | 만드는 작업 | 주기 (KST) | cron (UTC) |
|---|---|---|---|
| `rounds_latest.json` | 회차 발행내역 + 점수 + 회차별 1등 배출점 | 매일 09:00, 18:00 | `0 0 * * *`, `0 9 * * *` |
| `stores.json` | 전국 판매점 목록(스피또 판매점만) | 매주 일요일 03:00 | `0 18 * * 6` (토요일 18:00 UTC) |
| `config.json` | `public/config.json`을 복사 | 위 작업이 돌 때마다 | — |

- 각 작업은 gh-pages 브랜치를 받아 **자기 파일만** 바꿔 커밋한다. 실패하면 커밋하지 않으므로 지난번 파일이 그대로 남는다.
- 두 작업이 동시에 push하지 않도록 같은 `concurrency` 그룹을 쓴다.
- 형식: 앱 저장소 docs/data-contract.md. 요청 주소와 응답 필드: docs/data-source.md.

## 수집 규칙

- 동행복권 요청은 **1초에 1건 이하**. User-Agent는 `SpeettoDataCollector/1.0 (com.susuim.speetto; <연락처>)`. 연락처는 코드에 적지 않고 저장소 Variable `COLLECTOR_CONTACT`에서 읽는다.
- 판매점 전체 목록(약 1,448번 요청, 약 25분)은 **끝까지 다 받았을 때만** stores.json을 바꾼다. 중간에 하나라도 실패하면 지난주 파일을 유지한다.
- 좌표가 비었거나 한국 범위(위도 33~39, 경도 124~132) 밖인 판매점만 행안부 도로명주소 API로 좌표를 구한다. 키는 Actions secret `JUSO_API_KEY`. 행안부로도 못 찾으면 `lat`/`lng`를 null로 둔다(앱은 목록에만 표시). Nominatim 등 다른 지오코더는 쓰지 않는다.
- 1등 배출점은 최신 stores.json과 **좌표 30 m 이내 + 도로명 주소 앞부분 일치**로 짝을 짓는다. 짝이 없으면 배출 데이터의 좌표를 쓰고 `inStoreList: false`("현재 판매점 목록에 없음").

## 명령

```
python -m collector.check --target rounds        # 접속 확인 (stores도 있음)
python -m collector.stores --sido 서울 --sigungu 서초구   # 시험: out/stores.sample.json (약 10초)
python -m collector.stores                        # 전국: out/stores.json (약 25분)
python -m collector.rounds --stores out/stores.sample.json   # out/rounds_latest.json (약 75초)
python -m unittest discover -s tests
```

- `out/`은 커밋하지 않는다. 워크플로에서는 `out/`이 gh-pages 브랜치를 받은 폴더다.
- 시군구 시험 결과는 `stores.json`이라는 이름으로 쓸 수 없다(전국 파일을 덮어쓰지 않게).
- 환경 변수: `COLLECTOR_CONTACT`(User-Agent 연락처), `JUSO_API_KEY`, `JUSO_COORD_API_KEY`(좌표제공 API 키가 따로일 때). 키가 없으면 지오코딩만 건너뛴다.
- Windows 콘솔에서 한글이 깨지면 `PYTHONIOENCODING=utf-8`.

| 종료 코드 | 뜻 | 기존 JSON |
|---|---|---|
| 0 | 성공 | 새 파일로 교체 |
| 2 | 접속 차단 (HTTP 403·429, 연결 거부·끊김, 시간 초과, JSON 대신 HTML·점검 페이지) | 그대로 |
| 3 | 응답 형식 변경, 목록을 끝까지 못 받음, 판매점이 10% 넘게 줄어듦, 출력 형식 검사 실패 | 그대로 |
| 4 | (rounds) 짝지을 stores.json이 없음 → stores 워크플로를 먼저 | 그대로 |
