"""
Southeast Supply Enhancement (SSEP) Pipeline Monitor -- v3
------------------------------------------------------------
Tracks anything that could influence WHEN the Williams/Transco Southeast
Supply Enhancement Project gets built (FERC Docket No. CP25-10-000,
rehearing docket CP25-10-001, target in-service Q4 2027) and emails a
digest whenever something new appears.

SOURCES (see README.md for full setup instructions for each)
  Tier 0 - Official record
    - Federal Register API (EPA/USACE/DOT/PHMSA notices)          [no key]
    - FERC eSubscription                        -- external, see README
  Tier 1 - Litigation
    - CourtListener / RECAP search API (D.C. Cir., 4th Cir.,
      VA & NC district courts)                  [free account token]
  Tier 2 - Legislation (polled every ~6 hours, not every run --
           legislative action doesn't move minute-to-minute)
    - LegiScan API: Virginia, North Carolina (route states), South
      Carolina, Georgia, Alabama (compressor-station states), and
      US Congress -- eminent domain, pipeline siting, water-quality
      certification, gas-infrastructure moratoria, permitting reform
                                                  [free account key]
  Tier 3 - Press & advocacy, polled frequently
    - Google News RSS + Bing News RSS (national + trade press + the
      8 affected counties)
    - FERC.gov newsroom search
    - Appalachian Voices / NoSSEP coalition tracker page
    - Bluesky keyword search                     [optional, free login]

Run on a schedule (cron / GitHub Actions). Each run pulls fresh items,
diffs against state.json, emails only what's new, and updates state.json.
"""

import os
import sys
import json
import hashlib
import smtplib
import logging
import argparse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
LOG_FILE = os.path.join(BASE_DIR, "monitor.log")

FERC_DOCKET = "CP25-10-000"
FERC_REHEARING_DOCKET = "CP25-10-001"

