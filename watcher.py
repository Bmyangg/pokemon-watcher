"""
Pokemon TCG restock watcher.

What it does: opens the Pokemon card pages at Target, Walmart, Pokemon Center
and Best Buy, looks for items that show an "Add to cart" button and no
"sold out" wording, and sends a phone notification through ntfy.

It remembers what it already told you about (state.json), so you only get a
ping when something NEW comes into stock.

Nothing here buys anything. It only looks and alerts you.
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# THE PAGES TO WATCH. You can edit these web addresses any time.
# "link_pattern" tells the script what a product link looks like on that site.
# ---------------------------------------------------------------------------
STORES = [
    {
        "name": "Target",
        "url": "https://www.target.com/s?searchTerm=pokemon+trading+cards&sortBy=newest",
        "base": "https://www.target.com",
        "link_pattern": r'/p/[^"\'\s<>\\]+',
    },
    {
        "name": "Walmart",
        "url": "https://www.walmart.com/search?q=pokemon+trading+cards&sort=best_seller",
        "base": "https://www.walmart.com",
        "link_pattern": r'/ip/[^"\'\s<>\\]+',
    },
    {
        "name": "Pokemon Center",
        "url": "https://www.pokemoncenter.com/category/trading-card-game",
        "base": "https://www.pokemoncenter.com",
        "link_pattern": r'/product/[^"\'\s<>\\]+',
    },
    {
        "name": "Best Buy",
        "url": "https://www.bestbuy.com/site/searchpage.jsp?st=pokemon+trading+cards",
        "base": "https://www.bestbuy.com",
        "link_pattern": r'/site/[^"\'\s<>\\]+\.p\?skuId=\d+',
    },
]

IN_STOCK_WORDS = ["add to cart", "add to bag", "add to basket"]
SOLD_OUT_WORDS = [
    "sold out", "out of stock", "unavailable", "coming soon",
    "notify me", "get stock alert", "temporarily out",
]
BLOCKED_WORDS = [
    "captcha", "access denied", "are you a robot", "unusual traffic",
    "verify you are human", "px-captcha", "request blocked",
]

STATE_FILE = "state.json"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"in_stock": {}, "last_status": {}, "first_run_done": False}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def notify(title, message, link=None, priority="default"):
    """Send a phone notification through ntfy."""
    if not NTFY_TOPIC:
        print("No NTFY_TOPIC set. Would have sent:", title, message)
        return
    headers = {"Title": title.encode("ascii", "ignore").decode(), "Priority": priority}
    if link:
        headers["Click"] = link
    req = urllib.request.Request(
        "https://ntfy.sh/" + NTFY_TOPIC,
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print("Could not send notification:", e)


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def find_in_stock(store, html):
    """Return {link: short_label} for items that look buyable right now."""
    found = {}
    for m in re.finditer(store["link_pattern"], html):
        link = m.group(0).replace("&amp;", "&")
        start = max(0, m.start() - 700)
        end = min(len(html), m.end() + 900)
        snippet = html[start:end].lower()
        if any(w in snippet for w in SOLD_OUT_WORDS):
            continue
        if not any(w in snippet for w in IN_STOCK_WORDS):
            continue
        full = link if link.startswith("http") else store["base"] + link
        label = link.split("/")[2] if link.count("/") >= 2 else link
        label = re.sub(r"[-_]", " ", label)[:60]
        found[full] = label
    return found


def main():
    state = load_state()
    first_run = not state.get("first_run_done", False)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary = []

    for store in STORES:
        name = store["name"]
        try:
            html = fetch(store["url"])
        except urllib.error.HTTPError as e:
            status = "blocked (error %s)" % e.code
            print(name, status)
            summary.append("%s: %s" % (name, status))
            if state["last_status"].get(name) != today + status:
                notify(
                    "Watcher: %s blocked" % name,
                    "%s refused the check (%s). I will keep trying." % (name, e.code),
                    priority="low",
                )
                state["last_status"][name] = today + status
            continue
        except Exception as e:
            print(name, "failed:", e)
            summary.append("%s: could not load" % name)
            continue

        lowered = html.lower()
        if any(w in lowered for w in BLOCKED_WORDS) and len(html) < 60000:
            summary.append("%s: blocked by a bot check" % name)
            if state["last_status"].get(name) != today + "bot":
                notify(
                    "Watcher: %s blocked" % name,
                    "%s showed a robot check, so nothing could be read today." % name,
                    priority="low",
                )
                state["last_status"][name] = today + "bot"
            continue

        current = find_in_stock(store, html)
        summary.append("%s: %d buyable item(s) found" % (name, len(current)))

        previous = state["in_stock"].get(name, {})
        new_items = {u: l for u, l in current.items() if u not in previous}

        for url, label in new_items.items():
            if not first_run:
                notify(
                    "IN STOCK at %s" % name,
                    "%s\nTap to open." % label,
                    link=url,
                    priority="high",
                )
            print("NEW:", name, url)

        state["in_stock"][name] = current

    if first_run:
        notify(
            "Watcher is running",
            "First check done.\n" + "\n".join(summary),
        )
        state["first_run_done"] = True

    print("\n".join(summary))
    save_state(state)


if __name__ == "__main__":
    sys.exit(main())
