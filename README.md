# speetto-data

스피또 오라클 앱(com.susuim.speetto)이 받는 공개 데이터와 그 수집기.
동행복권 공개 페이지에서 스피또 발행내역·판매점·1등 배출점을 모아 JSON으로 만들고 GitHub Pages에 올린다.
본 저장소와 데이터는 개인이 만든 것이며 동행복권과 무관합니다.

> 지금은 앱 저장소(speetto)의 `server/` 폴더에 있다. 이 폴더의 내용을 그대로 speetto-data 저장소의 루트로 옮긴다(앱 저장소 docs/github-setup.md).

## 폴더

```
collector/scoring.py      오라클 지수, 상태(active/endgame/ended), 라벨(grade) — 점수 규칙의 기준
tests/test_scoring.py     점수 규칙 테스트
public/config.json        앱 원격 설정. 손으로 고치는 유일한 공개 파일
.github/workflows/test.yml  push·PR마다 테스트
requirements.txt          (지금은 표준 라이브러리만)
```

수집기 본체(동행복권 호출 → JSON)와 수집 워크플로는 아직 없다. 만들 때 아래 일정과 규칙을 따른다.

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

- 동행복권 요청은 **1초에 1건 이하**. User-Agent에 이 저장소 이름과 연락처를 넣는다. 연락처는 코드에 적지 않고 저장소 Variable `COLLECTOR_CONTACT`에서 읽는다.
- 판매점 전체 목록(약 1,448번 요청, 약 25분)은 **끝까지 다 받았을 때만** stores.json을 바꾼다. 중간에 하나라도 실패하면 지난주 파일을 유지한다.
- 좌표가 비었거나 한국 범위(위도 33~39, 경도 124~132) 밖인 판매점만 행안부 도로명주소 API로 좌표를 구한다. 키는 Actions secret `JUSO_API_KEY`. 행안부로도 못 찾으면 `lat`/`lng`를 null로 둔다(앱은 목록에만 표시). Nominatim 등 다른 지오코더는 쓰지 않는다.
- 1등 배출점은 최신 stores.json과 **좌표 30 m 이내 + 도로명 주소 앞부분 일치**로 짝을 짓는다. 짝이 없으면 배출 데이터의 좌표를 쓰고 `inStoreList: false`("현재 판매점 목록에 없음").

## 명령

```
python -m unittest discover -s tests
```
