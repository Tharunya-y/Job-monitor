#!/usr/bin/env python3
"""
=============================================================================
 EARLY-CAREER RTL / ASIC / DIGITAL-DESIGN JOB MONITOR  (USA only)
=============================================================================
 Polls company career APIs every time it runs, keeps only USA + early-career
 roles that match your resume keywords, remembers what it has already seen,
 and EMAILS you only the NEW matches. Free to run hourly (GitHub Actions or
 your PC's Task Scheduler / cron).

 -------- WHAT YOU MUST DO (only 2 things) -----------------------------------
 1) Install the one dependency:            pip install requests
 2) Provide your email login. EASIEST = edit the EMAIL block just below.
    (More secure = leave them blank and set environment variables /
     GitHub Secrets named EMAIL_ADDRESS, EMAIL_APP_PASSWORD, EMAIL_TO.)
    Your Gmail "App Password" is a free 16-char code from:
    Google Account -> Security -> 2-Step Verification -> App Passwords.
 -----------------------------------------------------------------------------
 Then just run:   python job_monitor_ready.py
=============================================================================
"""

import os
import json
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
import requests

# =============================================================================
# 1. EMAIL  ---  fill these in (or leave blank and use environment variables)
# =============================================================================
EMAIL_ADDRESS      = "tharunya6161@gmail.com"   # e.g. "youraddress@gmail.com"   (sender)
EMAIL_APP_PASSWORD = "ljzz oybr vhrd wubn"   # e.g. "abcd efgh ijkl mnop"      (Gmail App Password)
EMAIL_TO           = "tharunya6161@gmail.com"   # where alerts go; blank = same as EMAIL_ADDRESS

# =============================================================================
# 2. WHAT TO MATCH  ---  already tuned to your resume; edit if you like
# =============================================================================
# A role is kept only if its TITLE contains one of these:
RESUME_KEYWORDS = [
    "rtl", "asic", "digital design", "logic design", "soc", "cpu", "gpu",
    "dma", "verification", "design verification", "dv engineer",
    "hardware design", "physical design", "memory design", "dram",
    "power", "architecture", "design engineer", "silicon",
]

# Early-career only: roles whose title contains any of these are DROPPED.
SENIOR_TERMS = [
    "senior", "sr.", "sr ", "principal", "staff", "lead ", " lead",
    "manager", "director", "architect ii", " iii", " iv", " v ",
    "distinguished", "fellow",
]

# Nice-to-have signal (used only to label a match as a strong early-career hit)
EARLY_CAREER_TERMS = [
    "new grad", "new college grad", "college grad", "university grad",
    "university graduate", "new graduate", "recent graduate", "early career",
    "entry level", "entry-level", "associate", "campus", "graduate",
    "grad 2026", "grad 2025", "grad 2027", "grad 2028",
]

# STRICT MODE: keep ONLY roles whose TITLE says early-career / new-grad
# (e.g. NVIDIA's "... - New College Grad 2026"). This is what removes the
# "2+ years of experience" roles you were getting. Set False to loosen.
REQUIRE_EARLY_CAREER = True

# =============================================================================
# 3. COMPANIES  ---  NVIDIA + Amazon work as-is. Add others (see NOTES at end).
# =============================================================================
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0 (job-monitor)",
           "Accept-Encoding": "gzip, deflate"}   # avoid zstd (requests can't decode it)
SEEN_FILE = Path("seen_jobs.json")

US_STATES = {
    "alabama","alaska","arizona","arkansas","california","colorado","connecticut",
    "delaware","florida","georgia","hawaii","idaho","illinois","indiana","iowa",
    "kansas","kentucky","louisiana","maine","maryland","massachusetts","michigan",
    "minnesota","mississippi","missouri","montana","nebraska","nevada","new hampshire",
    "new jersey","new mexico","new york","north carolina","north dakota","ohio",
    "oklahoma","oregon","pennsylvania","rhode island","south carolina","south dakota",
    "tennessee","texas","utah","vermont","virginia","washington","west virginia",
    "wisconsin","wyoming",
    " al "," az "," ar "," ca "," co "," ct "," fl "," ga "," id "," il "," in ",
    " ks "," ky "," la "," ma "," md "," mi "," mn "," mo "," nc "," nh "," nj ",
    " nm "," nv "," ny "," oh "," or "," pa "," sc "," tn "," tx "," ut "," va ",
    " wa "," wi ",
    "united states","usa","u.s.","us,","(us)",
}


