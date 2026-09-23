#!/bin/bash

# Navigate to project directory
cd /home/jasonche/Documents/Git-Repository/AmazonPriceTracker || exit

# Activate virtual environment
source .venv/bin/activate

# Add random delay between 0 and 1800 seconds (0 to 30 mins)
python3 -c "import time, random; time.sleep(random.randint(0, 1800))"

# Run single-shot cron job under Xvfb and exit automatically
xvfb-run -a python tracker.py --cron