"""공개 처리기간 수집기.

IRCC·ESDC 공식 페이지에서 처리기간을 가져와 앱이 읽는 JSON 한 개로 정리한다.
개인 정보는 다루지 않는다. 표준 라이브러리만 사용 (GitHub Actions에서 바로 실행).

사용: python3 collector/collect.py [출력 폴더] [--from-dir 내려받은_파일_폴더]
     (기본 출력: collector/public)
     --from-dir: 이미 내려받은 공식 파일로 정리만 한다 (ptime.json / flpt.json / lmia.html)
"""
import datetime as dt
import html
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

IRCC_PTIME = "https://www.canada.ca/content/dam/ircc/documents/json/data-ptime-non-country-en.json"
IRCC_PTIME_COUNTRY = "https://www.canada.ca/content/dam/ircc/documents/json/data-ptime-en.json"
IRCC_COUNTRY_NAMES = "https://www.canada.ca/content/dam/ircc/documents/json/data-country-name-en.json"
# 지난번에 올린 처리기간 기록. 사이트는 매번 통째로 바뀌므로 먼저 받아 와서 이어 쓴다
HISTORY_URL = "https://jintaelim.github.io/canada-tracker-data/v1/history.json"
IRCC_FLPT = "https://www.canada.ca/content/dam/ircc/documents/json/flpt-en.json"
ESDC_LMIA = "https://www.canada.ca/en/employment-social-development/services/foreign-workers/labour-market-impact-assessment-processing-times.html"
IRCC_EE = "https://www.canada.ca/content/dam/ircc/documents/json/ee_rounds_123_en.json"
EE_PAGE = "https://www.canada.ca/en/immigration-refugees-citizenship/corporate/mandate/policies-operational-instructions-agreements/ministerial-instructions/express-entry-rounds.html"
IRB_RPD = "https://www.irb-cisr.gc.ca/en/statistics/protection/Pages/RPDStat{year}.aspx"
# 캐나다 정부 공식 뉴스 피드 (Atom). 제목·요약·날짜·링크만 쓴다
GC_NEWS = "https://api.io.canada.ca/io-server/gc/news/en/v2?dept={dept}&sort=publishedDate&orderBy=desc&pick=40&format=atom"
# IRB는 이 피드에 뉴스를 올리지 않는다 (2026-11 확인). IRCC만
NEWS_DEPTS = {"IRCC": "departmentofcitizenshipandimmigration"}
NEWS_DAYS = 60
IRCC_TOOL = "https://www.canada.ca/en/immigration-refugees-citizenship/services/application/check-processing-times.html"

