#!/usr/bin/env python3
"""
update_bills.py

How it works:
- Fetches ALL bills from Congress.gov for the 119th Congress via pagination
- Filters to bills that have actually moved (not just introduced with zero activity)
- Loads existing bills.json to check what already has AI analysis
- Only sends bills WITHOUT existing analysis to Claude — never re-analyzes
- Writes updated bills.json back to repo
- Result: first run analyzes everything unanalyzed (~$5-15 one-time cost)
  Subsequent runs only hit new bills (pennies per day)
"""

import os, json, time, datetime, requests, sys

CONGRESS_KEY  = os.environ["CONGRESS_API_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
BASE_URL      = "https://api.congress.gov/v3"
OUT_FILE      = "bills.json"

# Only fetch bills introduced on or after Trump's inauguration (Jan 20, 2025)
# Keeps the dataset focused on his current term only
TRUMP_TERM_START = "2025-01-20"

# Bill types to include — skip pure resolutions (HRES/SRES) and concurrent
# resolutions which are mostly ceremonial
INCLUDE_TYPES = {"HR", "S", "HJRES", "SJRES", "HCONRES", "SCONRES"}

# ── CATEGORY DETECTION ────────────────────────────────────────────────────────
def autocat(title):
    t = title.lower()
    if any(w in t for w in ["immigr","border","citizen","visa","asylum","deport",
                             "vote","election","speech","freedom","privacy","gun",
                             "abort","civil right","discriminat","first amendment",
                             "surveillance","fisa","wiretap"]):
        return "rights"
    if any(w in t for w in ["tax","budget","spend","deficit","tariff","trade",
                             "econ","debt","fiscal","approp","revenue","doge",
                             "continuing resolution","appropriat","finance"]):
        return "money"
    if any(w in t for w in ["environment","climate","energy","emission","carbon",
                             "land","water","forest","wildlife","fossil","epa",
                             "clean","pollution","conservation","mineral","drill"]):
        return "env"
    if any(w in t for w in ["social","medicaid","medicare","school","housing",
                             "food","veteran","child","student","tip","wage",
                             "snap","health","elder","disability","homeless",
                             "medicare","drug","prescription","mental","opioid"]):
        return "social"
    return "other"

def map_status(action_text):
    a = (action_text or "").lower()
    if any(w in a for w in ["signed","enacted","became law","public law"]):
        return "s-signed"
    if "passed house and senate" in a or "agreed to in" in a:
        return "s-passed"
    if "passed house" in a or "passed senate" in a:
        return "s-floor"
    if any(w in a for w in ["committee","referred","markup"]):
        return "s-committee"
    return "s-introduced"

STATUS_LABELS = {
    "s-introduced": "Introduced",
    "s-committee":  "In Committee",
    "s-floor":      "Passed Chamber",
    "s-passed":     "Passed Congress",
    "s-signed":     "Signed Law",
    "s-vetoed":     "Vetoed",
}

# ── FETCH ALL BILLS (PAGINATED) ───────────────────────────────────────────────
def fetch_all_bills():
    """
    Paginate through Congress.gov to get all 119th Congress bills.
    Congress.gov max per request is 250, so we loop with offset.
    """
    all_bills = []
    offset = 0
    limit = 250

    print(f"Fetching bills from Congress.gov (119th Congress, introduced on/after {TRUMP_TERM_START})...")

    while True:
        params = {
            "format":        "json",
            "limit":         limit,
            "offset":        offset,
            "sort":          "updateDate desc",
            "fromDateTime":  f"{TRUMP_TERM_START}T00:00:00Z",
            "api_key":       CONGRESS_KEY,
        }

        try:
            r = requests.get(f"{BASE_URL}/bill/119", params=params, timeout=30)
            r.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"  ✗ Congress.gov request failed at offset {offset}: {e}", file=sys.stderr)
            break

        data = r.json()
        batch = data.get("bills", [])
        if not batch:
            break

        # Filter to meaningful bill types
        filtered = [b for b in batch if (b.get("type") or "").upper() in INCLUDE_TYPES]
        all_bills.extend(filtered)

        total_available = data.get("pagination", {}).get("count", 0)
        offset += limit

        print(f"  Fetched {len(all_bills)} bills so far (total available: {total_available})...")

        # Stop if we've fetched everything
        if offset >= total_available or len(batch) < limit:
            break

        # Polite delay between pages
        time.sleep(0.3)

    print(f"  Done — {len(all_bills)} bills fetched from Congress.gov")
    return all_bills

