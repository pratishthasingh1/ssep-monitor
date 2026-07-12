# SSEP Pipeline Monitor

Tracks anything that could influence **when** the Williams/Transco
**Southeast Supply Enhancement Project** actually gets built
(FERC Docket No. **CP25-10-000**, rehearing docket **CP25-10-001**,
target in-service Q4 2027) — regulatory filings, litigation, legislation,
press, and opposition activity — and emails you a digest whenever
something new appears. Hosted for free on GitHub Actions, so nothing
runs on your own machine.

## What this tracks and how fast

| Tier | Source | Speed | Cost |
|---|---|---|---|
| 0 — Official record | FERC eSubscription (set up separately, see Step 1) | Same day | Free |
| 0 — Official record | Federal Register API (EPA/USACE/DOT/PHMSA notices) | Daily | Free, no key |
| 1 — Litigation | CourtListener/RECAP (D.C. Cir., 4th Cir., VA & NC courts) | ~15 min (this script's poll interval) | Free (needs account token) |
| 2 — Legislation | LegiScan: VA, NC (route states), SC, GA, AL (compressor-station states), US Congress | Every ~6 hours | Free (needs account key) |
| 3 — Press & advocacy | Google News + Bing News (national, trade press, 8 affected counties), FERC.gov newsroom, Appalachian Voices/NoSSEP, Bluesky | ~15 min | Free |

## Step 1 — Get FERC's own real-time feed (do this regardless of everything else)

FERC's eLibrary is a JavaScript app that a scraper can't reliably read, so
for the docket itself, use FERC's own free notification service:

1. Go to https://www.ferc.gov/ferc-online/esubscription
2. Create a free account
3. Subscribe to **CP25-10-000** and **CP25-10-001** (the rehearing docket,
   already active as of June 2026)

This is the authoritative, same-day source for every actual filing.
Everything below fills in what FERC's own feed won't tell you.

## Step 2 — Put this in a GitHub repo

1. Create a free GitHub account if you don't have one: https://github.com/signup
2. Create a new repository (private is fine — recommended, since your
   monitor state will live there)
3. Upload all the files in this folder, preserving the folder structure:
   ```
   your-repo/
     pipeline_monitor.py
     requirements.txt
     README.md
     .github/workflows/monitor.yml
   ```
   Easiest way: `git clone` your new empty repo locally, copy these files
   in, then `git add . && git commit -m "Initial setup" && git push`.

## Step 3 — Get the free API keys

You only strictly need email to get *something* running, but each of
these unlocks a real source:

**CourtListener (litigation)**
1. Free account: https://www.courtlistener.com/register/
2. Get a token: https://www.courtlistener.com/profile/api-token/
3. (Optional, $10/year) Become a Free Law Project member to unlock their
   "Real Time" alert tier if you ever want to use their native alerts
   instead of/alongside this script's own polling.

**LegiScan (legislation)**
1. Free account + API key: https://legiscan.com/legiscan (click "Signup
   Now" and then generate your key)
2. Free tier: 30,000 queries/month. This script uses roughly
   6 jurisdictions x 5 keywords = 30 queries, once every 6 hours =
   ~3,600 queries/month — comfortably under the limit with room to add
   more keywords if you want.

**Bluesky (optional — social monitoring)**
1. Create a free account at https://bsky.app if you don't have one
2. Create an *app password* (not your login password) at
   https://bsky.app/settings/app-passwords

**Gmail (for sending the actual alert emails)**
1. Enable 2-Step Verification on your Google account
2. Create an app password at https://myaccount.google.com/apppasswords
3. Use that as your SMTP password below — your normal Gmail password
   will not work for this.

## Step 4 — Add everything as GitHub repo secrets

In your repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add each of these (skip any source you don't want):

| Secret name | Value |
|---|---|
| `SSEP_SMTP_USER` | your email address |
| `SSEP_SMTP_PASS` | your Gmail app password |
| `SSEP_EMAIL_TO` | where you want alerts sent (can be same as SMTP_USER) |
| `SSEP_SMTP_HOST` | `smtp.gmail.com` (or your provider's SMTP host, only needed if not Gmail) |
| `SSEP_SMTP_PORT` | `587` (only needed if not Gmail) |
| `SSEP_COURTLISTENER_TOKEN` | your CourtListener API token |
| `SSEP_LEGISCAN_API_KEY` | your LegiScan API key |
| `SSEP_BLUESKY_HANDLE` | your Bluesky handle, e.g. `you.bsky.social` |
| `SSEP_BLUESKY_APP_PASSWORD` | your Bluesky app password |

Secrets are encrypted, never shown in logs, and never visible in the code.

## Step 5 — Turn it on

The workflow in `.github/workflows/monitor.yml` is already set to run
every 15 minutes automatically once it's on GitHub — nothing else to do.
To confirm it's working: go to the **Actions** tab in your repo, click
**SSEP Pipeline Monitor**, and click **Run workflow** to trigger a manual
test run. Check the logs; your first run will treat everything currently
out there as "new" and send one large baseline digest.

## How the pieces fit together

- Every 15 minutes: news, FERC.gov, Federal Register, CourtListener,
  Appalachian Voices, and Bluesky all get checked.
- Every ~6 hours: LegiScan gets checked too (gated inside the script
  itself via a timestamp in `state.json`, so it works fine even though
  the workflow runs every 15 minutes).
- `state.json` is committed back to your repo after every run, so
  nothing repeats and nothing is lost between runs.

## Notes

- **PHMSA** doesn't maintain a per-project docket the way FERC does —
  pipeline safety oversight happens through regional field offices and
  doesn't generate a real-time public feed. The "PHMSA Transco Williams
  pipeline" news query is the practical way to catch PHMSA-related
  coverage; a specific enforcement action, if one is ever opened, would
  eventually show up at
  https://www.phmsa.dot.gov/pipeline/enforcement-actions.
- **Legislation keywords** are intentionally broad ("pipeline eminent
  domain", "water quality certification pipeline", etc.) rather than
  naming the project, since most bills that could actually affect this
  project's timeline — state 401 water-quality certification rules,
  eminent domain reform, gas-infrastructure moratoria — won't mention
  "Southeast Supply Enhancement" by name. Tune `LEGISCAN_QUERIES` in
  `pipeline_monitor.py` if you want to narrow or broaden this.
- **If you ever want the professional-grade tier** (Law360, Bloomberg
  Law) — these are what law firms and institutional players pay for on
  exactly this kind of tracking. Real pricing: Law360 runs
  $5,940–$11,044/year per user (median $6,075, per actual buyer data);
  Bloomberg Law is quote-only but commonly estimated in a similar range.
  For monitoring one specific project, what they add over this free
  setup is curation and polish across thousands of dockets — not
  materially faster access to filings on *this* project, since FERC
  eSubscription and CourtListener already get you those same-day/~15-min.
- Delete `state.json` any time you want to reset and get a fresh
  "everything" baseline digest.