# 앱 programKey → 공식 데이터 위치
FLPT_PROGRAMS = {
    "ee_cec": "cec",
    "ee_fsw": "fsw",
    "pnp_ee": "pnp-ee",
    "pnp_base": "pnp-base",
    "qsw": "qsw",
    "aip": "aip",
    "caregiver": "caregiver",
    "startup_visa": "startup-visa",
    "self_employed": "self-employed",
    "quebec_business": "quebec-business",
    "pr_spouse_inland": "spousal-canada-roc",
    "pr_spouse_outland": "spousal-abroad-roc",
    "pr_family_outland": "pgp-roc",
    "pr_dependant_outland": "dependants-abroad-roc",
    "protected_persons": "protected-persons-in-canada-roc",
    "humanitarian": "humanitarian-roc",
    "citizenship": "citizen-grants",
    "citizenship_proof": "citizen-proofs",
}
PTIME_PROGRAMS = {
    "pr_card": ("pr_card", "existing_pr"),
    "pr_card_new": ("pr_card", "new_pr"),
    "visitor_inland": ("visitor_inside_canada", "visitor_inside_canada"),
    "visitor_extension": ("visitor_extension", "visitor_extension"),
    "sp_extension": ("study_extension", "study_extension"),
    "wp_extension": ("work_extension", "work_extension"),
    "iec": ("iec", "iec"),
    "sawp": ("sawp", "sawp_current"),
    "ee_fst": ("fed_skilled_trades", "skilled_trades_ee"),
}
# 신청자 국가마다 다른 처리기간 (앱 programKey → 공식 파일 묶음)
COUNTRY_PROGRAMS = {
    "visitor_outland": "visitor-outside-canada",
    "super_visa": "supervisa",
    "sp_outland": "study",
    "wp_outland": "work",
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
EE_ROUNDS_KEPT = 200  # Express Entry 초청 회차 (약 2년치, 종류별 비교용)
HISTORY_POINTS_KEPT = 260  # 처리기간 기록은 계열마다 최근 260번 (주 1회 갱신이면 약 5년)


LOCAL_DIR = None
LOCAL_FILES = {
    IRCC_PTIME: "nc.json", IRCC_FLPT: "flpt.json", ESDC_LMIA: "lmia.html", IRCC_EE: "ee.json",
    IRB_RPD.format(year=2025): "rpd2025.html", IRB_RPD.format(year=2026): "rpd2026.html",
    GC_NEWS.format(dept=NEWS_DEPTS["IRCC"]): "news-ircc.xml",
    IRCC_PTIME_COUNTRY: "country.json", IRCC_COUNTRY_NAMES: "country-names.json", HISTORY_URL: "history.json",
}


def fetch(url: str) -> str:
    if LOCAL_DIR:
        f = LOCAL_DIR / LOCAL_FILES[url]
        if not f.exists():
            raise FileNotFoundError(f)
        return f.read_text(encoding="utf-8-sig", errors="replace")
    req = urllib.request.Request(url, headers={
        "User-Agent": "canada-tracker-collector/1.0 (public processing-time collector)",
        "Accept": "application/json, text/html",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig", errors="replace")


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


MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]


def iso_date(text: str):
    """'November 4, 2026' → '2026-11-04', 'September 2026' → '2026-09-01'. 모르면 None."""
    m = re.match(r"^\s*([A-Za-z]+)\s+(?:(\d{1,2}),\s*)?(\d{4})\s*$", text or "")
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return f"{m.group(3)}-{MONTHS.index(m.group(1).lower()) + 1:02d}-{int(m.group(2) or 1):02d}"


def collect_countries():
    """국가별 처리기간: {programKey: {국가코드: 일수}}. 처리기간이 없는 나라는 뺀다."""
    raw = json.loads(fetch(IRCC_PTIME_COUNTRY))
    names = json.loads(fetch(IRCC_COUNTRY_NAMES))["country-name"]
    by_country, updated = {}, None
    for key, group in COUNTRY_PROGRAMS.items():
        values = {}
        for code, text in raw.get(group, {}).items():
            if not isinstance(text, str):
                continue
            if code == "lastupdated" or iso_date(text):  # 묶음 안에 날짜가 섞여 있다
                updated = updated or text
                continue
            days = to_days(text)
            if days is not None and code in names:
                values[code] = days
        by_country[key] = values
    used = sorted({c for v in by_country.values() for c in v})
    source = {"label": "IRCC 국가별 처리기간", "url": IRCC_TOOL, "updated": updated or ""}
    # 공식 파일에 '&rsquo;' 같은 HTML 글자가 섞여 있다
    return by_country, {c: html.unescape(names[c]) for c in used}, source


def series_points(programs, by_country, sources):
    """이번에 확인한 값: {계열키: (공식 갱신일, 일수)}. 국가별은 '프로그램키__국가코드'."""
    out = {}
    for key, p in programs.items():
        day = iso_date(sources.get(p["source"], {}).get("updated", ""))
        if day and p.get("days") is not None:
            out[key] = (day, p["days"])
    day = iso_date(sources.get("ircc_ptime_country", {}).get("updated", ""))
    if day:
        for key, values in by_country.items():
            for code, days in values.items():
                out[f"{key}__{code}"] = (day, days)
    return out


def load_history():
    """지난번 기록. 처음이면(파일 없음) 빈 기록, 받다가 실패하면 멈춘다 (기록이 지워지지 않게)."""
    try:
        return json.loads(fetch(HISTORY_URL)).get("series", {})
    except FileNotFoundError:
        return {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise


def update_history(series, points):
    """공식 갱신일이나 값이 바뀌었을 때만 한 줄 더한다. 같은 갱신일이면 마지막 줄을 고친다."""
    added = 0
    for key, (day, days) in points.items():
        rows = series.setdefault(key, [])
        if rows and rows[-1][0] == day:
            rows[-1][1] = days
        elif not rows or rows[-1][1] != days or rows[-1][0] < day:
            if rows and rows[-1][0] > day:
                continue  # 더 옛날 값은 무시
            rows.append([day, days])
            added += 1
        del rows[:-HISTORY_POINTS_KEPT]
    return added


def write_history(out: Path, series):
    (out / "v1" / "history").mkdir(parents=True, exist_ok=True)
    (out / "v1" / "history.json").write_text(json.dumps({"schemaVersion": 1, "series": series}, separators=(",", ":")), encoding="utf-8")
    # 앱은 보는 계열 하나만 받는다
    for key, rows in series.items():
        (out / "v1" / "history" / f"{key}.json").write_text(json.dumps({"key": key, "points": rows}, separators=(",", ":")), encoding="utf-8")


def _num(cell: str):
    """'1,388' → 1388, '--'(10건 미만 등 비공개) → None"""
    c = cell.replace(",", "").strip()
    return int(c) if c.isdigit() else None


def collect_irb(today: dt.date):
    """IRB 난민보호과(RPD) 국가별 결정 통계: 올해(연중)와 작년. 개인 정보 없음."""
    cols = ["referred", "accepted", "rejected", "abandoned", "withdrawn", "finalized", "pending"]
    years = {}
    for year in (today.year, today.year - 1):
        url = IRB_RPD.format(year=year)
        try:
            page = fetch(url)
        except Exception as e:  # noqa: BLE001  올해 표가 아직 없을 수 있다
            print(f"IRB {year} 통계 없음: {e}")
            continue
        table = re.search(r"<table.*?</table>", page, re.S)
        if not table:
            continue
        table = table.group(0)
        cap = re.search(r"<caption[^>]*>(.*?)</caption>", table, re.S)
        caption = html.unescape(re.sub(r"<[^>]+>|\s+|\u200b", " ", cap.group(1) if cap else "")).strip()
        caption = re.sub(r"\s+", " ", caption)
        head = re.search(r"Pending \(as of ([\d/]+)\)", html.unescape(re.sub(r"<[^>]+>", " ", table)))
        rows, total = {}, None
        for tr in re.findall(r"<tr.*?</tr>", table, re.S):
            cells = [html.unescape(re.sub(r"<[^>]+>|\s+", " ", c)).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
            if len(cells) != 8 or not cells[0] or cells[1] == "Accepted":
                continue
            vals = dict(zip(cols, (_num(c) for c in cells[1:])))
            if cells[0] == "Total":
                total = vals
            elif cells[0] != "(Blank)":
                rows[cells[0]] = vals
        if total and rows:
            years[str(year)] = {
                "period": re.sub(r"^Claims by Country of Alleged Persecution\s*[–-]\s*", "", caption),
                "pendingAsOf": head.group(1) if head else None,
                "url": url,
                "total": total,
                "byCountry": rows,
            }
    if not years:
        raise ValueError("IRB 통계 표를 찾지 못했어요")
    source = {"label": "IRB 난민 심사 통계", "url": IRB_RPD.format(year=max(int(y) for y in years)), "updated": years[max(years)]["period"]}
    return {"rpd": years}, source


def collect_news(today: dt.date):
    """IRCC 공식 뉴스 최근 60일. 행사 일정 안내(media advisories)는 뺀다."""
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom"}
    since = today - dt.timedelta(days=NEWS_DAYS)
    items = []
    for source, dept in NEWS_DEPTS.items():
        try:
            feed = ET.fromstring(fetch(GC_NEWS.format(dept=dept)))
        except Exception as e:  # noqa: BLE001
            print(f"{source} 뉴스 수집 실패: {e}")
            continue
        for e in feed.findall("a:entry", ns):
            kind = (e.find("a:category", ns).get("term") if e.find("a:category", ns) is not None else "").lower()
            if kind == "media advisories":
                continue
            day = (e.findtext("a:updated", "", ns) or "")[:10]
            if not day or dt.date.fromisoformat(day) < since:
                continue
            link = e.find("a:link", ns)
            title = html.unescape((e.findtext("a:title", "", ns) or "").strip())
            summary = html.unescape(re.sub(r"<[^>]+>", " ", e.findtext("a:summary", "", ns) or "")).strip()
            summary = re.sub(r"\s+", " ", summary)
            items.append({
                "source": source,
                "type": kind,
                "date": day,
                "title": title,
                "summary": "" if summary == title else summary[:400],
                "url": link.get("href") if link is not None else "",
            })
    items.sort(key=lambda x: x["date"], reverse=True)
    return items


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


def collect_ee():
    rounds = json.loads(fetch(IRCC_EE))["rounds"][:EE_ROUNDS_KEPT]
    out = [
        {"date": r["drawDate"], "name": r["drawName"], "size": to_int(r["drawSize"]), "crs": to_int(r["drawCRS"])}
        for r in rounds
    ]
    source = {"label": "IRCC Express Entry 초청 회차", "url": EE_PAGE, "updated": rounds[0]["drawDateFull"]}
    return {"rounds": out}, source


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

    # 초청 회차는 부가 정보라 실패해도 처리기간 갱신은 계속한다
    try:
        ee, ee_source = collect_ee()
        sources["ircc_ee"] = ee_source
    except Exception as e:  # noqa: BLE001
        print(f"Express Entry 수집 실패: {e}")
        ee = None

    try:
        by_country, countries, country_source = collect_countries()
        sources["ircc_ptime_country"] = country_source
    except Exception as e:  # noqa: BLE001
        print(f"국가별 처리기간 수집 실패: {e}")
        by_country, countries = None, None

    # 난민 심사 통계도 부가 정보라 실패해도 계속한다
    try:
        refugee, refugee_source = collect_irb(dt.date.today())
        sources["irb_rpd"] = refugee_source
    except Exception as e:  # noqa: BLE001
        print(f"IRB 통계 수집 실패: {e}")
        refugee = None

    news = collect_news(dt.date.today())
    (out / "v1").mkdir(parents=True, exist_ok=True)
    (out / "v1" / "news.json").write_text(json.dumps({
        "schemaVersion": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": news,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"뉴스 {len(news)}건")

    history = load_history()
    added = update_history(history, series_points(programs, by_country or {}, sources))
    write_history(out, history)

    missing = [k for k, v in programs.items() if v["days"] is None]
    data = {
        "schemaVersion": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": sources,
        "programs": programs,
        "waitByMonth": wait,
        "lmia": lmia,
    }
    if ee:
        data["expressEntry"] = ee
    if refugee:
        data["refugee"] = refugee
    if by_country:
        data["byCountry"] = by_country
        data["countries"] = countries
    (out / "v1" / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    # 기한 규칙도 같은 곳에서 내려준다 (앱 업데이트 없이 규칙 교체용)
    # 앱의 assets/deadline-rules.v1.json 과 같은 내용을 유지한다 (규칙을 바꾸면 두 곳 다 갱신)
    rules = Path(__file__).parent / "deadline-rules.json"
    shutil.copy(rules, out / "v1" / "deadline-rules.json")

    print(f"history 계열 {len(history)} · 새 기록 {added}")
    print(f"programs {len(programs)} · waitByMonth {sum(len(v) for v in wait.values())} · 값 없음 {missing or '없음'}")


if __name__ == "__main__":
    main()
