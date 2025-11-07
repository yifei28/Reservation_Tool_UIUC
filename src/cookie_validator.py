"""Cookie validation module for Active Illinois booking system."""

import logging
import pickle
import time
from datetime import datetime
from typing import Dict, Optional
from requests.exceptions import HTTPError

logger = logging.getLogger(__name__)


class CookieValidator:
    """Validates session cookies by testing API access."""

    def validate_cookies(self, client, session_file: str = '.session') -> Dict:
        """
        Validate cookies by attempting to fetch facility IDs.

        This uses the same API call that real bookings use, so it's
        the most accurate test of cookie validity.

        Args:
            client: FastBookingClient instance with cookies loaded
            session_file: Path to session file (for age calculation)

        Returns:
            dict: {
                'valid': bool,              # True if cookies work
                'error': str | None,        # Error message if invalid
                'cookie_age_hours': float,  # How old the cookies are
                'last_checked': str         # ISO timestamp
            }
        """
        result = {
            'valid': False,
            'error': None,
            'cookie_age_hours': 0.0,
            'last_checked': datetime.now().isoformat()
        }

        # Calculate cookie age
        try:
            with open(session_file, 'rb') as f:
                session_data = pickle.load(f)
                auth_time = session_data.get('auth_time')

                if auth_time:
                    # auth_time is a timestamp (float)
                    age_seconds = time.time() - auth_time
                    result['cookie_age_hours'] = round(age_seconds / 3600, 1)

        except FileNotFoundError:
            result['error'] = 'No session file found - please run extract_cookies.py'
            logger.error("Session file not found")
            return result
        except Exception as e:
            logger.warning(f"Could not read session file age: {e}")
            # Continue with validation anyway

        # Validate by trying to fetch facility IDs
        try:
            # Use ARC_MP1 as test facility (always exists)
            test_facility = 'ARC_MP1'
            if test_facility not in client.FACILITIES:
                result['error'] = 'Test facility not found in configuration'
                return result

            product_id = client.FACILITIES[test_facility]['product_id']

            # This will fail if cookies are expired
            facility_ids = client._get_all_facility_ids(product_id)

            # Success!
            result['valid'] = True
            result['error'] = None
            logger.info(f"✅ Cookies valid - found {len(facility_ids)} facilities")

        except HTTPError as e:
            # HTTP 401/403 = definitely expired
            if e.response.status_code in [401, 403]:
                result['error'] = f'HTTP {e.response.status_code}: Session expired'
                logger.error(f"Cookies expired: {result['error']}")
            else:
                result['error'] = f'HTTP {e.response.status_code}: {str(e)}'
                logger.error(f"HTTP error during validation: {result['error']}")

        except ValueError as e:
            # "Could not find any facility IDs" = probably seeing login page
            if 'Could not find any facility IDs' in str(e):
                result['error'] = 'Cannot access facility data - cookies likely expired'
                logger.error("Cookies likely expired (no facility IDs found)")
            else:
                result['error'] = f'Validation error: {str(e)}'
                logger.error(f"ValueError during validation: {result['error']}")

        except Exception as e:
            result['error'] = f'Unexpected error: {str(e)}'
            logger.error(f"Unexpected error during validation: {result['error']}")

        return result
