#!/usr/bin/env python3
"""
Test in één keer welke TCG-webshops bruikbaar zijn.

    python diagnose_all.py              # test de standaardlijst
    python diagnose_all.py shops.txt    # test je eigen lijst (1 URL per regel)

Per shop:
  1. homepage ophalen -> bereikbaar? welk platform?
  2. de JSON-endpoints van dat platform proberen
  3. tellen hoeveel producten er terugkomen

Aan het eind komt er een kant-en-klaar config-blok uit met alleen de shops
die werken. Dat kun je zo in config.yml plakken.
"""
import re
import sys
import time
import requests

BROWSER = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
    "sec-ch-ua": '"Chromium";v="126", "Not.A/Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Upgrade-Insecure-Requests": "1",
}

# Nederlandse TCG-webshops die naar NL leveren.
SHOPS = [
    "https://beamcardshop.com",
    "https://catchyourcards.nl",
    "https://ceescards.eu",
    "https://www.pkmkaarten.nl",
    "https://www.bescards.com",
    "https://tcgcompany.nl",
    "https://davidptcg.nl",
    "https://www.debroergrot.nl",
    "https://www.tcgreus.nl",
    "https://rarecards.nl",
    "https://pokeca.nl",
    "https://lichcards.nl",
    "https://arlytrading.nl",
    "https://nerdup.nl",
    "https://zycards.nl",
    "https://2hg.nl",
    "https://tcgholland.com",
    "https://tcgtwan.nl",
    "https://mintyfresh.eu",
    "https://dacardworld.eu",
    # --- nieuwe kandidaten (sept 2026), nog niet getest ---
    "https://kaartkamer.nl",
    "https://pokemagic.nl",
    "https://www.pkmncardshop.nl",
    "https://pokefamily.nl",
    "https://www.pkmwinkel.nl",
    "https://monkeltcg.nl",
    "https://derwincollectables.nl",
    "https://jaggazard.nl",
    "https://mysticcollectors.com",
    "https://www.2ttoys.nl",
]

PLATFORM_HINTS = [
    ("shopify", ["cdn.shopify.com", "shopify.com/s/files", "Shopify.theme", "myshopify"]),
    ("woocommerce", ["/wp-content/plugins/woocommerce", "woocommerce-", "wp-json/wc"]),
    ("lightspeed", ["static.webshopapp.com", "lightspeed"]),
    ("ccvshop", ["ccvshop.nl", "webshopapp"]),
    ("shopware", ["shopware", "/widgets/"]),
    ("magento", ["mage/", "static/version"]),
]

ENDPOINTS = {
    "shopify": ["/products.json?limit=5"],
    "woocommerce": ["/wp-json/wc/store/v1/products?per_page=5",
                    "/?rest_route=/wc/store/v1/products&per_page=5"],
    "unknown": ["/products.json?limit=5", "/wp-json/wc/store/v1/products?per_page=5"],
}


def detect_platform(html: str) -> str:
    low = html.lower()
    for name, needles in PLATFORM_HINTS:
        if any(n.lower() in low for n in needles):
            return name
    return "unknown"


def product_names(data) -> list[str]:
    items = data if isinstance(data, list) else (data.get("products", []) if isinstance(data, dict) else [])
    names = []
    for it in items:
        if isinstance(it, dict):
            names.append(str(it.get("name") or it.get("title") or "?"))
    return names


TCG_WORDS = ["pokémon", "pokemon", "booster", "elite trainer", "etb", "tcg",
             "trading card", "yu-gi-oh", "yugioh", "magic the gathering", "mtg",
             "one piece", "lorcana", "kaart", "card"]


def tcg_share(names: list[str]) -> float:
    """Welk deel van de producten lijkt op kaartspullen? Zo filteren we
    shops eruit die iets heel anders verkopen."""
    if not names:
        return 0.0
    hits = sum(1 for n in names if any(w in n.lower() for w in TCG_WORDS))
    return hits / len(names)


