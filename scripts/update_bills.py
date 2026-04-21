#!/usr/bin/env python3
"""
update_bills.py
Fetches 119th Congress bills from Congress.gov, generates plain-language
AI analysis via Claude, and writes bills.json for the Bill Board site.
"""

import os, json, time, datetime, requests, sys

CONGRESS_KEY   = os.environ["CONGRESS_API_KEY"]
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
BASE_URL       = "https://api.congress.gov/v3"
OUT_FILE       = "bills.json"
MAX_BILLS      = 250   # how many to fetch from Congress.gov
AI_BATCH       = 30    # how many to send for AI analysis per run (cost control)

# ── CATEGORY DETECTION ────────────────────────────────────────────────────────
def autocat(title):
    t = title.lower()
    if any(w in t for w in ["immigr","border","citizen","visa","asylum","deport",
                             "vote","election","speech","freedom","privacy","gun",
                             "abort","civil right","discriminat","first amendment"]):
        return "rights"
    if any(w in t for w in ["tax","budget","spend","deficit","tariff","trade",
                             "econ","debt","fiscal","approp","revenue","doge"]):
        return "money"
    if any(w in t for w in ["environment","climate","energy","emission","carbon",
                             "land","water","forest","wildlife","fossil","epa"]):
        return "env"
    if any(w in t for w in ["social","medicaid","medicare","school","housing",
                             "food","veteran","child","student","tip","wage",
                             "snap","health","elder","disability","homeless"]):
        return "social"
    return "other"

def map_status(action_text):
    a = (action_text or "").lower()
    if any(w in a for w in ["signed","enacted","became law","public law"]): return "s-signed"
    if "passed house and senate" in a or "agreed to in" in a:              return "s-passed"
    if "passed house" in a or "passed senate" in a:                         return "s-floor"
    if any(w in a for w in ["committee","referred","markup"]):               return "s-committee"
    return "s-introduced"

STATUS_LABELS = {
    "s-introduced": "Introduced",
    "s-committee":  "In Committee",
    "s-floor":      "Passed Chamber",
    "s-passed":     "Passed Congress",
    "s-signed":     "Signed Law",
    "s-vetoed":     "Vetoed",
}

