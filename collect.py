"""공개 처리기간 수집기.

IRCC·ESDC 공식 페이지에서 처리기간을 가져와 앱이 읽는 JSON 한 개로 정리한다.
개인 정보는 다루지 않는다. 표준 라이브러리만 사용 (GitHub Actions에서 바로 실행).

사용: python3 collector/collect.py [출력 폴더] [--from-dir 내려받은_파일_폴더]
     (기본 출력: collector/public)
     --from-dir: 이미 내려받은 공식 파일로 정리만 한다 (ptime.json / flpt.json / lmia.html)
"""
import datetime as dt
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

IRCC_PTIME = "https://www.canada.ca/content/dam/ircc/documents/json/data-ptime-non-country-en.json"
IRCC_FLPT = "https://www.canada.ca/content/dam/ircc/documents/json/flpt-en.json"
ESDC_LMIA = "https://www.canada.ca/en/employment-social-development/services/foreign-workers/labour-market-impact-assessment-processing-times.html"
IRCC_TOOL = "https://www.canada.ca/en/immigration-refugees-citizenship/services/application/check-processing-times.html"

# 앱 programKey → 공식 데이터 위치
FLPT_PROGRAMS = {
    "ee_cec": "cec",
    "ee_fsw": "fsw",
    "pr_spouse_outland": "spousal-abroad-roc",
    "pr_family_outland": "pgp-roc",
    "citizenship": "citizen-grants",
}
PTIME_PROGRAMS = {
    "pr_card": ("pr_card", "existing_pr"),
}
LMIA_STREAMS = {
    "Global Talent Stream": "global_talent",
    "Agricultural stream": "agricultural",
    "Seasonal Agricultural Worker Program": "sawp",
    "High-wage stream": "high_wage",
    "Low-wage stream": "low_wage",
    "Permanent resident stream": "permanent_resident",
}
LMIA_PROGRAMS = {"lmia_high_wage": "high_wage"}

WAIT_MONTHS_KEPT = 36  # 신청월별 남은 기간은 최근 3년치만


LOCAL_DIR = None
LOCAL_FILES = {IRCC_PTIME: "nc.json", IRCC_FLPT: "flpt.json", ESDC_LMIA: "lmia.html"}


def fetch(url: str) -> str:
    if LOCAL_DIR:
        return (LOCAL_DIR / LOCAL_FILES[url]).read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers={
        "User-Agent": "canada-tracker-collector/1.0 (public processing-time collector)",
        "Accept": "application/json, text/html",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def to_days(text: str):
    """'About 6 months' / '43 days' / '6 weeks' → 일수. 'Not enough data' 등은 None."""
    m = re.search(r"([\d,]+)\s*(day|week|month|year)", text or "", re.I)
    if not m or text.lower().startswith("more than"):
        return None
    n = int(m.group(1).replace(",", ""))
    return round(n * {"day": 1, "week": 7, "month": 30.44, "year": 365.25}[m.group(2).lower()])


def to_int(text: str):
    m = re.search(r"([\d,]+)", text or "")
    return int(m.group(1).replace(",", "")) if m else None


def collect_ircc():
    ptime = json.loads(fetch(IRCC_PTIME))
    flpt = json.loads(fetch(IRCC_FLPT))
    programs, wait = {}, {}

    for key, (group, item) in PTIME_PROGRAMS.items():
        text = ptime.get(group, {}).get(item)
        programs[key] = {"text": text, "days": to_days(text), "source": "ircc_ptime"}

    current, waits, ahead = flpt["current-flpt"], flpt["wait-times"], flpt["people-ahead"]
    for key, fk in FLPT_PROGRAMS.items():
        text = current.get(fk)
        programs[key] = {"text": text, "days": to_days(text), "source": "ircc_flpt", "flptKey": fk}
        months = sorted(k.rsplit("-", 1)[1] for k in waits if k.rsplit("-", 1)[0] == fk)[-WAIT_MONTHS_KEPT:]
        wait[fk] = {
            m: {
                "text": waits[f"{fk}-{m}"],
                "monthsLeft": None if "more time" in waits[f"{fk}-{m}"] else to_int(waits[f"{fk}-{m}"]),
                "ahead": to_int(ahead.get(f"{fk}-{m}", "")),
            }
            for m in months
        }

    sources = {
        "ircc_ptime": {"label": "IRCC 처리기간", "url": IRCC_TOOL, "updated": ptime["default-update"]["lastupdated"]},
        "ircc_flpt": {"label": "IRCC 예상 처리기간", "url": IRCC_TOOL, "updated": flpt["default-update"]["flpt_lastupdated"]},
    }
    return programs, wait, sources


def collect_lmia():
    page = fetch(ESDC_LMIA)
    table = re.search(r"<table.*?</table>", page, re.S).group(0)
    period = re.sub(r"\s+", " ", re.search(r"<caption[^>]*>(.*?)</caption>", table, re.S).group(1)).strip()
    period = re.sub(r"^.*for\s+", "", period)
    streams = {}
    for name, value in re.findall(r"<td>\s*(.*?)\s*</td>\s*<td[^>]*>\s*([\d,]+)\s*</td>", table, re.S):
        if name in LMIA_STREAMS:
            streams[LMIA_STREAMS[name]] = int(value.replace(",", ""))
    if len(streams) != len(LMIA_STREAMS):
        raise ValueError(f"LMIA 표 형식이 바뀌었어요: {streams}")
    programs = {
        key: {
            "text": f"{streams[s]} business days",
            "days": round(streams[s] * 7 / 5),  # 영업일 → 달력일 근사
            "businessDays": streams[s],
            "source": "esdc_lmia",
        }
        for key, s in LMIA_PROGRAMS.items()
    }
    source = {"label": "ESDC LMIA 처리기간", "url": ESDC_LMIA, "updated": period}
    return programs, {"unit": "business_days", "period": period, "streams": streams}, source


def main():
    global LOCAL_DIR
    args = sys.argv[1:]
    if "--from-dir" in args:
        i = args.index("--from-dir")
        LOCAL_DIR = Path(args[i + 1])
        del args[i:i + 2]
    out = Path(args[0]) if args else Path(__file__).parent / "public"
    (out / "v1").mkdir(parents=True, exist_ok=True)

    programs, wait, sources = collect_ircc()
    lmia_programs, lmia, lmia_source = collect_lmia()
    programs.update(lmia_programs)
    sources["esdc_lmia"] = lmia_source

    missing = [k for k, v in programs.items() if v["days"] is None]
    data = {
        "schemaVersion": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": sources,
        "programs": programs,
        "waitByMonth": wait,
        "lmia": lmia,
    }
    (out / "v1" / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # 기한 규칙도 같은 곳에서 내려준다 (앱 업데이트 없이 규칙 교체용)
    # 앱의 assets/deadline-rules.v1.json 과 같은 내용을 유지한다 (규칙을 바꾸면 두 곳 다 갱신)
    rules = Path(__file__).parent / "deadline-rules.json"
    shutil.copy(rules, out / "v1" / "deadline-rules.json")

    print(f"programs {len(programs)} · waitByMonth {sum(len(v) for v in wait.values())} · 값 없음 {missing or '없음'}")


if __name__ == "__main__":
    main()
