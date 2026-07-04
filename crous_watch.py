import json
import os
import re
import sys
import time
from pathlib import Path

import requests

BASE_URL = "https://trouverunlogement.lescrous.fr"
TOOL_ID = 45
SEARCH_PATH = f"/tools/{TOOL_ID}/search"
STATE_FILE = Path(__file__).parent / "logements_vus.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

PARIS_POSTAL_RE = re.compile(r"\b(75\d{3})\b")
PRICE_RE = re.compile(r"(\d[\d,\.]*)\s*€")
TAG_RE = re.compile(r"<[^>]+>")
ACCOMMODATION_LINK_RE = re.compile(rf'href="(/tools/{TOOL_ID}/accommodations/(\d+))"')
TOTAL_PAGES_RE = re.compile(r"page \d+ sur (\d+)")


def send_telegram(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants, message non envoyé :")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "disable_web_page_preview": False},
            timeout=15,
        )
        if not r.ok:
            print("Erreur Telegram:", r.status_code, r.text)
    except requests.RequestException as e:
        print("Erreur réseau Telegram:", e)


def get_total_pages(html: str) -> int:
    m = TOTAL_PAGES_RE.search(html)
    return int(m.group(1)) if m else 1


def parse_listings(html: str) -> dict:
    listings = {}
    for m in ACCOMMODATION_LINK_RE.finditer(html):
        acc_path, acc_id = m.group(1), m.group(2)
        if acc_id in listings:
            continue
        window = html[max(0, m.start() - 200): m.end() + 1500]
        text = TAG_RE.sub(" ", window)
        text = re.sub(r"\s+", " ", text).strip()
        price_m = PRICE_RE.search(text)
        postal_m = PARIS_POSTAL_RE.search(text)
        listings[acc_id] = {
            "url": BASE_URL + acc_path,
            "price": price_m.group(0) if price_m else "prix non trouvé",
            "is_paris": bool(postal_m),
            "postal": postal_m.group(1) if postal_m else None,
        }
    return listings


def fetch_all_listings() -> dict:
    session = requests.Session()
    all_listings = {}
    first = session.get(BASE_URL + SEARCH_PATH, headers=HEADERS, timeout=20)
    first.raise_for_status()
    total_pages = get_total_pages(first.text)
    all_listings.update(parse_listings(first.text))
    print(f"→ {total_pages} pages de résultats à parcourir")
    for page in range(2, total_pages + 1):
        r = session.get(BASE_URL + SEARCH_PATH, params={"page": page}, headers=HEADERS, timeout=20)
        if r.ok:
            all_listings.update(parse_listings(r.text))
        time.sleep(0.3)
    return all_listings


def load_last_scan() -> set:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_last_scan(ids: set) -> None:
    STATE_FILE.write_text(json.dumps(sorted(ids)))


def main():
    is_first_run = not STATE_FILE.exists()
    listings = fetch_all_listings()
    paris = {k: v for k, v in listings.items() if v["is_paris"]}
    last_scan = load_last_scan()
    new_ids = set(paris.keys()) - last_scan

    print(f"{len(listings)} logements au total en France, {len(paris)} à Paris, "
          f"{len(new_ids)} nouveaux/redevenus disponibles depuis le dernier passage.")

    if is_first_run:
        print("Premier lancement : liste de référence enregistrée, pas de notification envoyée.")
        for acc_id, info in paris.items():
            print(f"  - {info['url']} ({info['price']})")
    else:
        for acc_id in new_ids:
            info = paris[acc_id]
            msg = (
                "🏠 Logement CROUS disponible à Paris !\n\n"
                f"💶 {info['price']}\n"
                f"📍 {info['postal']}\n"
                f"🔗 {info['url']}"
            )
            send_telegram(msg)

    save_last_scan(set(paris.keys()))


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        print("Erreur de connexion au site CROUS:", e, file=sys.stderr)
        sys.exit(1)
