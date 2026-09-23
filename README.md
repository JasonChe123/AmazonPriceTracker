# Amazon Price Tracker Telegram Bot

An automated, multi-user Telegram bot that tracks Amazon product prices, logs historical prices, calculates price drops, and sends daily updates. Built with Python, `python-telegram-bot` (v21+), Playwright, and SQLite3.

## Features

- 🔗 **Short Link Resolution:** Automatically expands and sanitizes `amzn.eu` / `a.co` short links into canonical Amazon URLs.
- 📉 **Price Drop Metrics:** Retains peak prices and calculates percentage drops.
- 🎯 **Custom Alert Thresholds:** Set custom drop alert thresholds per product (e.g., `10%`).
- 🗑️ **Interactive Management:** Delete tracked items by replying `delete` directly to messages.
- 🔐 **Admin Access Control:** Restrict bot features to authorized users via the `confirm` reply command.
- 📅 **Cron Execution Mode:** Supports both 24/7 background interactive listening and scheduled headless daily checks (`--cron`).

## Setup & Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/your-username/AmazonPriceTracker.git](https://github.com/your-username/AmazonPriceTracker.git)
   cd AmazonPriceTracker


2. **Set up a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium

3. **Configure Environment Variables:**
   Copy .env.example to .env and fill in your values:
   ```bash
   cp .env.example .env

## Usage

* Interactive Listener Mode (24/7 or manual link adding):
   ```bash
  python tracker.py

* Daily Cron Mode (Scheduled daily price check):
   ```bash
  ./run_tracker.sh

## Telegram Commands & Workflow

* Authorize User: Reply confirm to a group member's message as the Admin.
* Track Product: Send any Amazon product link to the group.
* Set Threshold: Reply 10% (or any percentage) to a product message to receive daily alerts only when the price drops by that amount.
* Delete Product: Reply delete to a product message to remove it from tracking.
* 