# ── AI ANALYSIS ───────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a nonpartisan policy analyst who explains legislation in plain English for everyday Americans.
You present both supporter and critic perspectives accurately and fairly.
Respond ONLY with a valid JSON object — no preamble, no markdown, no code fences."""

def analyze_bill(bill_id, title, latest_action, sponsor):
    prompt = f"""Analyze this U.S. bill and return a JSON object with exactly these fields:

Bill ID: {bill_id}
Title: {title}
Latest Action: {latest_action}
Sponsor: {sponsor}

Return this exact JSON structure:
{{
  "plain_title": "A short plain-English name for this bill (max 10 words, no jargon)",
  "deck": "One sentence explaining what this bill does in plain language (max 30 words)",
  "plain_summary": "2-3 sentences explaining what this bill does and its current status. Write for someone with no legal background.",
  "impacts": [
    {{"type": "negative", "text": "One specific negative impact on Americans"}},
    {{"type": "positive", "text": "One specific positive impact on Americans"}},
    {{"type": "neutral",  "text": "One neutral or contested aspect"}}
  ],
  "who_affected": "A comma-separated list of specific groups of Americans most directly affected",
  "impact_score": 3,
  "impact_label": "medium",
  "ticker_alert": "Short alert for news ticker (max 15 words, start with an action emoji)"
}}

