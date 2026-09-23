import os
import re
import sys
import html
import asyncio
import logging
import sqlite3
from typing import Optional
from urllib.parse import urlparse
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
import requests


# --- Environment & Setup ---
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_USER_ID = str(os.getenv("ADMIN_USER_ID", TELEGRAM_CHAT_ID or ""))

DB_PATH = os.path.join(os.getcwd(), "tracker.db")
PROFILE_DIR = os.path.join(os.getcwd(), "chrome_profile")

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

ALLOWED_AMAZON_DOMAINS = {
    "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr", "amazon.it",
    "amazon.es", "amazon.ca", "amazon.co.jp", "amazon.in", "amazon.com.au"
}

SHORTENER_DOMAINS = {"amzn.eu", "a.co"}


# --- Database Setup ---
def init_db() -> None:
    """Initializes tables and performs schema migrations for existing databases."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    os.chmod(DB_PATH, 0o600)

    # Approved users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS approved_users (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tracked products table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
            title TEXT,
            last_price TEXT,
            peak_price TEXT,
            added_by TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Price history table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            price REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- Schema Migrations ---
    # Ensure peak_price column exists in products
    cursor.execute("PRAGMA table_info(products)")
    prod_cols = [col[1] for col in cursor.fetchall()]
    if "peak_price" not in prod_cols:
        cursor.execute("ALTER TABLE products ADD COLUMN peak_price TEXT")

    # Ensure url column exists in price_history
    cursor.execute("PRAGMA table_info(price_history)")
    history_cols = [col[1] for col in cursor.fetchall()]
    if "url" not in history_cols:
        cursor.execute("ALTER TABLE price_history ADD COLUMN url TEXT")

    # Ensure threshold_pct exists in products table
    cursor.execute("PRAGMA table_info(products)")
    prod_cols = [col[1] for col in cursor.fetchall()]
    if "threshold_pct" not in prod_cols:
        cursor.execute("ALTER TABLE products ADD COLUMN threshold_pct REAL DEFAULT 0.0")

    if ADMIN_USER_ID:
        cursor.execute(
            "INSERT OR IGNORE INTO approved_users (user_id, username, first_name) VALUES (?, ?, ?)",
            (ADMIN_USER_ID, "Admin", "Jason Che")
        )

    conn.commit()
    conn.close()


def is_user_approved(user_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM approved_users WHERE user_id = ?", (str(user_id),))
    result = cursor.fetchone()
    conn.close()
    return result is not None


def approve_user(user_id: str, username: str, first_name: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO approved_users (user_id, username, first_name) VALUES (?, ?, ?)",
            (str(user_id), username or "", first_name or "")
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logging.error(f"Failed to approve user {user_id}: {e}")
        return False


def get_tracked_products() -> list[tuple[int, str, str]]:
    """Retrieves all tracked product URLs from SQLite."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, url, last_price FROM products")
    rows = cursor.fetchall()
    conn.close()
    return rows


def parse_numeric_price(price_str: str) -> float:
    """Safely extracts a numeric float value from formatted price strings."""
    if not price_str:
        return 0.0
    clean_str = price_str.replace(",", "")
    digits = re.sub(r'[^\d.]', '', clean_str)
    try:
        return float(digits) if digits and re.search(r'\d', digits) else 0.0
    except ValueError:
        return 0.0


