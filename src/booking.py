"""
Booking client for Active Illini facility reservations.
Uses Playwright browser automation with saved session cookies.
"""

import pickle
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page

from .auth import SessionManager, AuthenticationError

logger = logging.getLogger(__name__)


class BookingClient:
    """Client for booking Active Illini facilities using Playwright automation."""

    BASE_URL = "https://active.illinois.edu"

    # Known facility product IDs and their court/facility IDs
    FACILITIES = {
        "ARC_MP1": {
            "product_id": "b005129c-6510-4b20-8658-3d1570b4c0c2",
            "facility_id": "547b9b68-bf48-4dab-9a64-23deed1a99df"
        },
        "ARC_PICKLEBALL": {
            "product_id": "1c288a93-2323-4d2f-a4fb-61e1f86b5c42",
            "facility_id": None  # Will auto-detect
        },
        "CRCE_MP1": {
            "product_id": "6aea73d7-baac-47b2-9689-f66b04ced0d8",
            "facility_id": None  # Will auto-detect
        }
    }

    def __init__(self, session_file: str = ".session", headless: bool = False):
        """
        Initialize booking client.

        Args:
            session_file: Path to pickled session cookies
            headless: Run browser in headless mode
        """
        self.session_file = Path(session_file)
        self.headless = headless
        self.cookies = self._load_cookies()

    def _load_cookies(self) -> Dict[str, str]:
        """Load cookies from session file."""
        if not self.session_file.exists():
            raise FileNotFoundError(
                f"Session file not found: {self.session_file}. "
                "Run extract_cookies.py first."
            )

        with open(self.session_file, 'rb') as f:
            session_data = pickle.load(f)

        cookies = session_data.get('cookies', {})
        logger.info(f"Loaded {len(cookies)} cookies from {self.session_file}")
        return cookies

    def _inject_cookies(self, page: Page) -> None:
        """Inject saved cookies into browser context."""
        # Navigate to domain first
        page.goto(self.BASE_URL)

        # Convert cookies to Playwright format
        playwright_cookies = []
        for name, value in self.cookies.items():
            playwright_cookies.append({
                'name': name,
                'value': str(value),
                'domain': 'active.illinois.edu',
                'path': '/',
                'httpOnly': False,
                'secure': True
            })

        page.context.add_cookies(playwright_cookies)
        logger.info("Cookies injected")

    def check_available_slots(
        self,
        facility: str,
        date: datetime,
        facility_id: Optional[str] = None
    ) -> List[Dict]:
        """
        Check available time slots for a facility on a given date.

        Args:
            facility: Facility name (e.g., "ARC_MP1")
            date: Date to check
            facility_id: Optional facility ID (court UUID)

        Returns:
            List of available slots with their details
        """
        facility_config = self.FACILITIES.get(facility)
        if not facility_config:
            raise ValueError(f"Unknown facility: {facility}")

        product_id = facility_config["product_id"]
        known_facility_id = facility_config.get("facility_id")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context()
            page = context.new_page()

            try:
                # Inject cookies
                self._inject_cookies(page)

                # Get facility ID if not provided
                if not facility_id:
                    facility_id = known_facility_id or self._get_facility_id(page, product_id)

                # Navigate to slots page
                year, month, day = date.year, date.month, date.day

                slots_url = (
                    f"{self.BASE_URL}/booking/{product_id}/slots/"
                    f"{facility_id}/{year}/{month}/{day}"
                )

                logger.info(f"Checking slots: {slots_url}")
                page.goto(slots_url)
                page.wait_for_load_state('networkidle')

                # Parse available slots
                html = page.content()
                return self._parse_slots(html)

            finally:
                browser.close()

    def _get_facility_id(self, page: Page, product_id: str) -> str:
        """Get facility ID from facilities page."""
        facilities_url = f"{self.BASE_URL}/booking/{product_id}/facilities"
        page.goto(facilities_url)
        page.wait_for_load_state('networkidle')

        html = page.content()
        import re
        match = re.search(r'data-facility-id="([a-f0-9-]+)"', html)
        if not match:
            match = re.search(r'hdnSelectedFacilityId.*?value="([a-f0-9-]+)"', html)

        if not match:
            raise ValueError("Could not find facility ID")

        facility_id = match.group(1)
        logger.info(f"Found facility ID: {facility_id}")
        return facility_id

    def _parse_slots(self, html: str) -> List[Dict]:
        """Parse available slots from HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        slots = []

        # Find all buttons with btn class
        buttons = soup.find_all('button', class_='btn')

        for button in buttons:
            # Skip disabled/unavailable buttons
            classes = button.get('class', [])
            if 'disabled' in classes or not button.get('data-slot-text'):
                continue

            slot = {
                'button_id': button.get('id'),
                'apt_id': button.get('data-apt-id'),
                'timeslot_id': button.get('data-timeslot-id'),
                'slot_number': button.get('data-slot-number'),
                'time_text': button.get('data-slot-text'),
                'spots_available': button.get('data-spots-left-text')
            }
            slots.append(slot)

        logger.info(f"Found {len(slots)} available slots")
        return slots

    def book_slot(
        self,
        facility: str,
        date: datetime,
        slot_time: str,
        facility_id: Optional[str] = None,
        dry_run: bool = False
    ) -> bool:
        """
        Book a specific time slot.

        Args:
            facility: Facility name (e.g., "ARC_MP1")
            date: Date to book
            slot_time: Time slot text (e.g., "11 AM - 12 PM")
            facility_id: Optional facility ID
            dry_run: If True, don't actually click the book button

        Returns:
            True if booking succeeded, False otherwise
        """
        facility_config = self.FACILITIES.get(facility)
        if not facility_config:
            raise ValueError(f"Unknown facility: {facility}")

        product_id = facility_config["product_id"]
        known_facility_id = facility_config.get("facility_id")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, slow_mo=500)
            context = browser.new_context()
            page = context.new_page()

            try:
                # Inject cookies
                self._inject_cookies(page)

                # Get facility ID if not provided
                if not facility_id:
                    facility_id = known_facility_id or self._get_facility_id(page, product_id)

                # Navigate to slots page
                year, month, day = date.year, date.month, date.day

                slots_url = (
                    f"{self.BASE_URL}/booking/{product_id}/slots/"
                    f"{facility_id}/{year}/{month}/{day}"
                )

                logger.info(f"Navigating to: {slots_url}")
                page.goto(slots_url)
                page.wait_for_load_state('networkidle')

                # Find the slot button
                slot_button = page.locator(
                    f'button[data-slot-text="{slot_time}"]'
                ).filter(has_not_text='Unavailable')

                if slot_button.count() == 0:
                    logger.error(f"Slot not found or unavailable: {slot_time}")
                    return False

                logger.info(f"Found slot button for: {slot_time}")

                if dry_run:
                    logger.info("DRY RUN - Would click booking button")
                    page.screenshot(path='booking_dry_run.png')
                    return True

                # Click the book button and wait for navigation
                logger.info("Clicking Book Now button...")

                # Click and wait for navigation to complete
                with page.expect_navigation(timeout=10000):
                    slot_button.click()

                logger.info("Navigation completed after click")

                # Wait a bit more for page to fully load
                page.wait_for_load_state('networkidle', timeout=10000)

                # Take screenshot
                page.screenshot(path='booking_confirmation.png')

                # Check current URL
                current_url = page.url
                logger.info(f"Current URL after click: {current_url}")

                # Check if we're on booking/mybookings or just /booking
                if '/mybookings' in current_url:
                    logger.info("✅ Redirected to My Bookings - booking successful!")
                    return True
                elif current_url == page.url and 'slots' not in current_url:
                    # Navigated away from slots page
                    logger.info("✅ Navigated away from slots page - likely successful")
                    Path('booking_result.html').write_text(page.content())
                    return True
                else:
                    logger.warning("⚠️  Still on slots page or unexpected location")
                    logger.warning(f"Expected redirect to /booking or /mybookings, got: {current_url}")
                    Path('booking_unexpected.html').write_text(page.content())
                    return False

            except Exception as e:
                logger.error(f"Booking failed: {e}")
                page.screenshot(path='booking_error.png')
                raise
            finally:
                page.wait_for_timeout(2000)
                browser.close()


# Legacy function for backwards compatibility
def book_facility(sport: str, date: str, time: str, config: Dict[str, Any]) -> bool:
    """Legacy function - use BookingClient instead."""
    logger.warning("book_facility() is deprecated, use BookingClient")
    return False
