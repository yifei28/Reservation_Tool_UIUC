"""
Fast HTTP-based booking client for Active Illini facilities.
Uses direct POST requests instead of browser automation.
"""

import pickle
import logging
import requests
import random
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class FastBookingClient:
    """Fast HTTP-based booking client using direct API calls."""

    BASE_URL = "https://active.illinois.edu"
    RESERVE_URL = f"{BASE_URL}/booking/reserve"

    # Known facility product IDs and their court/facility IDs
    FACILITIES = {
        "ARC_GYM_2_VOLLEYBALL_COURTS": {
            "product_id": "ae779f17-f3a2-4758-be2a-9670cf64fcdf",
            "facility_id": None  # Will auto-detect
        },
        "ARC_MP1": {
            "product_id": "b005129c-6510-4b20-8658-3d1570b4c0c2",
            "facility_id": "547b9b68-bf48-4dab-9a64-23deed1a99df"
        },
        "ARC_MP2": {
            "product_id": "6aea73d7-baac-47b2-9689-f66b04ced0d8",
            "facility_id": None  # Will auto-detect
        },
        "ARC_MP3_TABLE_TENNIS_ONLY": {
            "product_id": "49f02e87-c344-4087-a691-ac0f2f6a73da",
            "facility_id": None  # Will auto-detect
        },
        "ARC_MP4": {
            "product_id": "9ca0d0d2-28b3-429b-91bb-2a45c0dbd0d6",
            "facility_id": None  # Will auto-detect
        },
        "ARC_MP5": {
            "product_id": "075efde4-a683-4db2-9e3c-a27e0ad387da",
            "facility_id": None  # Will auto-detect
        },
        "ARC_PICKLEBALL_BADMINTON": {
            "product_id": "1c288a93-2323-4d2f-a4fb-61e1f86b5c42",
            "facility_id": None  # Will auto-detect
        },
        "ARC_RACQUETBALL_TABLE_TENNIS": {
            "product_id": "87656121-9423-4007-bff5-25a69e8d74db",
            "facility_id": None  # Will auto-detect
        },
        "ARC_REFLECTION_RECOVERY_ROOM": {
            "product_id": "4a16f0b3-6859-470b-a750-9d705cc6bf32",
            "facility_id": None  # Will auto-detect
        },
        "ARC_SQUASH_COURTS": {
            "product_id": "f874ef0c-d088-4e1b-84d6-e7c1f0d1940c",
            "facility_id": None  # Will auto-detect
        },
        "CRCE_MP1": {
            "product_id": "d56445b6-20fb-49bc-bf60-d57189aceb78",
            "facility_id": None  # Will auto-detect
        },
        "CRCE_MP2": {
            "product_id": "966316d6-bffc-42f0-b2c6-a6cad53f9c42",
            "facility_id": None  # Will auto-detect
        },
        "CRCE_RACQUETBALL": {
            "product_id": "56a2c9df-63c7-421b-9fcc-f5305e80d961",
            "facility_id": None  # Will auto-detect
        },
        "CRCE_SQUASH_RB_MP_COURT": {
            "product_id": "caf86dbf-3395-435b-a646-6ae8de13675f",
            "facility_id": None  # Will auto-detect
        },
        "ICE_ARENA_FREESTYLE_SKATING": {
            "product_id": "d2353cb4-0992-4074-85a7-b9e2645a945f",
            "facility_id": None  # Will auto-detect
        }
    }

    def __init__(self, session_file: str = ".session"):
        """
        Initialize HTTP booking client.

        Args:
            session_file: Path to pickled session cookies
        """
        self.session_file = Path(session_file)
        self.session = requests.Session()
        self._load_cookies()

        # Performance optimization: Configure connection pooling
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0  # We handle retries manually
        )
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

    def _load_cookies(self):
        """Load cookies from session file into requests session."""
        if not self.session_file.exists():
            raise FileNotFoundError(
                f"Session file not found: {self.session_file}. "
                "Run extract_cookies.py first."
            )

        with open(self.session_file, 'rb') as f:
            session_data = pickle.load(f)

        cookies = session_data.get('cookies', {})

        # Add cookies to session
        for name, value in cookies.items():
            self.session.cookies.set(name, str(value), domain='active.illinois.edu')

        logger.info(f"Loaded {len(cookies)} cookies from {self.session_file}")

    def _get_csrf_token(self) -> Optional[str]:
        """Extract CSRF token from cookies."""
        token = self.session.cookies.get('__RequestVerificationToken')
        if token:
            logger.debug(f"Found CSRF token: {token[:20]}...")
            return token
        logger.warning("No CSRF token found in cookies")
        return None

    def warm_connection(self):
        """
        Pre-establish HTTP connection to reduce booking latency.
        Should be called 5-10 seconds before booking execution.
        """
        import socket
        try:
            # Pre-resolve DNS
            socket.getaddrinfo('active.illinois.edu', 443, socket.AF_INET, socket.SOCK_STREAM)

            # Warm connection pool with lightweight HEAD request
            self.session.head(self.BASE_URL, timeout=3)
            logger.debug("Connection warmed successfully")
        except Exception as e:
            logger.debug(f"Connection warm failed (non-critical): {e}")

    def prepare_booking(
        self,
        facility: str,
        date: datetime,
        facility_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Pre-fetch and cache data needed for booking to reduce execution latency.
        Should be called 5-10 seconds before booking execution.

        For multi-court facilities, returns None to trigger full multi-court logic.
        For single-court facilities, returns the cached facility_id.

        Args:
            facility: Facility name
            date: Booking date
            facility_id: Optional facility ID

        Returns:
            facility_id for single-court facilities, None for multi-court facilities
        """
        facility_config = self.FACILITIES.get(facility)
        if not facility_config:
            raise ValueError(f"Unknown facility: {facility}")

        product_id = facility_config["product_id"]
        known_facility_id = facility_config.get("facility_id")

        # Warm connection
        self.warm_connection()

        # If specific facility_id provided, return it
        if facility_id:
            return facility_id

        # Pre-fetch all facility IDs to determine if multi-court
        try:
            logger.info("Pre-fetching facility IDs...")
            all_facility_ids = self._get_all_facility_ids(product_id)

            # Single court facility - cache and return it
            if len(all_facility_ids) == 1:
                facility_config["facility_id"] = all_facility_ids[0]
                logger.info(f"Single-court facility - cached ID: {all_facility_ids[0][:8]}")
                return all_facility_ids[0]

            # Multi-court facility - return None to trigger full multi-court logic
            logger.info(f"Multi-court facility ({len(all_facility_ids)} courts) - will use random selection")
            return None

        except Exception as e:
            logger.warning(f"Failed to pre-fetch facility IDs: {e}")
            # Fallback to old behavior
            if known_facility_id:
                return known_facility_id
            facility_id = self._get_facility_id(product_id)
            facility_config["facility_id"] = facility_id
            return facility_id

    def check_available_slots(
        self,
        facility: str,
        date: datetime,
        facility_id: Optional[str] = None
    ) -> List[Dict]:
        """
        Check available time slots for a facility on a given date.
        For multi-court facilities, returns slots aggregated from all courts.

        Args:
            facility: Facility name (e.g., "ARC_MP1")
            date: Date to check
            facility_id: Optional facility ID (if provided, checks only that court)

        Returns:
            List of available slots with their details
        """
        facility_config = self.FACILITIES.get(facility)
        if not facility_config:
            raise ValueError(f"Unknown facility: {facility}")

        product_id = facility_config["product_id"]
        known_facility_id = facility_config.get("facility_id")

        # If specific facility_id provided, check only that court
        if facility_id:
            logger.info(f"Checking slots for {facility} (specific court) on {date.strftime('%Y-%m-%d')}")
            return self._fetch_slots_for_court(product_id, facility_id, date)

        # Get all courts for this facility
        try:
            all_facility_ids = self._get_all_facility_ids(product_id)
        except Exception as e:
            logger.warning(f"Failed to get all facility IDs, falling back to single court: {e}")
            facility_id = known_facility_id or self._get_facility_id(product_id)
            return self._fetch_slots_for_court(product_id, facility_id, date)

        # Single court - return its slots directly
        if len(all_facility_ids) == 1:
            logger.info(f"Checking slots for {facility} (single court) on {date.strftime('%Y-%m-%d')}")
            return self._fetch_slots_for_court(product_id, all_facility_ids[0], date)

        # Multi-court facility - aggregate slots from all courts
        logger.info(f"Checking slots for {facility} ({len(all_facility_ids)} courts) on {date.strftime('%Y-%m-%d')}")

        # Collect all slots from all courts
        all_slots_by_time = {}  # time_text -> list of slots from different courts

        for court_id in all_facility_ids:
            try:
                court_slots = self._fetch_slots_for_court(product_id, court_id, date)

                for slot in court_slots:
                    time_text = slot['time_text']
                    if time_text not in all_slots_by_time:
                        all_slots_by_time[time_text] = []

                    # Add court info to slot
                    slot['court_id'] = court_id
                    all_slots_by_time[time_text].append(slot)

            except Exception as e:
                logger.debug(f"Failed to fetch slots for court {court_id[:8]}: {e}")
                continue

        # Convert to aggregated slot list
        aggregated_slots = []
        for time_text, court_slots in sorted(all_slots_by_time.items()):
            # Use first slot as template, but add court count info
            if court_slots:
                aggregated_slot = court_slots[0].copy()
                aggregated_slot['courts_available'] = len(court_slots)
                aggregated_slot['total_courts'] = len(all_facility_ids)
                aggregated_slot['spots_available'] = f"{len(court_slots)} of {len(all_facility_ids)} courts"
                aggregated_slots.append(aggregated_slot)

        logger.info(f"Found {len(aggregated_slots)} time slots across {len(all_facility_ids)} courts")
        return aggregated_slots

    def _get_facility_id(self, product_id: str) -> str:
        """Get facility ID from facilities page (returns first match)."""
        facilities_url = f"{self.BASE_URL}/booking/{product_id}/facilities"
        response = self.session.get(facilities_url)
        response.raise_for_status()

        html = response.text
        import re
        match = re.search(r'data-facility-id="([a-f0-9-]+)"', html)
        if not match:
            match = re.search(r'hdnSelectedFacilityId.*?value="([a-f0-9-]+)"', html)

        if not match:
            raise ValueError("Could not find facility ID")

        facility_id = match.group(1)
        logger.info(f"Found facility ID: {facility_id}")
        return facility_id

    def _get_all_facility_ids(self, product_id: str) -> List[str]:
        """
        Get all facility IDs for a product (e.g., all 8 pickleball courts).

        Args:
            product_id: Product UUID

        Returns:
            List of facility IDs (court UUIDs)
        """
        facilities_url = f"{self.BASE_URL}/booking/{product_id}/facilities"
        response = self.session.get(facilities_url)
        response.raise_for_status()

        html = response.text
        import re

        # Find ALL facility IDs (not just first one)
        matches = re.findall(r'data-facility-id="([a-f0-9-]+)"', html)

        if not matches:
            # Fallback: try hidden input field pattern
            matches = re.findall(r'hdnSelectedFacilityId.*?value="([a-f0-9-]+)"', html)

        if not matches:
            raise ValueError("Could not find any facility IDs")

        # Remove duplicates while preserving order
        facility_ids = list(dict.fromkeys(matches))

        logger.info(f"Found {len(facility_ids)} facility/court IDs for product {product_id}")
        return facility_ids

    def _select_initial_court(
        self,
        all_facility_ids: List[str],
        known_facility_id: Optional[str] = None,
        strategy: str = "random"
    ) -> str:
        """
        Select which court to try first.

        Args:
            all_facility_ids: List of all available court IDs
            known_facility_id: Previously cached court ID (optional)
            strategy: Selection strategy - "random", "first", or "cached"

        Returns:
            Facility ID to try first
        """
        if strategy == "cached" and known_facility_id and known_facility_id in all_facility_ids:
            logger.debug(f"Using cached court: {known_facility_id}")
            return known_facility_id

        elif strategy == "random":
            # Randomly select a court to reduce contention
            selected = random.choice(all_facility_ids)
            logger.debug(f"Randomly selected court: {selected} (out of {len(all_facility_ids)} courts)")
            return selected

        elif strategy == "first":
            # Use first court (original behavior)
            logger.debug(f"Using first court: {all_facility_ids[0]}")
            return all_facility_ids[0]

        else:
            raise ValueError(f"Unknown strategy: {strategy}")

    def _fetch_slots_for_court(
        self,
        product_id: str,
        facility_id: str,
        date: datetime
    ) -> List[Dict]:
        """
        Fetch available slots for a specific court.

        Args:
            product_id: Product UUID
            facility_id: Facility/court UUID
            date: Date to check

        Returns:
            List of available slots with their details
        """
        year, month, day = date.year, date.month, date.day
        slots_url = (
            f"{self.BASE_URL}/booking/{product_id}/slots/"
            f"{facility_id}/{year}/{month}/{day}"
        )

        logger.debug(f"Fetching slots for court {facility_id[:8]}...")
        response = self.session.get(slots_url)
        response.raise_for_status()

        return self._parse_slots(response.text)

    def _submit_booking(
        self,
        product_id: str,
        facility_id: str,
        date: datetime,
        target_slot: Dict
    ) -> bool:
        """
        Submit a booking request for a specific slot.

        Args:
            product_id: Product UUID
            facility_id: Facility/court UUID
            date: Booking date
            target_slot: Slot data dictionary

        Returns:
            True if booking succeeded, False otherwise
        """
        # Build the POST request payload (matching the form data from browser)
        payload = {
            'bId': product_id,  # Booking/Product ID
            'fId': facility_id,  # Facility ID
            'aId': target_slot['apt_id'],  # Appointment ID
            'tsId': target_slot['timeslot_id'],  # Timeslot ID
            'tsiId': target_slot['timeslot_instance_id'],  # Timeslot Instance ID
            'y': date.year,
            'm': date.month,
            'd': date.day,
            't': '',  # Time component (empty)
            'v': '0'  # Version
        }

        # Set required headers
        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Accept': '*/*',
            'Origin': self.BASE_URL,
            'Referer': f"{self.BASE_URL}/booking/{product_id}/slots/{facility_id}/{date.year}/{date.month}/{date.day}"
        }

        logger.debug(f"Submitting booking for court {facility_id[:8]}...")

        try:
            response = self.session.post(
                self.RESERVE_URL,
                data=payload,
                headers=headers,
                timeout=10
            )

            logger.debug(f"Response status: {response.status_code}")

            if response.status_code == 200:
                try:
                    result = response.json()

                    if result.get('Success'):
                        participant_id = result.get('ParticipantId')
                        logger.info(f"✅ Booking successful on court {facility_id[:8]}! Participant ID: {participant_id}")
                        return True
                    else:
                        error_code = result.get('ErrorCode', 'Unknown')
                        logger.debug(f"Booking failed on court {facility_id[:8]} with error code: {error_code}")
                        return False

                except Exception as e:
                    logger.error(f"Failed to parse response JSON: {e}")
                    logger.error(f"Raw response: {response.text}")
                    return False
            else:
                logger.debug(f"HTTP error {response.status_code} on court {facility_id[:8]}")
                return False

        except Exception as e:
            logger.debug(f"Booking request failed on court {facility_id[:8]}: {e}")
            return False

    def _attempt_booking_on_court(
        self,
        product_id: str,
        facility_id: str,
        date: datetime,
        slot_time: str,
        dry_run: bool = False
    ) -> Optional[str]:
        """
        Attempt to book a slot on a specific court.

        Args:
            product_id: Product UUID
            facility_id: Facility/court UUID
            date: Booking date
            slot_time: Time slot text (e.g., "11 AM - 12 PM")
            dry_run: If True, don't actually submit the booking

        Returns:
            Facility ID if successful, None if failed
        """
        logger.debug(f"Trying court {facility_id[:8]}...")

        # Fetch slots for this court
        try:
            slots = self._fetch_slots_for_court(product_id, facility_id, date)
        except Exception as e:
            logger.debug(f"Failed to fetch slots for court {facility_id[:8]}: {e}")
            return None

        # Find matching slot
        target_slot = None
        for slot in slots:
            if slot['time_text'] == slot_time:
                target_slot = slot
                break

        if not target_slot:
            logger.warning(f"Slot '{slot_time}' not found on court {facility_id[:8]}. Available slots: {[s['time_text'] for s in slots]}")
            return None

        logger.info(f"Found target slot '{slot_time}' on court {facility_id[:8]}")

        if dry_run:
            logger.info(f"DRY RUN - Would book on court {facility_id[:8]}")
            return facility_id

        # Submit booking
        if self._submit_booking(product_id, facility_id, date, target_slot):
            return facility_id
        else:
            return None

    def _parse_slots(self, html: str) -> List[Dict]:
        """Parse available slots from HTML."""
        soup = BeautifulSoup(html, 'html.parser')
        slots = []

        # Find all buttons with btn class
        buttons = soup.find_all('button', class_='btn')

        for button in buttons:
            # Skip disabled/unavailable buttons
            classes = button.get('class', [])
            # Check both class attribute and disabled attribute
            if 'disabled' in classes or button.has_attr('disabled'):
                continue

            # Skip if no slot data
            if not button.get('data-slot-text'):
                continue

            slot = {
                'button_id': button.get('id'),
                'apt_id': button.get('data-apt-id'),
                'timeslot_id': button.get('data-timeslot-id'),
                'timeslot_instance_id': button.get('data-timeslotinstance-id', '00000000-0000-0000-0000-000000000000'),
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
        dry_run: bool = False,
        court_selection: str = "random"
    ) -> bool:
        """
        Book a specific time slot using direct HTTP POST with multi-court support.

        Args:
            facility: Facility name (e.g., "ARC_MP1", "ARC_PICKLEBALL_BADMINTON")
            date: Date to book
            slot_time: Time slot text (e.g., "11 AM - 12 PM")
            facility_id: Optional facility ID (if None, will try all courts)
            dry_run: If True, don't actually submit the booking
            court_selection: Court selection strategy - "random", "first", or "cached"

        Returns:
            True if booking succeeded, False otherwise
        """
        facility_config = self.FACILITIES.get(facility)
        if not facility_config:
            raise ValueError(f"Unknown facility: {facility}")

        product_id = facility_config["product_id"]
        known_facility_id = facility_config.get("facility_id")

        # If specific facility_id provided, use single-court logic (fast path)
        if facility_id:
            logger.info(f"Booking on specific court {facility_id[:8]}...")
            result = self._attempt_booking_on_court(
                product_id, facility_id, date, slot_time, dry_run
            )
            if result:
                # Cache successful court for future bookings
                facility_config["facility_id"] = result
                return True
            return False

        # Multi-court logic: Get all available courts
        logger.info(f"Checking all available courts for {facility}...")

        try:
            all_facility_ids = self._get_all_facility_ids(product_id)
        except Exception as e:
            logger.error(f"Failed to get facility IDs: {e}")
            return False

        # If only one court, use it directly (fast path)
        if len(all_facility_ids) == 1:
            logger.info(f"Single court facility detected: {all_facility_ids[0][:8]}")
            result = self._attempt_booking_on_court(
                product_id, all_facility_ids[0], date, slot_time, dry_run
            )
            if result:
                facility_config["facility_id"] = result
                return True
            return False

        # Multiple courts: Use smart selection strategy
        logger.info(f"Found {len(all_facility_ids)} courts, using '{court_selection}' strategy")

        # Select initial court to try
        initial_court = self._select_initial_court(
            all_facility_ids,
            known_facility_id,
            strategy=court_selection
        )

        # Try initial court first (fast path)
        result = self._attempt_booking_on_court(
            product_id, initial_court, date, slot_time, dry_run
        )

        if result:
            # Cache successful court for future bookings
            facility_config["facility_id"] = result
            logger.info(f"✅ Booked successfully on initial court!")
            return True

        # Fast path failed - try remaining courts (fallback)
        logger.info(f"Initial court unavailable, trying remaining {len(all_facility_ids) - 1} courts...")

        for court_id in all_facility_ids:
            # Skip the court we already tried
            if court_id == initial_court:
                continue

            result = self._attempt_booking_on_court(
                product_id, court_id, date, slot_time, dry_run
            )

            if result:
                # Cache successful court for future bookings
                facility_config["facility_id"] = result
                logger.info(f"✅ Booked successfully on alternate court!")
                return True

        # All courts failed
        logger.error(f"❌ Slot '{slot_time}' unavailable on all {len(all_facility_ids)} courts")
        return False


# Legacy function for backwards compatibility
def book_facility(sport: str, date: str, time: str, config: Dict[str, Any]) -> bool:
    """Legacy function - use FastBookingClient instead."""
    logger.warning("book_facility() is deprecated, use FastBookingClient")
    return False
