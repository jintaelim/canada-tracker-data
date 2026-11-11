# canada-tracker-data

캐나다 이민·난민 트래커 앱이 읽는 **공개 처리기간 데이터**를 매일 모아 정적 파일로 내려줍니다.
개인 정보는 다루지 않습니다.

- 데이터: `https://jintaelim.github.io/canada-tracker-data/v1/data.json`
- 기한 규칙: `https://jintaelim.github.io/canada-tracker-data/v1/deadline-rules.json`

| 항목 | 출처 |
|---|---|
| 영주권 카드 등 | IRCC processing times (non-country) |
| Express Entry·초청·시민권, 신청월별 남은 기간 | IRCC forward-looking processing times |
| LMIA 종류별 처리기간 | ESDC LMIA processing times |
| 방문·슈퍼비자·스터디·워크 퍼밋(해외) 나라별 처리기간 | IRCC processing times (country) |
| Express Entry 초청 회차 (약 2년치) | IRCC Express Entry rounds |
| 난민 심사(RPD) 출신국별 결정 결과 (올해·작년) | IRB statistics — Claims by Country of Alleged Persecution |

## 처리기간 기록 (`v1/history.json`, `v1/history/<키>.json`)
- IRCC가 값을 바꿀 때마다 한 줄씩 쌓아요. 나라별은 `<프로그램키>__<국가코드>` (예: `wp_outland__KR`).
- 사이트가 매번 통째로 바뀌기 때문에, 수집기는 **지난번에 올린 history.json을 먼저 받아서** 이어 써요.
  - 처음(파일 없음)이면 빈 기록부터 시작.
  - 받다가 다른 오류가 나면 실행을 멈춰요 → 이전 사이트가 그대로 남아 기록이 지워지지 않아요.
- 앱은 보는 계열 파일 하나만 받아요.

매일 14:00 UTC에 GitHub Actions가 `collect.py`를 실행합니다. 공식 페이지 형식이 바뀌면 실행이 실패하고, 이전 데이터가 그대로 남습니다.
