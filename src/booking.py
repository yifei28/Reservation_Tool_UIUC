"""Booking functionality for Active Illini facilities."""

import logging
from typing import Dict, Any

from .auth import SessionManager, AuthenticationError

logger = logging.getLogger(__name__)


def book_facility(sport: str, date: str, time: str, config: Dict[str, Any]) -> bool:
    """
    Book a facility at Active Illini.

    Args:
        sport: Type of sport/facility (e.g., 'badminton', 'basketball')
        date: Date in YYYY-MM-DD format
        time: Time in HH:MM format (24-hour)
        config: Configuration dictionary with credentials

    Returns:
        True if booking successful, False otherwise
    """
    logger.info(f"Attempting to book {sport} on {date} at {time}")

    # Get credentials from config
    username = config.get('netid')
    password = config.get('password')

    if not username or not password:
        logger.error("Missing UIUC credentials in configuration")
        print("Error: UIUC_NETID and UIUC_PASSWORD must be set in .env file")
        return False

    try:
        # Initialize session manager
        session_mgr = SessionManager()

        # Ensure we have valid authentication
        session_mgr.ensure_authenticated(username, password)
        logger.info("Authentication successful")

        # TODO: Implement actual booking API calls using session_mgr.session
        # For now, just verify we're authenticated
        logger.warning("Booking API integration not yet implemented!")

        print(f"""
    Authentication successful!

    Booking Request (pending API integration):
    - Sport: {sport}
    - Date: {date}
    - Time: {time}
    - NetID: {username}

    [Booking API integration will be implemented in Task 3]
    """)

        return False

    except AuthenticationError as e:
        logger.error(f"Authentication failed: {e}")
        print(f"Authentication error: {e}")
        return False
    except Exception as e:
        logger.error(f"Booking failed: {e}")
        print(f"Error: {e}")
        return False
