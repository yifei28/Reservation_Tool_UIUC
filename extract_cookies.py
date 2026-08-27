#!/usr/bin/env python3
"""
Extract cookies from browser after manual login.

Instructions:
1. Run this script
2. When the browser opens, manually log in to Active Illini
3. Navigate to the booking page
4. The script will extract your cookies and save them
"""

import sys
import argparse
import os
from playwright.sync_api import sync_playwright
from src.account_pool import AccountPool

def extract_cookies(label: str, account_id: str = None):
    """Launch browser, wait for manual login, then extract cookies."""

    print("=" * 60)
    print("Cookie Extraction Tool for Active Illini")
    print("=" * 60)
    print()
    print("Instructions:")
    print("1. A browser window will open")
    print("2. Log in to Active Illini manually")
    print("3. Navigate to the booking page (https://active.illinois.edu/booking)")
    print("4. Press ENTER in this terminal when you're logged in")
    print()

    with sync_playwright() as p:
        # Launch browser (not headless so user can see it)
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        # Navigate to booking page
        print("Opening Active Illini booking page...")
        page.goto('https://active.illinois.edu/booking')

        print()
        print("=" * 60)
        print("Please log in manually in the browser window")
        print("Then press ENTER here to continue...")
        print("=" * 60)

        # Wait for user to press enter
        input()

        # Check current URL
        current_url = page.url
        print(f"\nCurrent URL: {current_url}")

        # Extract cookies
        cookies = context.cookies()

        print(f"\n✓ Extracted {len(cookies)} cookies")

        cookie_dict = {cookie['name']: cookie['value'] for cookie in cookies}
        pool = AccountPool(
            accounts_dir=os.getenv('ACCOUNTS_DIR', '.accounts'),
            legacy_session_file=os.getenv('SESSION_FILE', '.session')
        )
        stored_id = pool.store_cookies(label, cookie_dict, account_id=account_id)

        print(f"✓ Saved account '{label}' ({stored_id})")

        print()
        print("=" * 60)
        print("SUCCESS: Session cookies extracted and saved!")
        print("=" * 60)
        print()
        print("You can now use these cookies for automated booking.")
        print("The session will be valid for approximately 5-20 minutes.")

        browser.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Add or refresh an Active Illini account')
    parser.add_argument('--label', help='Friendly account label')
    parser.add_argument('--account-id', help='Existing account ID to refresh')
    args = parser.parse_args()
    label = args.label or input('Account label: ').strip()
    if not label:
        label = 'Account'
    try:
        extract_cookies(label, args.account_id)
    except KeyboardInterrupt:
        print("\n\nCancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
