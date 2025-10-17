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
import pickle
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

def extract_cookies():
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

        # Convert to requests-compatible format and save
        session_data = {
            'cookies': {},
            'authenticated': True,
            'auth_time': time.time()
        }

        for cookie in cookies:
            session_data['cookies'][cookie['name']] = cookie['value']

        # Save to .session file
        session_file = Path('.session')
        with open(session_file, 'wb') as f:
            pickle.dump(session_data, f)

        session_file.chmod(0o600)  # Secure permissions

        print(f"✓ Saved session to {session_file}")
        print()
        print("Cookie details:")
        for name, value in session_data['cookies'].items():
            print(f"  - {name}: {value[:30]}..." if len(value) > 30 else f"  - {name}: {value}")

        print()
        print("=" * 60)
        print("SUCCESS: Session cookies extracted and saved!")
        print("=" * 60)
        print()
        print("You can now use these cookies for automated booking.")
        print("The session will be valid for approximately 8 hours.")

        browser.close()

if __name__ == '__main__':
    try:
        extract_cookies()
    except KeyboardInterrupt:
        print("\n\nCancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
