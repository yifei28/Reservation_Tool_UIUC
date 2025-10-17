"""Authentication module for UIUC Active Illini system."""

import logging
import pickle
import time
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when authentication fails."""
    pass


class SessionManager:
    """Manages authenticated sessions with UIUC Active Illini."""

    def __init__(self, session_file: str = '.session'):
        """
        Initialize session manager.

        Args:
            session_file: Path to session persistence file
        """
        self.session_file = Path(session_file)
        self.session = requests.Session()
        self.authenticated = False
        self.auth_time = None

        # Set realistic browser headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        })

        # UIUC URLs
        self.base_url = "https://campusrec.illinois.edu"
        self.login_url = "https://login.illinois.edu"

    def login(self, username: str, password: str, max_retries: int = 3) -> bool:
        """
        Authenticate with UIUC Active Illini using Shibboleth SSO.

        Args:
            username: UIUC NetID
            password: UIUC password
            max_retries: Maximum number of retry attempts

        Returns:
            True if authentication successful, False otherwise

        Raises:
            AuthenticationError: If authentication fails after retries
        """
        for attempt in range(max_retries):
            try:
                logger.info(f"Authentication attempt {attempt + 1}/{max_retries}")

                # Step 1: Initial request to trigger SSO redirect
                logger.debug("Initiating SSO authentication flow")
                response = self.session.get(
                    f"{self.base_url}/booking",
                    allow_redirects=False,
                    timeout=10
                )

                # Step 2: Follow redirects to login page
                redirect_count = 0
                while response.status_code in [301, 302, 303, 307] and redirect_count < 10:
                    redirect_url = response.headers.get('Location')

                    # Handle relative redirects
                    if not redirect_url.startswith('http'):
                        parsed = urlparse(response.url)
                        redirect_url = f"{parsed.scheme}://{parsed.netloc}{redirect_url}"

                    logger.debug(f"Following redirect to: {redirect_url}")
                    response = self.session.get(redirect_url, allow_redirects=False, timeout=10)
                    redirect_count += 1

                    # Check if we've reached the login form
                    if 'login.illinois.edu' in response.url or 'shibboleth.illinois.edu' in response.url:
                        break

                # Step 3: Parse login form
                logger.debug("Parsing login form")
                soup = BeautifulSoup(response.text, 'html.parser')
                form = soup.find('form', {'id': 'fm1'}) or soup.find('form', {'name': 'loginForm'})

                if not form:
                    logger.error("Login form not found in response")
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    raise AuthenticationError("Login form not found")

                # Extract form action URL
                action_url = form.get('action')
                if not action_url.startswith('http'):
                    parsed = urlparse(response.url)
                    action_url = f"{parsed.scheme}://{parsed.netloc}{action_url}"

                # Build form data with credentials
                form_data = {
                    'j_username': username,
                    'j_password': password,
                    '_eventId_proceed': ''
                }

                # Add all hidden fields from the form
                for hidden in form.find_all('input', {'type': 'hidden'}):
                    name = hidden.get('name')
                    value = hidden.get('value', '')
                    if name and name not in form_data:
                        form_data[name] = value

                logger.debug(f"Form fields: {list(form_data.keys())}")

                # Step 4: Submit credentials
                logger.info("Submitting credentials")
                response = self.session.post(
                    action_url,
                    data=form_data,
                    allow_redirects=False,
                    timeout=10
                )

                # Step 5: Follow post-authentication redirects
                redirect_count = 0
                while response.status_code in [301, 302, 303, 307] and redirect_count < 10:
                    redirect_url = response.headers.get('Location')
                    if not redirect_url.startswith('http'):
                        parsed = urlparse(response.url)
                        redirect_url = f"{parsed.scheme}://{parsed.netloc}{redirect_url}"

                    logger.debug(f"Following post-auth redirect to: {redirect_url}")
                    response = self.session.get(redirect_url, allow_redirects=False, timeout=10)
                    redirect_count += 1

                # Step 6: Verify authentication success
                logger.debug(f"Final URL after authentication: {response.url}")

                if self.base_url in response.url or 'campusrec' in response.url:
                    self.authenticated = True
                    self.auth_time = time.time()
                    logger.info("Authentication successful!")
                    self.save_session()
                    return True
                else:
                    # Check if we got an error message
                    if 'error' in response.text.lower() or 'invalid' in response.text.lower():
                        logger.error("Authentication failed - invalid credentials")
                        raise AuthenticationError("Invalid username or password")

                    logger.warning(f"Unexpected redirect after login: {response.url}")
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                        continue

            except requests.exceptions.RequestException as e:
                logger.error(f"Network error during authentication: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise AuthenticationError(f"Network error: {e}")

            except Exception as e:
                logger.error(f"Unexpected error during authentication: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise AuthenticationError(f"Authentication failed: {e}")

        raise AuthenticationError("Authentication failed after maximum retries")

    def logout(self) -> None:
        """Clear session and remove persisted session file."""
        logger.info("Logging out and clearing session")
        self.session.cookies.clear()
        self.authenticated = False
        self.auth_time = None

        if self.session_file.exists():
            self.session_file.unlink()
            logger.debug("Session file removed")

    def save_session(self) -> None:
        """Persist session to file for reuse."""
        try:
            session_data = {
                'cookies': self.session.cookies.get_dict(),
                'headers': dict(self.session.headers),
                'auth_time': self.auth_time,
                'authenticated': self.authenticated
            }

            with open(self.session_file, 'wb') as f:
                pickle.dump(session_data, f)

            # Set restrictive file permissions (owner read/write only)
            self.session_file.chmod(0o600)
            logger.debug(f"Session saved to {self.session_file}")

        except Exception as e:
            logger.warning(f"Failed to save session: {e}")

    def load_session(self, max_age_hours: int = 8) -> bool:
        """
        Load persisted session from file.

        Args:
            max_age_hours: Maximum age of session in hours (default: 8)

        Returns:
            True if session loaded and still valid, False otherwise
        """
        if not self.session_file.exists():
            logger.debug("No saved session file found")
            return False

        try:
            with open(self.session_file, 'rb') as f:
                session_data = pickle.load(f)

            # Check session age
            if session_data.get('auth_time'):
                age_seconds = time.time() - session_data['auth_time']
                age_hours = age_seconds / 3600

                if age_hours > max_age_hours:
                    logger.info(f"Session expired ({age_hours:.1f} hours old)")
                    self.session_file.unlink()
                    return False

            # Restore session
            for name, value in session_data.get('cookies', {}).items():
                self.session.cookies.set(name, value)

            self.authenticated = session_data.get('authenticated', False)
            self.auth_time = session_data.get('auth_time')

            # Verify session is still valid
            if self.is_session_valid():
                logger.info("Session loaded successfully")
                return True
            else:
                logger.info("Loaded session is no longer valid")
                self.logout()
                return False

        except Exception as e:
            logger.warning(f"Failed to load session: {e}")
            if self.session_file.exists():
                self.session_file.unlink()
            return False

    def is_session_valid(self) -> bool:
        """
        Check if current session is still valid.

        Returns:
            True if session is valid, False otherwise
        """
        if not self.authenticated:
            return False

        try:
            # Try to access a protected page
            response = self.session.get(
                f"{self.base_url}/booking",
                timeout=5,
                allow_redirects=False
            )

            # If we get redirected to login, session is invalid
            if response.status_code in [301, 302, 303, 307]:
                location = response.headers.get('Location', '')
                if 'login' in location.lower() or 'shibboleth' in location.lower():
                    logger.debug("Session invalid - redirected to login")
                    return False

            return response.status_code == 200

        except Exception as e:
            logger.warning(f"Session validation failed: {e}")
            return False

    def ensure_authenticated(self, username: str, password: str) -> None:
        """
        Ensure we have a valid authenticated session.

        Tries to load existing session first, then authenticates if needed.

        Args:
            username: UIUC NetID
            password: UIUC password

        Raises:
            AuthenticationError: If authentication fails
        """
        # Try to load existing session
        if self.load_session():
            logger.info("Using existing authenticated session")
            return

        # Need to authenticate
        logger.info("No valid session found, authenticating...")
        if not self.login(username, password):
            raise AuthenticationError("Failed to establish authenticated session")