For impact_score use 1-5 (5=affects tens of millions of people, 1=very narrow impact).
For impact_label: "high" (score 4-5), "medium" (score 3), "low" (score 1-2).
Be specific and accurate. Present both sides fairly."""

    headers = {
        "x-api-key":         ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    body = {
        "model":      "claude-sonnet-4-6",
        "max_tokens": 1000,
        "system":     SYSTEM_PROMPT,
        "messages":   [{"role": "user", "content": prompt}],
    }
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers, json=body, timeout=45
    )
    r.raise_for_status()
    text = r.json()["content"][0]["text"].strip()
    # Strip accidental markdown fences
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

def has_real_analysis(bill):
    """Returns True if this bill already has real AI-generated analysis."""
    summary = bill.get("plain_summary", "")
    if not summary:
        return False
    stubs = [
        "Analysis pending",
        "Analysis unavailable",
        "Analysis temporarily",
        "will be generated",
        "See Congress.gov",
        "To be determined",
        "Live bill from Congress",
    ]
    return not any(s.lower() in summary.lower() for s in stubs)

# ── LOAD EXISTING ─────────────────────────────────────────────────────────────
def load_existing():
    try:
        with open(OUT_FILE) as f:
            data = json.load(f)
        existing = {b["id"]: b for b in data.get("bills", [])}
        print(f"  Loaded {len(existing)} existing bills from {OUT_FILE}")
        return existing
    except FileNotFoundError:
        print("  No existing bills.json — starting fresh")
        return {}

# ── BUILD TICKER ──────────────────────────────────────────────────────────────
def build_ticker(bills):
    items = []
    for b in bills:
        if b.get("status") == "s-signed" and b.get("ticker_alert") and len(items) < 8:
            items.append(b["ticker_alert"])
    for b in bills:
        if b.get("impact_score", 0) >= 4 and b.get("status") != "s-signed" and b.get("ticker_alert"):
            if b["ticker_alert"] not in items and len(items) < 16:
                items.append(b["ticker_alert"])
    for b in bills:
        if b.get("status") in ("s-floor", "s-passed") and b.get("ticker_alert"):
            if b["ticker_alert"] not in items and len(items) < 20:
                items.append(b["ticker_alert"])
    return items

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    existing = load_existing()
    raw_bills = fetch_all_bills()

    merged = dict(existing)
    ai_count = 0
    skip_count = 0
    error_count = 0

    for raw in raw_bills:
        bill_type   = (raw.get("type") or "").upper()
        bill_number = raw.get("number") or ""
        bill_id     = f"{bill_type} {bill_number}".strip()
        title       = raw.get("title") or "Untitled"
        intro_date  = raw.get("introducedDate") or ""
        if raw is None:
            continue  # or skip/log this bill
        action_text = raw.get("latestAction", {}).get("text") or ""
        action_date = raw.get("latestAction", {}).get("actionDate") or ""

        sp = (raw.get("sponsors") or [{}])[0] if raw.get("sponsors") else {}
        fname = sp.get("firstName", "") or ""
        lname = sp.get("lastName", "") or ""
        party = sp.get("party", "") or ""
        state = sp.get("state", "") or ""
        sponsor = f"{fname} {lname} ({party}-{state})".strip("- ") if sp else "See Congress.gov"

        status_key   = map_status(action_text)
        status_label = STATUS_LABELS.get(status_key, "Active")
        category     = autocat(title)

        signed_date = None
        if status_key == "s-signed" and action_date:
            signed_date = action_date

        # Skip bills that haven't passed committee — they're noise
        # Only care about bills with real traction: floor vote, passed, signed, vetoed
        if status_key in ("s-introduced", "s-committee"):
            if bill_id not in merged:
                pass  # don't add them at all
            else:
                # Update status on existing bills in case they moved backwards (rare)
                merged[bill_id]["status"] = status_key
                merged[bill_id]["status_label"] = status_label
            skip_count += 1
            continue

        # If we already have real analysis for this bill, just update status/dates
        if bill_id in merged and has_real_analysis(merged[bill_id]):
            merged[bill_id]["status"]       = status_key
            merged[bill_id]["status_label"] = status_label
            if signed_date:
                merged[bill_id]["signed_date"] = signed_date
            skip_count += 1
            continue

        # New bill or stub — send to Claude for analysis
        print(f"  Analyzing {bill_id}: {title[:65]}…")
        try:
            ai = analyze_bill(bill_id, title, action_text, sponsor)
            merged[bill_id] = {
                "id":           bill_id,
                "title":        title,
                "plain_title":  ai.get("plain_title", title),
                "deck":         ai.get("deck", ""),
                "plain_summary":ai.get("plain_summary", ""),
                "impacts":      ai.get("impacts", []),
                "who_affected": ai.get("who_affected", ""),
                "category":     category,
                "status":       status_key,
                "status_label": status_label,
                "impact_score": int(ai.get("impact_score", 2)),
                "impact_label": ai.get("impact_label", "medium"),
                "intro_date":   intro_date,
                "signed_date":  signed_date,
                "sponsor":      sponsor,
                "ticker_alert": ai.get("ticker_alert"),
            }
            ai_count += 1
            # Brief pause to be nice to the API
            time.sleep(0.4)

        except Exception as e:
            print(f"    ✗ Failed {bill_id}: {e}", file=sys.stderr)
            error_count += 1
            # Store stub so we don't skip it forever
            if bill_id not in merged:
                merged[bill_id] = {
                    "id": bill_id, "title": title,
                    "plain_title": title,
                    "deck": (action_text or title)[:80],
                    "plain_summary": "Analysis pending — will be generated on next update.",
                    "impacts": [{"type": "neutral", "text": "Analysis pending."}],
                    "who_affected": "To be determined.",
                    "category": category,
                    "status": status_key, "status_label": status_label,
                    "impact_score": 2, "impact_label": "medium",
                    "intro_date": intro_date, "signed_date": signed_date,
                    "sponsor": sponsor, "ticker_alert": None,
                }

    # Sort: signed laws first (by signed date), then by latest activity
    all_bills = list(merged.values())

    def sort_key(b):
        # Signed bills float to top, sorted by signed date desc
        if b.get("signed_date"):
            return (0, b["signed_date"])
        # Everything else sorted by intro date desc
        return (1, b.get("intro_date") or "")

    all_bills.sort(key=sort_key, reverse=True)

    ticker = build_ticker(all_bills)

    output = {
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "congress":   119,
        "total":      len(all_bills),
        "ticker":     ticker,
        "bills":      all_bills,
    }

    with open(OUT_FILE, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n✓ Done")
    print(f"  Total bills in file: {len(all_bills)}")
    print(f"  Already had analysis (skipped): {skip_count}")
    print(f"  New AI analyses this run: {ai_count}")
    print(f"  Errors: {error_count}")

if __name__ == "__main__":
    main()
