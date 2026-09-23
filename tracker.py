import sqlite3
import datetime
import re
import os
import random
import asyncio
import logging
import requests
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# ==========================================
# LOAD ENVIRONMENT VARIABLES
# ==========================================
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in environment variables or .env file.")

PROFILE_DIR = os.path.join(os.getcwd(), "chrome_profile")
DB_NAME = 'price_tracker.db'

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Global Playwright instance references
playwright_instance = None
browser_context = None


# ==========================================
# DATABASE LOGIC
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            asin TEXT PRIMARY KEY,
            name TEXT,
            url TEXT,
            added_date DATETIME
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asin TEXT,
            price REAL,
            timestamp DATETIME
        )
    ''')
    conn.commit()
    conn.close()


def extract_clean_amazon_url(text: str):
    """Extracts exact domain, ASIN, and parameters directly from raw message text."""
    url_match = re.search(r'https?://[^\s]+', text)
    if not url_match:
        return None, None

    raw_url = url_match.group(0)
    parsed = urlparse(raw_url)

    asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', parsed.path)
    if not asin_match:
        return None, None

    asin = asin_match.group(1)

    domain_match = re.search(r'(www\.)?amazon\.[a-z\.]+', raw_url)
    if domain_match:
        domain = domain_match.group(0)
    else:
        domain = parsed.netloc if parsed.netloc else "www.amazon.com"

    query_params = parse_qs(parsed.query)
    clean_params = {}
    if 'th' in query_params:
        clean_params['th'] = query_params['th'][0]
    if 'psc' in query_params:
        clean_params['psc'] = query_params['psc'][0]

    query_string = urlencode(clean_params)
    clean_path = f"/dp/{asin}"

    clean_url = urlunparse(("https", domain, clean_path, '', query_string, ''))
    return asin, clean_url


# ==========================================
# TELEGRAM API HELPERS
# ==========================================
def send_telegram_message(text: str):
    """Sends a standard text message to your Telegram chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": False
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
    except Exception as e:
        logging.error(f"Failed to send Telegram message: {e}")


def fetch_latest_telegram_messages():
    """Reads unread messages sent to the Telegram bot."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()

        messages = []
        highest_update_id = None

        if data.get("ok") and data.get("result"):
            for result in data["result"]:
                highest_update_id = result["update_id"]
                msg = result.get("message", {})
                sender_id = str(msg.get("from", {}).get("id"))

                if sender_id == str(TELEGRAM_CHAT_ID) and "text" in msg:
                    messages.append(msg["text"].strip())

            if highest_update_id:
                requests.get(f"{url}?offset={highest_update_id + 1}", timeout=10)

        return messages
    except Exception as e:
        logging.error(f"Failed to fetch Telegram updates: {e}")
        return []


# ==========================================
# SCRAPER ENGINE (PLAYWRIGHT ASYNC)
# ==========================================
async def scrape_amazon_product(url: str):
    global playwright_instance, browser_context

    if not playwright_instance:
        playwright_instance = await async_playwright().start()
        browser_context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            viewport=None
        )

    page = await browser_context.new_page()

    try:
        await asyncio.sleep(random.uniform(1.5, 3.0))
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        content = await page.content()
        if "validateCaptcha" in content:
            return None, None, "CAPTCHA"

        # 1. Extract Product Title
        title = None
        title_element = await page.query_selector("#productTitle")
        if title_element:
            title_text = await title_element.inner_text()
            title = title_text.strip()

        # 2. Extract Product Price
        price = None
        price_selectors = [
            "#corePrice_feature_div .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .a-offscreen",
            "#apex_desktop .a-offscreen",
            ".a-price .a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            ".priceToPay .a-offscreen",
            "#price_inside_buybox"
        ]

        for selector in price_selectors:
            try:
                elements = await page.query_selector_all(selector)
                for element in elements:
                    raw_text = await element.inner_text()
                    clean_str = "".join([c for c in raw_text.strip() if c.isdigit() or c == '.'])
                    if clean_str and float(clean_str) > 0:
                        price = float(clean_str)
                        break
                if price:
                    break
            except Exception:
                continue

        # Fallback 1: Apex Whole/Fraction block text parsing
        if not price:
            try:
                whole = await page.query_selector(".a-price-whole")
                fraction = await page.query_selector(".a-price-fraction")
                if whole:
                    w_text = "".join([c for c in (await whole.inner_text()) if c.isdigit()])
                    f_text = "".join([c for c in (await fraction.inner_text()) if c.isdigit()]) if fraction else "00"
                    if w_text:
                        price = float(f"{w_text}.{f_text}")
            except Exception:
                pass

        # Fallback 2: Regex extraction from main price container
        if not price:
            try:
                price_box = await page.query_selector("#centerCol") or await page.query_selector("#buyBoxAccordion")
                if price_box:
                    text_content = await price_box.inner_text()
                    matches = re.findall(r'(?:[\$£€])\s?(\d+\.\d{2})', text_content)
                    if matches:
                        price = float(matches[0])
            except Exception:
                pass

        return title, price, None
    finally:
        await page.close()


# ==========================================
# PROCESS SINGLE PRODUCT
# ==========================================
async def process_single_item(asin, existing_name, url):
    title, current_price, error = await scrape_amazon_product(url)

    display_name = title if title else existing_name

    if error == "CAPTCHA":
        return f"Product: {display_name}\n⚠️ CAPTCHA encountered! Please solve manually in Chrome."

    if current_price is None:
        return f"Product: {display_name}\n❌ Failed to extract price from Amazon."

    now = datetime.datetime.now()

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if title:
        cursor.execute("UPDATE products SET name = ? WHERE asin = ?", (title, asin))

    cursor.execute(
        "INSERT INTO price_history (asin, price, timestamp) VALUES (?, ?, ?)",
        (asin, current_price, now)
    )

    lookback_cutoff = now - datetime.timedelta(days=365)
    cursor.execute('''
        SELECT MAX(price) FROM price_history 
        WHERE asin = ? AND timestamp >= ?
    ''', (asin, lookback_cutoff))

    max_price = cursor.fetchone()[0]
    if max_price is None or max_price == 0:
        max_price = current_price

    percentage_drop = ((max_price - current_price) / max_price) * 100 if max_price > 0 else 0.0
    conn.commit()
    conn.close()

    return (
        f"Product: {display_name}\n"
        f"Peak Price (365-Day Window): {max_price:.2f}\n"
        f"Current Price: {current_price:.2f}\n"
        f"Price drop: {percentage_drop:.2f}%"
    )


# ==========================================
# MAIN EXECUTION FLOW
# ==========================================
async def main():
    global playwright_instance, browser_context
    init_db()
    logging.info("Starting Amazon Price Tracker execution run...")

    try:
        logging.info("Reading unread messages from Telegram...")
        incoming_texts = fetch_latest_telegram_messages()

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        for text in incoming_texts:
            asin, clean_url = extract_clean_amazon_url(text)
            if asin and clean_url:
                cursor.execute("SELECT asin FROM products WHERE asin = ?", (asin,))
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO products (asin, name, url, added_date) VALUES (?, ?, ?, ?)",
                        (asin, f"Amazon Product ({asin})", clean_url, datetime.datetime.now())
                    )
                    conn.commit()
                    send_telegram_message(f"✅ Added new link to tracking database!\nASIN: {asin}\nURL: {clean_url}")

        cursor.execute("SELECT asin, name, url FROM products")
        tracked_products = cursor.fetchall()
        conn.close()

        if not tracked_products:
            logging.info("No products currently in tracking list.")
            send_telegram_message("ℹ️ No Amazon products are currently being tracked. Send an Amazon link to add one!")
            return

        logging.info(f"Checking prices for all {len(tracked_products)} tracked product(s)...")

        for asin, name, url in tracked_products:
            logging.info(f"Checking product {asin} at {url}...")
            report = await process_single_item(asin, name, url)
            send_telegram_message(report)
            await asyncio.sleep(2)

    finally:
        # Cleanup: close browser and stop Playwright cleanly
        if browser_context:
            await browser_context.close()
            logging.info("Chromium browser closed.")
        if playwright_instance:
            await playwright_instance.stop()
            logging.info("Playwright stopped.")

    logging.info("Execution complete. Program exiting.")


if __name__ == "__main__":
    asyncio.run(main())