#!/usr/bin/env python3
"""
PM Job Scanner — Belarus
========================
Scans open Project Manager / Head of PMO / Delivery Manager roles in Belarus
via the public HeadHunter API (api.hh.ru), which is the same backend that
powers rabota.by. No scraping, no auth needed for vacancy search.

It deduplicates results, scores them for seniority + fintech/payments fit
(your wheelhouse), and writes a reviewable CSV plus a ranked console summary.

Usage:
    pip install requests
    python pm_job_scanner.py

Then open pm_shortlist.csv in Excel / Google Sheets and filter/sort as you like.

Tune the CONFIG block below to taste.
"""

import csv
import time
import sys
from datetime import datetime

import requests

# ----------------------------------------------------------------------------
# CONFIG — edit these
# ----------------------------------------------------------------------------

# Search phrases. HH matches name + description. Keep them broad; we filter later.
QUERIES = [
    "project manager",
    "руководитель проектов",
    "delivery manager",
    "head of PMO",
    "руководитель проектного офиса",
    "проектный менеджер",
]

# Region. 16 = Belarus (whole country). 1002 = Minsk only.
AREA = 16

# Seniority filter (HH "experience" codes). Empty list = no filter.
#   noExperience | between1And3 | between3And6 | moreThan6
EXPERIENCE = ["between3And6", "moreThan6"]

# Only keep roles whose title looks senior/lead-level. Set to False to keep all.
REQUIRE_SENIOR_TITLE = True
SENIOR_TITLE_TERMS = [
    "senior", "lead", "head", "руководитель", "ведущий",
    "старш", "директор", "pmo", "delivery", "principal",
]

# Roles to exclude (you said NOT product manager, and skip junior).
EXCLUDE_TITLE_TERMS = [
    "product manager", "продакт", "продуктовый",
    "junior", "стажер", "стажёр", "intern", "ассистент",
]

# Fit scoring tuned to Yauheni's CV. Each category has a weight; a role earns
# (weight x number of distinct matching terms in that category). Edit freely.
SCORE_CATEGORIES = {
    # Core domain — strongest signal (ITEXUS fintech, Chivo crypto wallet, AlphaPoint)
    "fintech_payments": (5, [
        "fintech", "финтех", "bank", "банк", "payment", "платеж", "платёж",
        "pos", "эквайринг", "acquiring", "биллинг", "billing", "терминал",
        "процессинг", "processing", "card", "карт", "wallet", "кошел",
        "crypto", "крипто", "blockchain", "блокчейн", "trading", "финанс",
    ]),
    # Telecom / MVNO (Yonder YoMobile MVNO, KZ telecom partner)
    "telecom": (3, [
        "telecom", "телеком", "mvno", "оператор связи", "sim", "voip",
        "роуминг", "roaming",
    ]),
    # PMO / governance — your title sweet spot, less crowded field
    "pmo_governance": (4, [
        "pmo", "governance", "портфел", "portfolio", "проектн", "офис",
        "стандарт", "stage gate", "методолог", "compliance",
    ]),
    # Delivery / release / incident (Head of PMO + delivery-lead work)
    "delivery": (3, [
        "delivery", "релиз", "release", "incident", "инцидент", "sla",
        "devops", "ci/cd", "uptime", "mttr",
    ]),
    # Methods & tools you live in
    "methods_tools": (2, [
        "jira", "confluence", "scrum", "kanban", "agile", "monte carlo",
        "pert", "forecast", "прогноз", "estimation", "оценк",
    ]),
    # Scale / international / cross-functional (25+ teams, multi-country, $2M)
    "scale": (2, [
        "distributed", "international", "международ", "cross-functional",
        "кросс-функ", "budget", "бюджет", "stakeholder", "стейкхолдер",
    ]),
}

# Output
CSV_PATH = "pm_shortlist.csv"
TOP_N_TO_PRINT = 25
REQUEST_PAUSE = 0.25  # seconds between API calls, be polite

# HH requires a descriptive User-Agent. Put any contact string here.
HEADERS = {"User-Agent": "pm-job-scanner/1.0 (personal job search)"}

API = "https://api.hh.ru/vacancies"

# ----------------------------------------------------------------------------
# Core
# ----------------------------------------------------------------------------