def fetch_workday(tenant, site, host, search_text):
    """Workday CXS endpoint. NVIDIA uses this; Micron/others may too."""
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    out, offset = [], 0
    while True:
        body = {"appliedFacets": {}, "limit": 20, "offset": offset,
                "searchText": search_text}
        r = requests.post(url, json=body, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        posts = data.get("jobPostings", [])
        if not posts:
            break
        for p in posts:
            path = p.get("externalPath", "")
            out.append({
                "id": path,
                "title": (p.get("title") or "").strip(),
                "location": p.get("locationsText", ""),
                "url": f"https://{host}/en-US/{site}{path}",
            })
        offset += 20
        if offset >= data.get("total", 0) or offset >= 100:
            break
    return out


def fetch_amazon(query):
    """Amazon.jobs JSON search, restricted to the USA. Covers Annapurna Labs."""
    url = "https://www.amazon.jobs/en/search.json"
    params = {"base_query": query, "country": "USA",
              "result_limit": 100, "sort": "recent"}
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append({
            "id": j.get("id_icims") or j.get("job_path"),
            "title": (j.get("title") or "").strip(),
            "location": j.get("normalized_location", ""),
            "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
        })
    return out


def fetch_greenhouse(company):
    url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "id": str(j.get("id")),
        "title": (j.get("title") or "").strip(),
        "location": (j.get("location") or {}).get("name", ""),
        "url": j.get("absolute_url", ""),
    } for j in r.json().get("jobs", [])]


def fetch_lever(company):
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "id": j.get("id"),
        "title": (j.get("text") or "").strip(),
        "location": (j.get("categories") or {}).get("location", ""),
        "url": j.get("hostedUrl", ""),
    } for j in r.json()]