def save_product(url: str, title: str, price: str, user_id: str) -> dict:
    """
    Saves or updates a product in SQLite while strictly preserving
    existing history, peak prices, and original user tracking metrics.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    current_num = parse_numeric_price(price)

    # 1. Check if product already exists in database
    cursor.execute("SELECT last_price, peak_price FROM products WHERE url = ?", (url,))
    existing = cursor.fetchone()

    if existing:
        old_price_str, existing_peak_str = existing
        existing_peak_num = parse_numeric_price(existing_peak_str or "")

        # Preserve or elevate peak price
        if current_num > existing_peak_num:
            peak_price_str = price
        else:
            peak_price_str = existing_peak_str or price

        cursor.execute("""
            UPDATE products 
            SET title = ?, 
                last_price = ?, 
                peak_price = ?, 
                updated_at = CURRENT_TIMESTAMP
            WHERE url = ?
        """, (title, price, peak_price_str, url))

    else:
        peak_price_str = price
        cursor.execute("""
            INSERT INTO products (url, title, last_price, peak_price, added_by)
            VALUES (?, ?, ?, ?, ?)
        """, (url, title, price, price, user_id))

    # Log numeric entry to price_history
    if current_num > 0:
        cursor.execute(
            "INSERT INTO price_history (url, price) VALUES (?, ?)",
            (url, current_num)
        )

    conn.commit()
    conn.close()

    return {
        "title": title,
        "current_price": price,
        "peak_price": peak_price_str,
        "is_existing": existing is not None
    }


# --- URL Security ---
def sanitize_amazon_url(raw_url: str) -> Optional[str]:
    """
    Validates Amazon links, unshortens amzn.eu / a.co links using HTTP GET stream,
    strips tracking parameters, and returns a clean canonical Amazon URL.
    """
    if len(raw_url) > 2000 or not raw_url.startswith(("http://", "https://")):
        return None

    try:
        parsed = urlparse(raw_url)
        domain = parsed.netloc.lower().removeprefix("www.")

        # 1. Unshorten mobile app short-links (amzn.eu / a.co) using GET stream=True
        if domain in SHORTENER_DOMAINS:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            with requests.get(raw_url, allow_redirects=True, headers=headers, stream=True, timeout=10) as resp:
                raw_url = resp.url

            parsed = urlparse(raw_url)
            domain = parsed.netloc.lower().removeprefix("www.")

        # 2. Verify the resolved domain is an allowed Amazon storefront
        if domain not in ALLOWED_AMAZON_DOMAINS:
            logging.warning(f"Rejected non-Amazon domain after resolution: {domain}")
            return None

        # 3. Extract standard 10-character Amazon ASIN
        asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', parsed.path)
        if asin_match:
            asin = asin_match.group(1)
            return f"https://www.{domain}/dp/{asin}"

        return f"https://www.{domain}{parsed.path}"

    except Exception as e:
        logging.error(f"Error parsing URL {raw_url}: {e}")
        return None


# --- Playwright Scraper ---
async def scrape_amazon_price(url: str) -> dict:
    """Scrapes Amazon product title and price using persistent context."""
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Wait briefly for product title to populate in DOM
            await page.wait_for_selector("#productTitle", timeout=10000)

            title = "Unknown Product"
            title_elem = await page.query_selector("#productTitle")
            if title_elem:
                title = (await title_elem.inner_text()).strip()

            price = "Price not found"
            price_selectors = [
                ".a-price .a-offscreen",
                "#priceblock_ourprice",
                "#priceblock_dealprice",
                ".a-section .a-color-price"
            ]

            for selector in price_selectors:
                price_elem = await page.query_selector(selector)
                if price_elem:
                    price_text = (await price_elem.inner_text()).strip()
                    if price_text:
                        price = price_text
                        break

            await context.close()
            return {"title": title, "price": price, "url": url}

        except Exception as e:
            logging.error(f"Scraper error for {url}: {e}")
            await context.close()
            return {"title": "Error fetching product", "price": "Failed to scrape", "url": url}


# --- Daily Cron Task (Runs once and exits) ---
async def run_daily_cron_job():
    """Scrapes products, checks price drop thresholds, and sends notifications."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Retrieve tracked products along with their stored threshold_pct
    cursor.execute("SELECT url, title, last_price, peak_price, COALESCE(threshold_pct, 0.0) FROM products")
    products = cursor.fetchall()
    conn.close()

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    for url, old_title, old_price, peak_price, threshold_pct in products:
        data = await scrape_amazon_price(url)
        clean_new_price = data["price"]
        clean_title = data["title"] or old_title

        saved_info = save_product(url, clean_title, clean_new_price, "CRON")

        current_num = parse_numeric_price(clean_new_price)
        peak_num = parse_numeric_price(saved_info["peak_price"])

        # Calculate percentage drop relative to peak price
        if current_num > 0 and peak_num > 0 and peak_num >= current_num:
            drop_percent = round(((peak_num - current_num) / peak_num) * 100, 1)
        else:
            drop_percent = 0.0

        # Filter out products where current price drop is below requested threshold
        if threshold_pct > 0 and drop_percent < threshold_pct:
            logging.info(
                f"Skipped {clean_title}: Drop ({drop_percent}%) is below threshold ({threshold_pct}%)."
            )
            continue

        drop_str = f"{drop_percent}%" if drop_percent > 0 else "0% (All-time low / base price)"

        msg = (
            f"🔄 <b>Daily Price Update</b>\n"
            f"📌 <b>{html.escape(clean_title)}</b>\n\n"
            f"💰 <b>Current Price:</b> {html.escape(clean_new_price)} (Previous: {html.escape(old_price)})\n"
            f"📈 <b>Peak Price:</b> {html.escape(saved_info['peak_price'])}\n"
            f"📉 <b>Price Drop:</b> {drop_str}\n\n"
            f"🔗 <a href='{url}'>Amazon Link</a>"
        )

        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="HTML")