# --- Email (required) --------------------------------------------------
SMTP_HOST = os.environ.get("SSEP_SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = int(os.environ.get("SSEP_SMTP_PORT") or "587")
SMTP_USER = os.environ.get("SSEP_SMTP_USER")
SMTP_PASS = os.environ.get("SSEP_SMTP_PASS")
EMAIL_TO = os.environ.get("SSEP_EMAIL_TO") or SMTP_USER

# --- CourtListener (optional but recommended -- free account) ----------
COURTLISTENER_TOKEN = os.environ.get("SSEP_COURTLISTENER_TOKEN")
COURTLISTENER_QUERIES = [
    "Southeast Supply Enhancement",
    "Transcontinental Gas Pipe Line",
    '"CP25-10"',
]
COURTLISTENER_COURTS = "cadc,ca4,vaed,vawd,ncmd,ncwd"

# --- LegiScan (optional but recommended -- free account) ---------------
# Sign up free at https://legiscan.com, register for an API key at
# https://legiscan.com/legiscan
LEGISCAN_API_KEY = os.environ.get("SSEP_LEGISCAN_API_KEY")
# VA & NC = route states. SC, GA, AL = compressor-station states.
# US = US Congress, in LegiScan's own jurisdiction coding.
LEGISCAN_STATES = ["VA", "NC", "SC", "GA", "AL", "US"]
LEGISCAN_QUERIES = [
    "pipeline eminent domain",
    "natural gas pipeline",
    "water quality certification pipeline",
    "gas infrastructure moratorium",
    "pipeline permitting",
]
# Legislative action doesn't move minute-to-minute -- only actually poll
# LegiScan if this many hours have passed since the last check, so a
# 15-minute cron for the fast sources doesn't blow through the free
# 30,000 query/month quota.
LEGISCAN_MIN_HOURS_BETWEEN_POLLS = 6

# --- Bluesky (optional -- free account+app password for reliable search)
BLUESKY_HANDLE = os.environ.get("SSEP_BLUESKY_HANDLE")
BLUESKY_APP_PASSWORD = os.environ.get("SSEP_BLUESKY_APP_PASSWORD")
BLUESKY_QUERIES = [
    "Southeast Supply Enhancement",
    "NoSSEP",
    "Transco pipeline lawsuit",
]

# --- News queries --------------------------------------------------------
NEWS_QUERIES = [
    "Southeast Supply Enhancement lawsuit",
    "Southeast Supply Enhancement pipeline",
    '"CP25-10" FERC Transco',
    "Transco Southeast Supply Enhancement FERC order",
    "NoSSEP Southeast Supply Enhancement opposition",
    "PHMSA Transco Williams pipeline",
    "Williams Transco pipeline Virginia North Carolina lawsuit",
    "Southeast Supply Enhancement rehearing appeal",
    "Transco pipeline Williams Companies natural gas expansion",
    "Pittsylvania County pipeline Transco",
    "Rockingham County North Carolina pipeline Transco",
    "Guilford County pipeline Transco",
    "Forsyth County pipeline Transco",
    "Davidson County North Carolina pipeline Transco",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (SSEP-Monitor/3.0; personal research tool)"}
REQUEST_TIMEOUT = 20

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.INFO)
logging.getLogger().addHandler(_console)
log = logging.getLogger("ssep-monitor")


# ----------------------------------------------------------------------------
# STATE
# ----------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"seen_ids": [], "legiscan_last_run": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def item_id(url, title):
    return hashlib.sha256(f"{url}|{title}".encode("utf-8")).hexdigest()


def legiscan_due(state):
    last_run = state.get("legiscan_last_run")
    if not last_run:
        return True
    last_dt = datetime.fromisoformat(last_run)
    return datetime.now(timezone.utc) - last_dt >= timedelta(hours=LEGISCAN_MIN_HOURS_BETWEEN_POLLS)


# ----------------------------------------------------------------------------
# SOURCE: Google News RSS
# ----------------------------------------------------------------------------

def fetch_google_news(query):
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    items = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for entry in root.findall(".//item"):
            title = (entry.findtext("title") or "").strip()
            link = (entry.findtext("link") or "").strip()
            pub_date = (entry.findtext("pubDate") or "").strip()
            source_el = entry.find("source")
            source = source_el.text if source_el is not None else "Google News"
            if title and link:
                items.append({
                    "title": title, "url": link, "date": pub_date,
                    "source": source, "category": f"News: {query}",
                })
    except Exception as e:
        log.warning(f"Google News fetch failed for '{query}': {e}")
    return items


# ----------------------------------------------------------------------------
# SOURCE: Bing News RSS
# ----------------------------------------------------------------------------

def fetch_bing_news(query):
    url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=RSS"
    items = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for entry in root.findall(".//item"):
            title = (entry.findtext("title") or "").strip()
            link = (entry.findtext("link") or "").strip()
            pub_date = (entry.findtext("pubDate") or "").strip()
            if title and link:
                items.append({
                    "title": title, "url": link, "date": pub_date,
                    "source": "Bing News", "category": f"News: {query}",
                })
    except Exception as e:
        log.warning(f"Bing News fetch failed for '{query}': {e}")
    return items


# ----------------------------------------------------------------------------
# SOURCE: FERC.gov newsroom search
# ----------------------------------------------------------------------------

def fetch_ferc_news():
    items = []
    url = "https://www.ferc.gov/search?query=Southeast+Supply+Enhancement"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.select("a[href]"):
            href = link["href"]
            text = link.get_text(strip=True)
            if not text or len(text) < 8:
                continue
            if "southeast supply" in text.lower() or "cp25-10" in text.lower():
                full_url = href if href.startswith("http") else f"https://www.ferc.gov{href}"
                items.append({
                    "title": text, "url": full_url, "date": "",
                    "source": "FERC.gov", "category": "FERC",
                })
    except Exception as e:
        log.warning(f"FERC.gov search failed: {e}")
    return items


# ----------------------------------------------------------------------------
# SOURCE: Federal Register API (no key needed)
# ----------------------------------------------------------------------------

def fetch_federal_register():
    items = []
    url = "https://www.federalregister.gov/api/v1/documents.json"
    params = {
        "conditions[term]": "Southeast Supply Enhancement",
        "per_page": 20,
        "order": "newest",
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        for doc in data.get("results", []):
            agencies = doc.get("agencies", [])
            agency_name = agencies[0].get("name", "Federal Register") if agencies else "Federal Register"
            items.append({
                "title": doc.get("title", ""),
                "url": doc.get("html_url", ""),
                "date": doc.get("publication_date", ""),
                "source": f"Federal Register ({agency_name})",
                "category": "Federal Register",
            })
    except Exception as e:
        log.warning(f"Federal Register fetch failed: {e}")
    return items


# ----------------------------------------------------------------------------
# SOURCE: CourtListener / RECAP search API
# ----------------------------------------------------------------------------

def fetch_courtlistener():
    items = []
    if not COURTLISTENER_TOKEN:
        log.info("SSEP_COURTLISTENER_TOKEN not set -- skipping CourtListener (see README).")
        return items

    headers = dict(HEADERS)
    headers["Authorization"] = f"Token {COURTLISTENER_TOKEN}"
    url = "https://www.courtlistener.com/api/rest/v4/search/"

    for query in COURTLISTENER_QUERIES:
        params = {"q": query, "type": "r", "order_by": "dateFiled desc"}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            for docket in data.get("results", []):
                case_name = docket.get("caseName", "Unknown case")
                docket_url = "https://www.courtlistener.com" + docket.get("absolute_url", "")
                date_filed = docket.get("dateFiled", "")
                court = docket.get("court", "")
                items.append({
                    "title": f"{case_name} ({court})",
                    "url": docket_url, "date": date_filed,
                    "source": "CourtListener/RECAP", "category": f"Litigation: {query}",
                })
        except Exception as e:
            log.warning(f"CourtListener fetch failed for '{query}': {e}")
    return items


# ----------------------------------------------------------------------------
# SOURCE: LegiScan -- state & federal legislation
# ----------------------------------------------------------------------------

def fetch_legiscan():
    items = []
    if not LEGISCAN_API_KEY:
        log.info("SSEP_LEGISCAN_API_KEY not set -- skipping legislation tracking (see README).")
        return items

    url = "https://api.legiscan.com/"
    for state in LEGISCAN_STATES:
        for query in LEGISCAN_QUERIES:
            params = {
                "key": LEGISCAN_API_KEY,
                "op": "getSearch",
                "state": state,
                "query": query,
            }
            try:
                resp = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") != "OK":
                    continue
                results = data.get("searchresult", {})
                for key, bill in results.items():
                    if key == "summary" or not isinstance(bill, dict):
                        continue
                    bill_number = bill.get("bill_number", "")
                    title = bill.get("title") or bill.get("text", "")
                    bill_url = bill.get("url", "")
                    change_hash = bill.get("change_hash", "")
                    last_action = bill.get("last_action", "")
                    last_action_date = bill.get("last_action_date", "")
                    # Include change_hash in the identity so a status change
                    # (new committee vote, amendment, etc.) re-alerts even
                    # though the bill URL is the same as before.
                    items.append({
                        "title": f"[{state}] {bill_number}: {title} -- {last_action}",
                        "url": f"{bill_url}#{change_hash}",
                        "date": last_action_date,
                        "source": f"LegiScan ({state})",
                        "category": f"Legislation: {query}",
                    })
            except Exception as e:
                log.warning(f"LegiScan fetch failed for state={state} query='{query}': {e}")
    return items


# ----------------------------------------------------------------------------
# SOURCE: Appalachian Voices / NoSSEP coalition tracker page
# ----------------------------------------------------------------------------

def fetch_appalachian_voices():
    items = []
    url = "https://appvoices.org/pipelines/ssep/"
    keywords = ["lawsuit", "challenge", "court", "petition", "appeal", "ferc",
                "phmsa", "rehearing", "sue", "litigation"]
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.select("a[href]"):
            text = link.get_text(strip=True)
            href = link["href"]
            if not text or len(text) < 10:
                continue
            if any(k in text.lower() for k in keywords):
                full_url = href if href.startswith("http") else f"https://appvoices.org{href}"
                items.append({
                    "title": text, "url": full_url, "date": "",
                    "source": "Appalachian Voices (NoSSEP)", "category": "Opposition/Legal",
                })
    except Exception as e:
        log.warning(f"Appalachian Voices fetch failed: {e}")
    return items


# ----------------------------------------------------------------------------
# SOURCE: Bluesky keyword search (optional)
# ----------------------------------------------------------------------------

def _bluesky_session():
    try:
        resp = requests.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": BLUESKY_HANDLE, "password": BLUESKY_APP_PASSWORD},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("accessJwt")
    except Exception as e:
        log.warning(f"Bluesky login failed: {e}")
        return None


def fetch_bluesky():
    items = []
    if not (BLUESKY_HANDLE and BLUESKY_APP_PASSWORD):
        log.info("Bluesky credentials not set -- skipping (optional, see README).")
        return items

    token = _bluesky_session()
    if not token:
        return items

    headers = {"Authorization": f"Bearer {token}"}
    for query in BLUESKY_QUERIES:
        try:
            resp = requests.get(
                "https://bsky.social/xrpc/app.bsky.feed.searchPosts",
                params={"q": query, "limit": 15, "sort": "latest"},
                headers=headers, timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            for post in data.get("posts", []):
                author = post.get("author", {}).get("handle", "unknown")
                uri = post.get("uri", "")
                post_id = uri.split("/")[-1] if uri else ""
                web_url = f"https://bsky.app/profile/{author}/post/{post_id}" if post_id else uri
                text = post.get("record", {}).get("text", "")[:200]
                created_at = post.get("record", {}).get("createdAt", "")
                items.append({
                    "title": f"@{author}: {text}",
                    "url": web_url, "date": created_at,
                    "source": "Bluesky", "category": f"Social: {query}",
                })
        except Exception as e:
            log.warning(f"Bluesky search failed for '{query}': {e}")
    return items


# ----------------------------------------------------------------------------
# EMAIL
# ----------------------------------------------------------------------------

def send_email(new_items):
    if not SMTP_USER or not SMTP_PASS:
        log.error("SSEP_SMTP_USER / SSEP_SMTP_PASS not set -- printing instead of emailing.")
        for item in new_items:
            print(f"- [{item['category']}] {item['title']} ({item['url']})")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"SSEP Pipeline Monitor: {len(new_items)} new update(s)"
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_TO

    text_lines = [f"New updates affecting the Williams/Transco Southeast Supply "
                  f"Enhancement Project (FERC Docket {FERC_DOCKET}):\n"]
    html_rows = []
    for item in new_items:
        text_lines.append(
            f"[{item['category']}] {item['title']}\n  {item['url']}\n"
            f"  {item['date']} - {item['source']}\n"
        )
        html_rows.append(
            f"<li><b>[{item['category']}]</b> "
            f"<a href='{item['url']}'>{item['title']}</a>"
            f"<br><small>{item['date']} &middot; {item['source']}</small></li>"
        )

    text_body = "\n".join(text_lines)
    html_body = (
        f"<p>New updates on the SSEP Project (FERC Docket {FERC_DOCKET}):</p>"
        f"<ul>{''.join(html_rows)}</ul>"
    )

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [EMAIL_TO], msg.as_string())

    log.info(f"Emailed {len(new_items)} new item(s) to {EMAIL_TO}")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def run_once():
    log.info("Starting SSEP monitor run")
    state = load_state()
    seen = set(state.get("seen_ids", []))

    all_items = []
    for q in NEWS_QUERIES:
        all_items.extend(fetch_google_news(q))
        all_items.extend(fetch_bing_news(q))
    all_items.extend(fetch_ferc_news())
    all_items.extend(fetch_federal_register())
    all_items.extend(fetch_courtlistener())
    all_items.extend(fetch_appalachian_voices())
    all_items.extend(fetch_bluesky())

    ran_legiscan = False
    if legiscan_due(state):
        log.info("LegiScan poll is due -- checking legislation")
        all_items.extend(fetch_legiscan())
        ran_legiscan = True
    else:
        log.info("LegiScan poll not due yet -- skipping this run to conserve free quota")

    new_items = []
    for item in all_items:
        if not item.get("url"):
            continue
        iid = item_id(item["url"], item["title"])
        if iid not in seen:
            seen.add(iid)
            new_items.append(item)

    log.info(f"Checked {len(all_items)} items total across all sources, {len(new_items)} are new")

    if new_items:
        send_email(new_items)
    else:
        log.info("No new items this run")

    state["seen_ids"] = list(seen)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    if ran_legiscan:
        state["legiscan_last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SSEP Pipeline Monitor")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit (default behavior)")
    parser.parse_args()
    run_once()
