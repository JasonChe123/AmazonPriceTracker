# 🛒 Amazon Price Tracker Telegram Bot

An automated, multi-user Telegram bot that tracks Amazon product prices, logs historical price trends, calculates percentage price drops from peak values, and sends daily update summaries. Built with Python, `python-telegram-bot` (v21+), Playwright, and SQLite3.

---

## ✨ Features

- 🔗 **Short Link Resolution:** Resolves mobile short links (`amzn.eu` / `a.co`) into canonical Amazon product URLs.
- 📉 **Peak Price & Drop Tracking:** Preserves all-time peak prices and calculates real-time price drop percentages.
- 🎯 **Custom Alert Thresholds:** Set custom drop alert thresholds per item (e.g., reply `10%` to a product message to receive daily notifications only when the item drops by at least 10%).
- 🗑️ **Interactive Management:** Delete tracked items by replying `delete` directly to product messages.
- 🔐 **Admin Access Control:** Restrict tracking privileges to authorized users using the `confirm` reply command.
- 📖 **Automatic Onboarding:** Sends a quick-start user manual to newly confirmed group members.
- 📅 **Dual Execution Modes:** Supports both 24/7 interactive listening and headless scheduled daily checks (`--cron`).

---

## 🛠️ Setup & Installation

### Option A: Linux / Ubuntu / macOS

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/AmazonPriceTracker.git
   cd AmazonPriceTracker
   ```

2. **Create virtual environment & install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium
   ```

3. **Make shell script executable:**
   ```bash
   chmod +x run_tracker.sh
   ```

---

### Option B: Windows Setup

1. **Clone the repository:**
   ```cmd
   git clone https://github.com/your-username/AmazonPriceTracker.git
   cd AmazonPriceTracker
   ```

2. **Create virtual environment & install dependencies:**
   ```cmd
   python -m venv .venv
   .venv\Scripts\activate.bat
   pip install -r requirements.txt
   playwright install chromium
   ```

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and populate your credentials:

```bash
cp .env.example .env
```

```env
TELEGRAM_BOT_TOKEN="your_telegram_bot_token_here"
TELEGRAM_CHAT_ID="-1001234567890"  # Must be a negative integer for group chats
ADMIN_USER_ID="123456789"          # Your personal Telegram User ID
```

---

## 🚀 Execution Modes

### 1. Interactive Listener Mode (24/7 or Manual Link Submissions)
Runs the live Telegram bot listener to accept links, authorize users, and manage threshold settings in real time.
```bash
python tracker.py
```

### 2. Headless Daily Price Check (Cron / Scheduled Execution)
Executes a single headless scan across all products stored in `tracker.db`, calculates price deltas, sends Telegram updates for items meeting alert thresholds, and exits cleanly.
- **Linux / Ubuntu:** `./run_tracker.sh`
- **Windows:** `run_tracker.bat`

---

## ⏰ Automating Daily Price Checks

### Option 1: Linux / Ubuntu (Crontab)
Open crontab editor:
```bash
crontab -e
```
Add an entry to execute daily at 9:00 AM:
```cron
0 9 * * * /home/username/AmazonPriceTracker/run_tracker.sh >> /home/username/AmazonPriceTracker/cron.log 2>&1
```

### Option 2: Windows (Task Scheduler)
1. Create run_tracker.bat in your terminal: On Windows Command Prompt (cmd.exe): ```(
echo @echo off
echo cd /d "%%~dp0"
echo call .venv\Scripts\activate
echo python tracker.py --cron
) > run_tracker.bat```
2. Open **Task Scheduler** from the Start Menu.
3. Select **Create Basic Task...** in the right sidebar.
4. Name:** `Amazon Price Tracker Cron`
5. Trigger:** Select **Daily** and set your preferred run time (e.g., `09:00 AM`).
6. Action:** Select **Start a program**.
7. Program/script:** Browse and select your `run_tracker.bat` path (`C:\Users\YourName\Documents\AmazonPriceTracker\run_tracker.bat`).
8. Start in (optional):** Enter your full project directory (`C:\Users\YourName\Documents\AmazonPriceTracker\`).
9. Click **Finish**.

---

## 📖 Telegram Group User Guide

| Action | Command / Method | Description |
| :--- | :--- | :--- |
| **Authorize User** | Reply `confirm` | Admin replies `confirm` directly to a member's message to grant access and display the user manual. |
| **Track Product** | Send Amazon Link | Send any standard Amazon URL or `amzn.eu` short link directly to the group. |
| **Set Alert Threshold** | Reply `10%` | Reply directly to a product message with a percentage (e.g. `10%` or `15.5%`) to filter daily updates. |
| **Reset Threshold** | Reply `0%` | Reply `0%` to receive daily updates regardless of price changes. |
| **Delete Product** | Reply `delete` | Reply `delete` directly to a product message to remove it from tracking. |

---

## 📁 Database Management

To clear all stored products and price histories while resetting auto-increment sequences, run:
```bash
python reset_db.py
```