def test_shop(base: str) -> dict:
    base = base.rstrip("/")
    result = {"url": base, "reachable": False, "platform": "?",
              "adapter": None, "endpoint": None, "count": 0, "note": "",
              "samples": [], "tcg": 0.0}

    session = requests.Session()
    session.headers.update(BROWSER)

    try:
        home = session.get(base + "/", timeout=25)
    except requests.RequestException as e:
        result["note"] = f"netwerkfout: {type(e).__name__}"
        return result

    if home.status_code != 200:
        cf = "cloudflare" in home.headers.get("server", "").lower()
        result["note"] = f"HTTP {home.status_code}" + (" (Cloudflare blokkeert)" if cf else "")
        return result

    result["reachable"] = True
    result["platform"] = detect_platform(home.text)
    time.sleep(1)

    candidates = ENDPOINTS.get(result["platform"], ENDPOINTS["unknown"])
    for path in candidates:
        url = base + path
        headers = {"Accept": "application/json, text/plain, */*",
                   "Referer": base + "/", "X-Requested-With": "XMLHttpRequest"}
        try:
            r = session.get(url, headers=headers, timeout=25)
        except requests.RequestException:
            continue

        if r.status_code != 200:
            result["note"] = f"API HTTP {r.status_code}"
            continue
        try:
            data = r.json()
        except ValueError:
            result["note"] = "API geeft geen JSON"
            continue

        names = product_names(data)
        if names:
            result["adapter"] = "shopify" if "products.json" in path else "woocommerce"
            result["endpoint"] = path.split("?")[0]
            result["count"] = len(names)
            result["samples"] = names[:3]
            result["tcg"] = tcg_share(names)
            result["note"] = "OK" if result["tcg"] >= 0.4 else "LET OP: lijkt geen TCG-shop"
            return result

    if not result["note"]:
        result["note"] = "geen bruikbare API — HTML-adapter nodig"
    return result


def main() -> int:
    shops = SHOPS
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            shops = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

    results = []
    for i, shop in enumerate(shops, 1):
        print(f"[{i}/{len(shops)}] {shop}", flush=True)
        res = test_shop(shop)
        results.append(res)
        status = "✓" if res["adapter"] and res["tcg"] >= 0.4 else ("!" if res["adapter"] else "✗")
        print(f"    {status} platform={res['platform']} adapter={res['adapter']} "
              f"kaartspullen={res['tcg']:.0%} — {res['note']}", flush=True)
        for sample in res["samples"]:
            print(f"        · {sample[:70]}", flush=True)
        print(flush=True)
        time.sleep(2)

    ok = [r for r in results if r["adapter"] and r["tcg"] >= 0.4]
    wrong = [r for r in results if r["adapter"] and r["tcg"] < 0.4]
    reachable_no_api = [r for r in results if r["reachable"] and not r["adapter"]]
    blocked = [r for r in results if not r["reachable"]]

    print("\n" + "=" * 60)
    print(f"SAMENVATTING: {len(ok)} bruikbaar, {len(reachable_no_api)} zonder API, "
          f"{len(blocked)} geblokkeerd\n")

    if blocked:
        print("Geblokkeerd (niet te monitoren vanaf GitHub):")
        for r in blocked:
            print(f"  - {r['url']}: {r['note']}")
        print()

    if wrong:
        print("Werkt technisch, maar verkoopt geen kaarten — NIET toevoegen:")
        for r in wrong:
            print(f"  - {r['url']}: voorbeeld \"{(r['samples'] or ['?'])[0][:50]}\"")
        print()

    if reachable_no_api:
        print("Bereikbaar maar geen JSON-API (kan met de html-adapter):")
        for r in reachable_no_api:
            print(f"  - {r['url']} ({r['platform']}): {r['note']}")
        print()

    if ok:
        print("Plak dit in config.yml onder 'sites:':\n")
        for r in ok:
            name = re.sub(r"^(https?://)?(www\.)?", "", r["url"]).split(".")[0]
            print(f"""  - name: {name.title()}
    enabled: true
    adapter: {r['adapter']}
    base_url: {r['url']}
    max_pages: 10
    include: ["pokémon", "pokemon"]
    exclude: ["sleeve", "toploader", "binder", "playmat"]
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