def fetch_phenom(host, domain, query):
    """Phenom People sites (Micron, Qualcomm) via the /api/pcsx/search API."""
    base = f"https://{host}/api/pcsx/search"
    out, start = [], 0
    while True:
        params = {"domain": domain, "query": query, "location": "",
                  "start": start, "sort_by": "relevance"}
        r = requests.get(base, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        payload = payload.get("data", payload)
        jobs = (payload.get("jobs") or payload.get("positions")
                or payload.get("results") or payload.get("hits") or [])
        if not jobs:
            break
        for d in jobs:
            d = d.get("job", d) if isinstance(d.get("job", None), dict) else d
            loc = (d.get("cityStateCountry") or d.get("cityState")
                   or d.get("location")
                   or ", ".join(x for x in [d.get("city"), d.get("state"),
                                            d.get("country")] if x))
            jid = str(d.get("jobId") or d.get("reqId") or d.get("id")
                      or d.get("jobSeqNo") or d.get("ml_job_id") or "")
            jurl = (d.get("jobUrl") or d.get("url") or d.get("applyUrl")
                    or d.get("apply_url") or "")
            if jurl.startswith("/"):
                jurl = f"https://{host}{jurl}"
            if not jurl and jid:
                jurl = f"https://{host}/careers?pid={jid}&domain={domain}"
            out.append({"id": jid, "title": (d.get("title") or "").strip(),
                        "location": loc, "url": jurl})
        start += len(jobs)
        total = payload.get("totalHits") or payload.get("count") or 0
        if start >= total or start >= 120:
            break
    return out


def fetch_phenom_jobs(host, keywords):
    """Older Phenom /api/jobs?keywords= style (AMD)."""
    url = f"https://{host}/api/jobs"
    out, page = [], 1
    while True:
        params = {"keywords": keywords, "page": page,
                  "sortBy": "relevance", "internal": "false"}
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
        if not jobs:
            break
        for entry in jobs:
            d = entry.get("data", entry)
            loc = ", ".join(x for x in [d.get("city"), d.get("state"),
                                        d.get("country")] if x)
            out.append({
                "id": str(d.get("req_id") or d.get("jobId") or d.get("slug") or ""),
                "title": (d.get("title") or "").strip(),
                "location": loc,
                "url": d.get("apply_url") or d.get("job_seo_url") or "",
            })
        page += 1
        if page > 8 or len(jobs) < 10:
            break
    return out


def fetch_apple(query=None):
    """Apple jobs via the /api/v1/quickfind/<term> GET endpoint (no CSRF).
    Queries several terms and merges/dedupes the results."""
    terms = ["rtl", "asic", "design verification", "soc",
             "digital design", "silicon"]
    out, seen = [], set()
    for term in terms:
        url = f"https://jobs.apple.com/api/v1/quickfind/{requests.utils.quote(term)}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception:
            continue
        results = (data.get("results") or data.get("jobs")
                   or data.get("searchResults") or [])
        if isinstance(results, dict):
            results = results.get("jobs") or results.get("items") or []
        for j in results:
            if not isinstance(j, dict):
                continue
            pid = str(j.get("positionId") or j.get("id") or j.get("reqNo") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            locs = j.get("locations")
            if isinstance(locs, list):
                loc = ", ".join(l.get("name", "") if isinstance(l, dict)
                                else str(l) for l in locs)
            else:
                loc = j.get("location") or ""
            title = (j.get("postingTitle") or j.get("title")
                     or j.get("name") or "")
            out.append({"id": pid, "title": title.strip(),
                        "location": loc or "United States",
                        "url": f"https://jobs.apple.com/en-us/details/{pid}"})
    return out


def fetch_ashby(org):
    """Ashby job-board API (many AI-hardware startups, e.g. Etched)."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{org}"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return [{
        "id": j.get("id"),
        "title": (j.get("title") or "").strip(),
        "location": j.get("location", ""),
        "url": j.get("jobUrl", ""),
    } for j in r.json().get("jobs", [])]


def fetch_oracle(host, site_number, keyword):
    """Oracle Cloud Recruiting (e.g., Texas Instruments careers.ti.com).
    Best-effort: if 0/err, grab the recruitingCEJobRequisitions request from
    DevTools and adjust site_number / params."""
    url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    params = {"onlyData": "true",
              "expand": "requisitionList.secondaryLocations",
              "finder": (f"findReqs;siteNumber={site_number},keyword={keyword},"
                         "sortBy=POSTING_DATES_DESC")}
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for block in r.json().get("items", []):
        for d in block.get("requisitionList", []):
            jid = str(d.get("Id") or d.get("RequisitionId") or "")
            out.append({
                "id": jid,
                "title": (d.get("Title") or "").strip(),
                "location": d.get("PrimaryLocation", ""),
                "url": f"https://{host}/en/sites/CX/job/{jid}",
            })
    return out


# ---- Companies to watch. NVIDIA + Marvell (Workday) and Amazon are solid.
#      Micron/Qualcomm (Phenom), Apple, Etched (Ashby) are BEST-EFFORT: run
#      once and read the printout - a "[warn] ... failed" line means we need to
#      grab that one's exact endpoint from DevTools (recipe in NOTES). ---------
SOURCES = [
    ("NVIDIA", lambda: fetch_workday(
        "nvidia", "NVIDIAExternalCareerSite",
        "nvidia.wd5.myworkdayjobs.com", "RTL ASIC design verification")),

    ("Marvell", lambda: fetch_workday(
        "marvell", "MarvellCareers",
        "marvell.wd1.myworkdayjobs.com", "RTL ASIC design verification")),

    ("Amazon/Annapurna", lambda: fetch_amazon("Annapurna")),

    ("Micron",   lambda: fetch_phenom("careers.micron.com", "micron.com", "RTL ASIC design")),
    ("Qualcomm", lambda: fetch_phenom("careers.qualcomm.com", "qualcomm.com", "RTL design")),
    ("Apple",    lambda: fetch_apple("RTL ASIC design verification")),
    ("Etched",   lambda: fetch_ashby("etched")),

    ("AMD", lambda: fetch_phenom_jobs("careers.amd.com", "asic rtl design")),
    ("Texas Instruments", lambda: fetch_oracle(
        "edbz.fa.us2.oraclecloud.com", "CX_1", "rtl asic design")),

    # Custom GraphQL/APIs - add via the DevTools recipe in NOTES at the bottom:
    #   Meta   -> www.metacareers.com/graphql
    #   Google -> google.com/about/careers/applications/.../api
    #   Cisco  -> careers.cisco.com  (Eightfold: /api/apply/v2/jobs)
]

# =============================================================================
# CORE LOGIC  ---  you shouldn't need to touch anything below here
# =============================================================================
def _has(text, terms):
    t = f" {text.lower()} "
    return any(term in t for term in terms)


def is_usa(location):
    if not location:
        return True                     # some feeds omit location; don't drop
    return _has(location, US_STATES)


def matches_resume(title):
    return _has(title, RESUME_KEYWORDS)


def is_senior(title):
    return _has(title, SENIOR_TERMS)


def is_early_career(title):
    return _has(title, EARLY_CAREER_TERMS)


def keep(job):
    if not (matches_resume(job["title"])
            and is_usa(job["location"])
            and not is_senior(job["title"])):
        return False
    if REQUIRE_EARLY_CAREER and not is_early_career(job["title"]):
        return False
    return True


def load_seen():
    return set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen)))


def send_email(new_jobs):
    sender = EMAIL_ADDRESS or os.environ.get("EMAIL_ADDRESS", "")
    password = EMAIL_APP_PASSWORD or os.environ.get("EMAIL_APP_PASSWORD", "")
    to = EMAIL_TO or os.environ.get("EMAIL_TO", "") or sender
    if not sender or not password:
        print("[!] Email not configured - printing instead:\n")
        for company, j in new_jobs:
            print(f"  {company}: {j['title']} | {j['location']}\n    {j['url']}")
        return

    lines = []
    for company, j in new_jobs:
        star = " *EARLY CAREER*" if is_early_career(j["title"]) else ""
        lines.append(f"{company}: {j['title']}{star}\n"
                     f"    {j['location']}\n    {j['url']}\n")
    body = f"{len(new_jobs)} new USA early-career match(es):\n\n" + "\n".join(lines)

    msg = EmailMessage()
    msg["Subject"] = f"[Job Alert] {len(new_jobs)} new RTL/ASIC role(s)"
    msg["From"], msg["To"] = sender, to
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465,
                          context=ssl.create_default_context()) as s:
        s.login(sender, password)
        s.send_message(msg)
    print(f"Emailed {len(new_jobs)} new job(s) to {to}.")


def main():
    first_run = not SEEN_FILE.exists()
    seen = load_seen()
    new_jobs = []
    for company, fetch in SOURCES:
        try:
            jobs = fetch()
        except Exception as e:
            print(f"[warn] {company} fetch failed: {e}")
            continue
        for j in jobs:
            if not j.get("id"):
                continue
            jid = f"{company}:{j['id']}"
            if jid in seen:
                continue
            seen.add(jid)               # remember it either way (stay quiet later)
            if keep(j):
                new_jobs.append((company, j))
        print(f"[ok] {company}: {len(jobs)} scanned")

    # FIRST RUN = silent baseline: record everything currently posted but send
    # NO email (those are "old" jobs). From the next run on you're only alerted
    # about postings that appear AFTER this baseline -> apply within the hour.
    if first_run:
        save_seen(seen)
        print(f"Baseline set: {len(seen)} existing jobs recorded, no email sent. "
              "You'll now only be alerted about NEW postings from here on.")
        return

    if new_jobs:
        send_email(new_jobs)
    else:
        print("No new matching jobs this run.")
    save_seen(seen)


if __name__ == "__main__":
    main()

# =============================================================================
# NOTES - HOW TO ADD QUALCOMM / MICRON / GOOGLE (their APIs differ)
# =============================================================================
# 1. Open the company careers page in Chrome and search e.g. "RTL".
# 2. Press F12 -> "Network" tab -> filter "Fetch/XHR".
# 3. Watch for the request that returns the jobs as JSON (click it -> Preview).
# 4. Copy its URL + request body, then write a small fetcher that returns a
#    list of {id, title, location, url} dicts - copy fetch_amazon as a template
#    - and add it to SOURCES above.
#    - Micron & Qualcomm run on "Phenom" (look for a /api/.../jobs POST).
#    - Google: careers.google.com search calls a /api/ endpoint returning JSON.
# The USA + early-career + keyword filtering then applies automatically.
# =============================================================================