def fetch_query(text):
    """Fetch all pages for a single search phrase."""
    results = []
    page = 0
    while True:
        params = {
            "text": text,
            "area": AREA,
            "per_page": 100,
            "page": page,
            "order_by": "publication_time",
        }
        if EXPERIENCE:
            params["experience"] = EXPERIENCE
        try:
            r = requests.get(API, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  ! request failed for '{text}' p{page}: {e}", file=sys.stderr)
            break
        data = r.json()
        results.extend(data.get("items", []))
        pages = data.get("pages", 0)
        page += 1
        if page >= pages:
            break
        time.sleep(REQUEST_PAUSE)
    return results


def text_blob(v):
    """All searchable text for a vacancy, lowercased."""
    snippet = v.get("snippet") or {}
    parts = [
        v.get("name", ""),
        (v.get("employer") or {}).get("name", ""),
        snippet.get("requirement") or "",
        snippet.get("responsibility") or "",
    ]
    return " ".join(p for p in parts if p).lower()


def title_ok(v):
    title = (v.get("name") or "").lower()
    if any(t in title for t in EXCLUDE_TITLE_TERMS):
        return False
    if REQUIRE_SENIOR_TITLE and not any(t in title for t in SENIOR_TITLE_TERMS):
        return False
    return True


def fit_score(v):
    """Higher = better fit. Weighted to Yauheni's CV. Returns (score, tags)."""
    blob = text_blob(v)
    title = (v.get("name") or "").lower()
    score = 0
    tags = []

    # CV-weighted domain categories
    for cat, (weight, terms) in SCORE_CATEGORIES.items():
        hits = sum(1 for t in terms if t in blob)
        if hits:
            score += weight * hits
            tags.append(cat)

    # Title seniority — Head/PMO/Delivery map straight to your titles
    score += 3 * sum(1 for t in ("head", "руководитель", "pmo", "lead") if t in title)
    score += 2 * sum(1 for t in ("senior", "старш", "ведущий", "delivery") if t in title)

    # Relocation-friendly + your 14+ yrs
    if (v.get("schedule") or {}).get("id") == "remote":
        score += 2
    if (v.get("experience") or {}).get("id") == "moreThan6":
        score += 2

    return score, tags


def fmt_salary(v):
    s = v.get("salary")
    if not s:
        return ""
    lo, hi, cur = s.get("from"), s.get("to"), s.get("currency") or ""
    if lo and hi:
        rng = f"{lo}–{hi}"
    elif lo:
        rng = f"from {lo}"
    elif hi:
        rng = f"up to {hi}"
    else:
        return ""
    return f"{rng} {cur}".strip()


def collect_ranked():
    """Fetch, dedupe, filter, score. Returns list of vacancies sorted by fit.
    Reusable by other scripts (e.g. the Telegram agent)."""
    by_id = {}
    for q in QUERIES:
        for v in fetch_query(q):
            by_id[v["id"]] = v          # dedupe by vacancy id
        time.sleep(REQUEST_PAUSE)

    kept = [v for v in by_id.values() if title_ok(v)]
    for v in kept:
        v["_score"], v["_tags"] = fit_score(v)
    kept.sort(key=lambda v: (v["_score"], v.get("published_at", "")), reverse=True)
    return kept


def main():
    print(f"Scanning HH/rabota.by for PM roles in Belarus  ({datetime.now():%Y-%m-%d %H:%M})\n")
    kept = collect_ranked()

    print(f"\n{len(kept)} roles after filtering.\n")

    # CSV for review
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["fit", "why", "title", "company", "city", "salary",
                    "schedule", "experience", "published", "url"])
        for v in kept:
            w.writerow([
                v["_score"],
                ", ".join(v["_tags"]),
                v.get("name", ""),
                (v.get("employer") or {}).get("name", ""),
                (v.get("area") or {}).get("name", ""),
                fmt_salary(v),
                (v.get("schedule") or {}).get("name", ""),
                (v.get("experience") or {}).get("name", ""),
                (v.get("published_at") or "")[:10],
                v.get("alternate_url", ""),
            ])
    print(f"Full list written to {CSV_PATH}\n")

    # Console: top picks
    print(f"Top {min(TOP_N_TO_PRINT, len(kept))} by fit:\n" + "-" * 60)
    for v in kept[:TOP_N_TO_PRINT]:
        sal = fmt_salary(v)
        flag = "  ★" + "/".join(v["_tags"]) if v["_tags"] else ""
        print(f"[{v['_score']:>2}] {v.get('name','')}{flag}")
        print(f"     {(v.get('employer') or {}).get('name','')} · "
              f"{(v.get('area') or {}).get('name','')}"
              f"{' · ' + sal if sal else ''}")
        print(f"     {v.get('alternate_url','')}")


if __name__ == "__main__":
    main()