# ── FETCH FROM CONGRESS.GOV ───────────────────────────────────────────────────
def fetch_bills():
    print("Fetching bills from Congress.gov...")
    url = f"{BASE_URL}/bill/119"
    params = {
        "format":  "json",
        "limit":   MAX_BILLS,
        "sort":    "updateDate desc",
        "api_key": CONGRESS_KEY,
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    raw = r.json().get("bills", [])
    print(f"  Got {len(raw)} bills from Congress.gov")
    return raw

# ── AI ANALYSIS ───────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a nonpartisan policy analyst who explains legislation in plain English for everyday Americans. 
You present both supporter and critic perspectives accurately and fairly.
Respond ONLY with a valid JSON object — no preamble, no markdown, no code fences."""

def analyze_bill(bill_id, title, latest_action, sponsor):
    user_prompt = f"""Analyze this U.S. bill and return a JSON object with exactly these fields:

Bill ID: {bill_id}
Title: {title}
Latest Action: {latest_action}
Sponsor: {sponsor}

Return this exact JSON structure:
{{
  "plain_title": "A short, plain-English name for this bill (max 10 words, no jargon)",
  "deck": "One sentence explaining what this bill does in plain language (max 30 words)",
  "plain_summary": "2-3 sentences explaining what this bill does, who introduced it, and its current status. Write for someone with no legal background.",
  "impacts": [
    {{"type": "negative", "text": "One specific negative impact on Americans"}},
    {{"type": "positive", "text": "One specific positive impact on Americans"}},
    {{"type": "neutral",  "text": "One neutral or contested aspect"}}
  ],
  "who_affected": "A comma-separated list of specific groups of Americans most directly affected",
  "impact_score": 3,
  "impact_label": "medium",
  "ticker_alert": "Short alert for the news ticker (max 15 words, start with an action emoji)"
}}

For impact_score use 1-5 (5=affects tens of millions, 1=very narrow). 
For impact_label use: "high" (score 4-5), "medium" (score 3), "low" (score 1-2).
Be specific and accurate. Present both sides fairly."""

    headers = {
        "x-api-key":         ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }
    body = {
        "model":      "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "system":     SYSTEM_PROMPT,
        "messages":   [{"role": "user", "content": user_prompt}],
    }
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers=headers, json=body, timeout=30)
    r.raise_for_status()
    text = r.json()["content"][0]["text"].strip()
    # Strip any accidental markdown fences
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

# ── LOAD EXISTING bills.json (to avoid re-analyzing known bills) ──────────────
def load_existing():
    try:
        with open(OUT_FILE) as f:
            data = json.load(f)
        existing = {b["id"]: b for b in data.get("bills", [])}
        print(f"  Loaded {len(existing)} existing bills from {OUT_FILE}")
        return existing
    except FileNotFoundError:
        print("  No existing bills.json found — starting fresh")
        return {}

# ── BUILD TICKER ──────────────────────────────────────────────────────────────
def build_ticker(bills):
    """Generate ticker items from bills that have AI analysis."""
    items = []
    # Signed laws first
    for b in bills:
        if b.get("status") == "s-signed" and b.get("ticker_alert"):
            items.append(b["ticker_alert"])
    # Then high impact
    for b in bills:
        if b.get("impact_score", 0) >= 4 and b.get("status") != "s-signed" and b.get("ticker_alert"):
            items.append(b["ticker_alert"])
    # Fill with moving bills
    for b in bills:
        if b.get("status") in ("s-floor","s-passed") and b.get("ticker_alert"):
            if b["ticker_alert"] not in items:
                items.append(b["ticker_alert"])
    return items[:20]  # cap at 20

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    existing = load_existing()
    raw_bills = fetch_bills()

    merged = dict(existing)  # start with what we have
    ai_count = 0
    errors = 0

    for raw in raw_bills:
        bill_type   = (raw.get("type") or "").upper()
        bill_number = raw.get("number") or ""
        bill_id     = f"{bill_type} {bill_number}".strip()
        title       = raw.get("title") or "Untitled"
        intro_date  = raw.get("introducedDate") or ""
        action_text = raw.get("latestAction", {}).get("text") or ""
        action_date = raw.get("latestAction", {}).get("actionDate") or ""

        sp = raw.get("sponsors", [{}])[0] if raw.get("sponsors") else {}
        fname = sp.get("firstName", "") or ""
        lname = sp.get("lastName", "") or ""
        party = sp.get("party", "") or ""
        state = sp.get("state", "") or ""
        sponsor = f"{fname} {lname} ({party}-{state})".strip("- ") if sp else "See Congress.gov"

        status_key   = map_status(action_text)
        status_label = STATUS_LABELS.get(status_key, "Active")
        category     = autocat(title)

        # Detect signed date from action text
        signed_date = None
        if status_key == "s-signed" and action_date:
            signed_date = action_date

        # Check if we already have AI analysis for this bill
        if bill_id in merged and merged[bill_id].get("plain_title"):
            # Update status/dates but keep AI analysis
            merged[bill_id]["status"]       = status_key
            merged[bill_id]["status_label"] = status_label
            merged[bill_id]["signed_date"]  = signed_date or merged[bill_id].get("signed_date")
            continue

        # New bill — needs AI analysis (up to AI_BATCH per run)
        if ai_count >= AI_BATCH:
            # Add stub without AI for now — will get analyzed next run
            if bill_id not in merged:
                merged[bill_id] = {
                    "id": bill_id, "title": title,
                    "plain_title": title, "deck": action_text[:80] or title[:80],
                    "plain_summary": f"{title}. Status: {status_label}.",
                    "impacts": [{"type":"neutral","text":"Analysis pending — will be generated on next update."}],
                    "who_affected": "To be determined.",
                    "category": category, "status": status_key, "status_label": status_label,
                    "impact_score": 2, "impact_label": "medium",
                    "intro_date": intro_date, "signed_date": signed_date,
                    "sponsor": sponsor, "ticker_alert": None,
                }
            continue

        # Send to Claude for analysis
        print(f"  Analyzing {bill_id}: {title[:60]}…")
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
            time.sleep(0.5)  # be nice to the API
        except Exception as e:
            print(f"    ✗ AI analysis failed for {bill_id}: {e}", file=sys.stderr)
            errors += 1
            # Add stub
            merged[bill_id] = {
                "id": bill_id, "title": title,
                "plain_title": title, "deck": action_text[:80] or title[:80],
                "plain_summary": f"{title}. Status: {status_label}. Analysis unavailable.",
                "impacts": [{"type":"neutral","text":"Analysis temporarily unavailable."}],
                "who_affected": "See Congress.gov for details.",
                "category": category, "status": status_key, "status_label": status_label,
                "impact_score": 2, "impact_label": "medium",
                "intro_date": intro_date, "signed_date": signed_date,
                "sponsor": sponsor, "ticker_alert": None,
            }

    # Sort final list: signed first, then by impact, then by date
    all_bills = list(merged.values())
    all_bills.sort(key=lambda b: (
        0 if b.get("status") == "s-signed" else 1,
        -(b.get("impact_score") or 0),
        b.get("intro_date") or ""
    ), reverse=False)
    # Fix: secondary sort descending on intro_date
    all_bills.sort(key=lambda b: (
        b.get("status") != "s-signed",
        -(b.get("impact_score") or 0),
        -(int((b.get("intro_date") or "0000-00-00").replace("-","")) )
    ))

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

    print(f"\n✓ Wrote {len(all_bills)} bills to {OUT_FILE}")
    print(f"  AI analyses this run: {ai_count}")
    print(f"  Errors: {errors}")

if __name__ == "__main__":
    main()
