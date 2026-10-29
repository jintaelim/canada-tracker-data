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

매일 14:00 UTC에 GitHub Actions가 `collect.py`를 실행합니다. 공식 페이지 형식이 바뀌면 실행이 실패하고, 이전 데이터가 그대로 남습니다.
