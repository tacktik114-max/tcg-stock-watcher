#!/usr/bin/env python3
"""
TCG Stock Watcher
-----------------
Monitort webshops op:
  1. NIEUWE producten (bv. een nieuwe set die online komt)
  2. RESTOCKS (product ging van 'uitverkocht' naar 'op voorraad')
  3. (optioneel) prijsdalingen

Meldingen gaan via ntfy. Status wordt bewaard in state/<site>.json,
zodat de volgende run weet wat er veranderd is.

Gebruik:
    python monitor.py                 # normale run
    python monitor.py --dry-run       # alles checken, niets versturen/opslaan
    python monitor.py --test-ntfy     # testmelding sturen
    python monitor.py --site CatchYourCards   # 1 site checken
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
STATE_DIR = ROOT / "state"
CONFIG_PATH = ROOT / "config.yml"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

TIMEOUT = 25


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def get(url: str, **kwargs) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
    r.raise_for_status()
    return r


def money(value, minor_unit: int = 2) -> float | None:
    """WooCommerce geeft prijzen in centen ('2295' + minor_unit 2)."""
    if value in (None, ""):
        return None
    try:
        return round(float(value) / (10 ** minor_unit), 2)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# Adapters — elk geeft een lijst dicts terug:
#   {id, name, url, in_stock, price, status}
# ----------------------------------------------------------------------

def fetch_woocommerce(site: dict) -> list[dict]:
    """WooCommerce Store API (publiek, geen key nodig).
    Endpoint: /wp-json/wc/store/v1/products
    """
    base = site["base_url"].rstrip("/")
    endpoint = site.get("endpoint", "/wp-json/wc/store/v1/products")
    max_pages = int(site.get("max_pages", 10))
    per_page = int(site.get("per_page", 100))
    products: list[dict] = []

    for page in range(1, max_pages + 1):
        params = {"per_page": per_page, "page": page}
        if site.get("category"):
            params["category"] = site["category"]
        if site.get("search"):
            params["search"] = site["search"]

        r = get(base + endpoint, params=params)
        batch = r.json()
        if not batch:
            break

        for p in batch:
            prices = p.get("prices") or {}
            products.append({
                "id": str(p.get("id")),
                "name": p.get("name", "").strip(),
                "url": p.get("permalink") or base,
                "in_stock": bool(p.get("is_in_stock")) and bool(p.get("is_purchasable", True)),
                "price": money(prices.get("price"), int(prices.get("currency_minor_unit", 2))),
                "status": "op voorraad" if p.get("is_in_stock") else "uitverkocht",
            })

        if len(batch) < per_page:
            break
        time.sleep(0.5)

    return products


def fetch_shopify(site: dict) -> list[dict]:
    """Shopify shops geven /products.json publiek vrij."""
    base = site["base_url"].rstrip("/")
    path = site.get("endpoint", "/products.json")
    max_pages = int(site.get("max_pages", 5))
    products: list[dict] = []

    for page in range(1, max_pages + 1):
        r = get(base + path, params={"limit": 250, "page": page})
        batch = r.json().get("products", [])
        if not batch:
            break

        for p in batch:
            variants = p.get("variants") or []
            in_stock = any(v.get("available") for v in variants)
            price = None
            if variants:
                try:
                    price = round(float(variants[0].get("price")), 2)
                except (TypeError, ValueError):
                    price = None
            products.append({
                "id": str(p.get("id")),
                "name": (p.get("title") or "").strip(),
                "url": f"{base}/products/{p.get('handle')}",
                "in_stock": in_stock,
                "price": price,
                "status": "op voorraad" if in_stock else "uitverkocht",
            })

        if len(batch) < 250:
            break
        time.sleep(0.5)

    return products


def fetch_html(site: dict) -> list[dict]:
    """Laatste redmiddel: HTML scrapen met CSS-selectors uit de config."""
    sel = site.get("selectors", {})
    products: list[dict] = []

    for url in site.get("urls", [site["base_url"]]):
        r = get(url)
        soup = BeautifulSoup(r.text, "html.parser")

        for card in soup.select(sel.get("product", "li.product")):
            link_el = card.select_one(sel.get("link", "a")) or card
            name_el = card.select_one(sel.get("name", "h2")) or link_el
            price_el = card.select_one(sel.get("price", ".price"))

            href = link_el.get("href") if link_el else None
            if not href:
                continue
            href = urljoin(url, href)

            text = card.get_text(" ", strip=True).lower()
            out_words = [w.lower() for w in sel.get("out_of_stock_text",
                         ["uitverkocht", "niet op voorraad", "sold out", "uitverkocht!"])]
            in_stock = not any(w in text for w in out_words)

            price = None
            if price_el:
                m = re.search(r"(\d+[.,]\d{2})", price_el.get_text())
                if m:
                    price = float(m.group(1).replace(",", "."))

            products.append({
                "id": href,
                "name": name_el.get_text(" ", strip=True)[:160],
                "url": href,
                "in_stock": in_stock,
                "price": price,
                "status": "op voorraad" if in_stock else "uitverkocht",
            })

        time.sleep(1)

    return products


ADAPTERS = {
    "woocommerce": fetch_woocommerce,
    "shopify": fetch_shopify,
    "html": fetch_html,
}


# ----------------------------------------------------------------------
# Filters
# ----------------------------------------------------------------------

def passes_filters(product: dict, site: dict) -> bool:
    name = product["name"].lower()

    include = [w.lower() for w in site.get("include", [])]
    if include and not any(w in name for w in include):
        return False

    exclude = [w.lower() for w in site.get("exclude", [])]
    if any(w in name for w in exclude):
        return False

    max_price = site.get("max_price")
    if max_price and product.get("price") and product["price"] > float(max_price):
        return False

    return True


# ----------------------------------------------------------------------
# State
# ----------------------------------------------------------------------

def load_state(site_name: str) -> dict:
    path = STATE_DIR / f"{slugify(site_name)}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("products", {})
    except json.JSONDecodeError:
        log(f"WAARSCHUWING: state van {site_name} onleesbaar, opnieuw beginnen")
        return {}


def save_state(site_name: str, products: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    path = STATE_DIR / f"{slugify(site_name)}.json"
    path.write_text(
        json.dumps({"updated": now_iso(), "products": products},
                   ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


# ----------------------------------------------------------------------
# ntfy
# ----------------------------------------------------------------------

class Notifier:
    def __init__(self, cfg: dict, dry_run: bool = False):
        self.server = os.environ.get("NTFY_SERVER", cfg.get("server", "https://ntfy.sh")).rstrip("/")
        self.topic = os.environ.get("NTFY_TOPIC", cfg.get("topic", ""))
        self.token = os.environ.get("NTFY_TOKEN", cfg.get("token", ""))
        self.dry_run = dry_run
        if not self.topic:
            raise SystemExit("Geen ntfy-topic ingesteld (config.yml of NTFY_TOPIC).")

    def send(self, title: str, message: str, *, click: str | None = None,
             tags: list[str] | None = None, priority: int = 3) -> None:
        payload = {
            "topic": self.topic,
            "title": title,
            "message": message,
            "priority": priority,
            "tags": tags or [],
        }
        if click:
            payload["click"] = click

        if self.dry_run:
            log(f"[DRY-RUN] {title} | {message}")
            return

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        for attempt in range(3):
            try:
                r = requests.post(self.server, json=payload, headers=headers, timeout=15)
                if r.status_code < 400:
                    return
                log(f"ntfy gaf {r.status_code}: {r.text[:200]}")
            except requests.RequestException as e:
                log(f"ntfy fout: {e}")
            time.sleep(2 * (attempt + 1))


# ----------------------------------------------------------------------
# Kern
# ----------------------------------------------------------------------

def fmt_price(p) -> str:
    return f"€{p:.2f}".replace(".", ",") if p else "prijs onbekend"


def check_site(site: dict, notifier: Notifier, settings: dict, dry_run: bool) -> int:
    name = site["name"]
    adapter = ADAPTERS.get(site.get("adapter", "woocommerce"))
    if adapter is None:
        log(f"{name}: onbekende adapter '{site.get('adapter')}' — overgeslagen")
        return 0

    log(f"{name}: ophalen…")
    try:
        raw = adapter(site)
    except Exception as e:  # noqa: BLE001
        log(f"{name}: FOUT bij ophalen — {e}")
        if settings.get("notify_errors", True):
            notifier.send(
                f"⚠️ {name} niet bereikbaar",
                f"{type(e).__name__}: {str(e)[:180]}",
                tags=["warning"], priority=2,
            )
        return 0

    products = [p for p in raw if passes_filters(p, site)]
    log(f"{name}: {len(products)} producten na filters (van {len(raw)})")

    old = load_state(name)
    first_run = not old

    new_state: dict[str, dict] = {}
    events: list[dict] = []

    for p in products:
        key = p["id"]
        prev = old.get(key)
        entry = {
            "name": p["name"],
            "url": p["url"],
            "in_stock": p["in_stock"],
            "price": p["price"],
            "first_seen": prev.get("first_seen") if prev else now_iso(),
            "last_seen": now_iso(),
        }
        new_state[key] = entry

        if first_run:
            continue

        if prev is None:
            events.append({"type": "new", "p": p})
        else:
            if p["in_stock"] and not prev.get("in_stock"):
                events.append({"type": "restock", "p": p})
            elif settings.get("notify_out_of_stock", False) and not p["in_stock"] and prev.get("in_stock"):
                events.append({"type": "gone", "p": p})

            if settings.get("notify_price_drops", False):
                old_price, new_price = prev.get("price"), p["price"]
                if old_price and new_price and new_price < old_price * 0.95:
                    events.append({"type": "price", "p": p, "old_price": old_price})

    # Producten die verdwenen zijn: uit de state halen (niet melden).
    if first_run:
        log(f"{name}: eerste run — {len(products)} producten vastgelegd, geen meldingen")
        notifier.send(
            f"👀 Monitoring gestart: {name}",
            f"{len(products)} producten vastgelegd. Je krijgt vanaf nu bericht bij "
            f"nieuwe producten en restocks.",
            click=site["base_url"], tags=["eyes"], priority=2,
        )
    else:
        send_events(name, events, notifier, settings, site)

    if not dry_run:
        save_state(name, new_state)

    return len(events)


def send_events(site_name: str, events: list[dict], notifier: Notifier,
                settings: dict, site: dict) -> None:
    if not events:
        log(f"{site_name}: niets veranderd")
        return

    log(f"{site_name}: {len(events)} wijziging(en)")
    cap = int(settings.get("max_notifications", 12))

    # Te veel tegelijk (bv. hele nieuwe set online) → één samenvatting.
    if len(events) > cap:
        lines = [f"• {e['p']['name']}" for e in events[:20]]
        rest = len(events) - len(lines)
        if rest > 0:
            lines.append(f"…en nog {rest} andere")
        notifier.send(
            f"🃏 {len(events)} wijzigingen bij {site_name}",
            "\n".join(lines),
            click=site["base_url"], tags=["card_index"], priority=4,
        )
        return

    meta = {
        "restock": ("🔥 Weer op voorraad", ["fire"], 5),
        "new": ("🆕 Nieuw product", ["new"], 4),
        "price": ("📉 Prijs omlaag", ["chart_with_downwards_trend"], 3),
        "gone": ("❌ Uitverkocht", ["x"], 2),
    }

    for e in events:
        p = e["p"]
        prefix, tags, prio = meta[e["type"]]
        if e["type"] == "price":
            body = f"{fmt_price(e['old_price'])} → {fmt_price(p['price'])}\n{site_name}"
        else:
            body = f"{fmt_price(p['price'])}\n{site_name}"
        notifier.send(f"{prefix}: {p['name']}"[:120], body,
                      click=p["url"], tags=tags, priority=prio)
        time.sleep(0.4)  # ntfy rate limit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--site", help="Alleen deze site checken (naam uit config)")
    ap.add_argument("--dry-run", action="store_true", help="Niets versturen of opslaan")
    ap.add_argument("--test-ntfy", action="store_true", help="Alleen een testmelding sturen")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    settings = cfg.get("settings", {})
    notifier = Notifier(cfg.get("ntfy", {}), dry_run=args.dry_run)

    if args.test_ntfy:
        notifier.send("✅ Test", "TCG Stock Watcher werkt.", tags=["white_check_mark"])
        return 0

    sites = [s for s in cfg.get("sites", []) if s.get("enabled", True)]
    if args.site:
        sites = [s for s in sites if s["name"].lower() == args.site.lower()]
        if not sites:
            log(f"Site '{args.site}' niet gevonden in config")
            return 1

    total = 0
    for site in sites:
        total += check_site(site, notifier, settings, args.dry_run)

    log(f"Klaar — {total} melding(en) in totaal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