# --- Interactive Telegram Handler ---
async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.from_user or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    sender_id = str(message.from_user.id)
    text = (message.text or "").strip()

    # 1. Admin Confirmation Workflow
    if text.lower() == "confirm" and sender_id == ADMIN_USER_ID:
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user = message.reply_to_message.from_user
            target_id = str(target_user.id)
            target_username = target_user.username or ""
            target_name = target_user.first_name or "User"

            if approve_user(target_id, target_username, target_name):
                # Send approval confirmation message
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Permission granted to {html.escape(target_name)} (@{html.escape(target_username)}).",
                    parse_mode="HTML"
                )
                # Send interactive quick-start manual
                await send_user_manual(context.bot, chat_id, target_name)
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Database error while granting permissions."
                )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Reply 'confirm' directly to a user's message to authorize them."
            )
        return

    # 2. Authorization Check
    if not is_user_approved(sender_id):
        return

    # 3. Product Deletion Workflow ("delete" reply)
    if text.lower() == "delete":
        if not message.reply_to_message:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Please reply 'delete' directly to a message containing the Amazon product or link."
            )
            return

        reply_msg = message.reply_to_message
        candidate_urls = []

        # Extract plain text URLs from replied message or caption
        if reply_msg.text:
            candidate_urls.extend(re.findall(r'https?://[^\s>"]+', reply_msg.text))
        if reply_msg.caption:
            candidate_urls.extend(re.findall(r'https?://[^\s>"]+', reply_msg.caption))

        # Extract URLs embedded in Telegram link entities (<a href="..."> or text_link)
        entities = list(reply_msg.entities or ()) + list(reply_msg.caption_entities or ())
        for entity in entities:
            if hasattr(entity, 'url') and entity.url:
                candidate_urls.append(entity.url)

        deleted = False
        processed_urls = set()

        for raw_url in candidate_urls:
            clean_url = sanitize_amazon_url(raw_url)
            if clean_url and clean_url not in processed_urls:
                processed_urls.add(clean_url)
                if delete_product_by_url(clean_url):
                    deleted = True

        if deleted:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🗑️ Product removed from price tracking database."
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ No matching tracked product found in the replied message."
            )
        return

    # 4. Custom Percentage Threshold Workflow (reply "xx%" to a product)
    pct_match = re.match(r'^(\d+(?:\.\d+)?)\s*%$', text)
    if pct_match:
        if not message.reply_to_message:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Please reply with a percentage (e.g. '10%') directly to a product message."
            )
            return

        threshold_val = float(pct_match.group(1))
        reply_msg = message.reply_to_message
        candidate_urls = []

        if reply_msg.text:
            candidate_urls.extend(re.findall(r'https?://[^\s>"]+', reply_msg.text))
        if reply_msg.caption:
            candidate_urls.extend(re.findall(r'https?://[^\s>"]+', reply_msg.caption))

        entities = list(reply_msg.entities or ()) + list(reply_msg.caption_entities or ())
        for entity in entities:
            if hasattr(entity, 'url') and entity.url:
                candidate_urls.append(entity.url)

        updated = False
        processed_urls = set()

        for raw_url in candidate_urls:
            clean_url = sanitize_amazon_url(raw_url)
            if clean_url and clean_url not in processed_urls:
                processed_urls.add(clean_url)
                if update_product_threshold(clean_url, threshold_val):
                    updated = True

        if updated:
            if threshold_val > 0:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🎯 Alert threshold set to <b>{threshold_val}%</b>. Daily updates will only be sent when the drop reaches at least {threshold_val}%.",
                    parse_mode="HTML"
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="🎯 Threshold cleared (0%). Daily updates will be sent every day for this product."
                )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ No matching tracked product found in the replied message."
            )
        return

    # 5. Amazon Link Processing
    if "http" in text:
        extracted_urls = re.findall(r'https?://[^\s]+', text)
        for raw_url in extracted_urls:
            clean_url = sanitize_amazon_url(raw_url)
            if clean_url:
                await context.bot.send_message(chat_id=chat_id, text="🔎 Amazon link accepted. Fetching product info...")
                data = await scrape_amazon_price(clean_url)

                # Save product and retrieve tracked metrics
                saved_info = save_product(clean_url, data["title"], data["price"], sender_id)

                current_price_str = saved_info["current_price"]
                peak_price_str = saved_info["peak_price"]

                current_num = parse_numeric_price(current_price_str)
                peak_num = parse_numeric_price(peak_price_str)

                if current_num > 0 and peak_num > 0 and peak_num >= current_num:
                    drop_percent = round(((peak_num - current_num) / peak_num) * 100, 1)
                    drop_str = f"{drop_percent}%" if drop_percent > 0 else "0% (Lowest price recorded!)"
                else:
                    drop_str = "0%"

                response_text = (
                    f"📌 <b>{html.escape(data['title'])}</b>\n\n"
                    f"💰 <b>Current Price:</b> {html.escape(current_price_str)}\n"
                    f"📈 <b>Peak Price:</b> {html.escape(peak_price_str)}\n"
                    f"📉 <b>Price Drop:</b> {html.escape(drop_str)}\n\n"
                    f"✅ Saved to database for daily tracking!"
                )
                await context.bot.send_message(chat_id=chat_id, text=response_text, parse_mode="HTML")
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Invalid link. Only direct Amazon product links are accepted."
                )


