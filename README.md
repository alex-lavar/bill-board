# The Bill Board 🗞️

**What Congress is actually doing — translated for everyone.**

Plain-language summaries of 119th Congress legislation (Trump's 2nd term, Jan 2025–present), with AI-generated impact analysis for everyday Americans.

Live site: **https://alex-lavar.github.io/bill-board**

---

## How It Works

1. **GitHub Action** runs every night at 2 AM Eastern
2. Fetches the latest bills from the Congress.gov API
3. Sends new bills to Claude for plain-language analysis
4. Writes `bills.json` to the repo
5. Site reads `bills.json` on load — no server needed

---

## One-Time Setup (~10 minutes)

### 1. Create the repo on GitHub
- Go to github.com/new
- Name it `bill-board`
- Set to **Public**
- Don't initialize with README (you're uploading these files)
- Click **Create repository**

### 2. Push this code
```bash
cd bill-board
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/alex-lavar/bill-board.git
git push -u origin main
```
When prompted for password, use your **GitHub Personal Access Token** (not your password).
Get one at: github.com/settings/tokens → Generate new token (classic) → check `repo`

### 3. Add API keys as GitHub Secrets
Go to: github.com/alex-lavar/bill-board → Settings → Secrets and variables → Actions

Add these two secrets:

| Name | Value |
|------|-------|
| `CONGRESS_API_KEY` | Your key from api.congress.gov |
| `ANTHROPIC_API_KEY` | Your key from console.anthropic.com (starts with sk-ant-...) |

### 4. Enable GitHub Pages
- Go to Settings → Pages
- Source: **Deploy from a branch**
- Branch: **main**, folder: **/ (root)**
- Save

Your site will be live at `https://alex-lavar.github.io/bill-board` in ~1 minute.

### 5. Trigger the first data update
- Go to the **Actions** tab in your repo
- Click **"Update Bills & Deploy"**
- Click **"Run workflow"** → **"Run workflow"**

This kicks off the first AI analysis pass. It takes 3–5 minutes.

---

## Cost Estimate

| Service | Cost |
|---------|------|
| Congress.gov API | Free |
| GitHub Actions | Free (2,000 min/month included) |
| Anthropic API | ~$0.50–2.00/day (analyzes ~30 new bills/night) |
| Hosting (GitHub Pages) | Free |

**Total: ~$15–60/month** depending on how many new bills Congress introduces.

Set a spending limit at: console.anthropic.com → Settings → Limits

---

## File Structure

```
bill-board/
├── index.html              ← The site (reads bills.json on load)
├── bills.json              ← Auto-generated data file (commit this seed version)
├── scripts/
│   └── update_bills.py     ← The nightly update script
├── .github/
│   └── workflows/
│       └── update_bills.yml ← GitHub Actions automation
└── README.md
```

---

## Updating Bills Manually

If you want to trigger an update outside the nightly schedule:
1. Go to your repo → **Actions** tab
2. Click **"Update Bills & Deploy"**
3. Click **"Run workflow"**

---

## Adding a Bill Manually

Open `bills.json` directly on GitHub and add a bill object to the `bills` array following the existing format. The AI analysis fields (`plain_title`, `plain_summary`, `impacts`, etc.) are pre-filled by the nightly script — but you can override them by hand if needed.