# --- Delete Product from Database ---
def delete_product_by_url(url: str) -> bool:
    """Deletes a tracked product and its price history from SQLite by URL."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM products WHERE url = ?", (url,))
    deleted = cursor.rowcount > 0

    # Clean up associated price history entries
    cursor.execute("DELETE FROM price_history WHERE url = ?", (url,))

    conn.commit()
    conn.close()
    return deleted


# --- Update Product Threshold ---
def update_product_threshold(url: str, threshold_pct: float) -> bool:
    """Updates the minimum percentage drop threshold required for daily notifications."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET threshold_pct = ? WHERE url = ?", (threshold_pct, url))
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# --- Welcome Message ---
async def send_user_manual(bot: Bot, chat_id: int | str, user_name: str) -> None:
    """Sends a short usage manual to the group when a user is newly authorized."""
    clean_name = html.escape(user_name)
    manual_text = (
        f"🎉 <b>Welcome {clean_name}!</b> You are now authorized to use the Amazon Price Tracker.\n\n"
        f"📖 <b>Quick User Manual:</b>\n\n"
        f"1️⃣ <b>Track a Product:</b> Send any Amazon product link (or mobile app <code>amzn.eu</code> link) directly to the group. The bot will save it and start tracking.\n\n"
        f"2️⃣ <b>Set Price Drop Alert:</b> Reply to a product message with a percentage (e.g. <code>10%</code> or <code>15%</code>). You will only get daily alerts when the item drops by at least that percentage.\n\n"
        f"3️⃣ <b>Remove a Product:</b> Reply directly to a product message with <code>delete</code> to remove it from the tracking database.\n\n"
        f"4️⃣ <b>Daily Updates:</b> The bot automatically scans tracked products daily and posts price updates and price drop calculations here."
    )
    await bot.send_message(chat_id=chat_id, text=manual_text, parse_mode="HTML")


# --- Entry Point ---
if __name__ == "__main__":
    init_db()

    if "--cron" in sys.argv:
        asyncio.run(run_daily_cron_job())
    else:
        app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_incoming_message))
        logging.info("Running interactive Telegram bot listener... Press Ctrl+C to stop.")
        app.run_polling()