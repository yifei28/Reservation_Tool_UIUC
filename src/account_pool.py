"""Persistent pool of authenticated Active Illini accounts.

Account selection and validation happen before a reservation's critical path.
An account is consumed for a target date only after a confirmed reservation.
"""

from __future__ import annotations

import fcntl
import json
import os
import pickle
import random
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

from .booking_http import FastBookingClient


class NoAvailableAccountError(RuntimeError):
    """Raised when no valid, unused account exists for a target date."""


class AccountInUseError(RuntimeError):
    """Raised when an assigned or leased account cannot be removed."""


@dataclass
class AccountLease:
    """An account reserved for one in-flight booking attempt."""

    account_id: str
    label: str
    target_date: str
    lease_id: str
    client: FastBookingClient


DateLike = Union[str, date, datetime]


class AccountPool:
    """Store, refresh, and lease multiple account cookie sessions safely."""

    REGISTRY_VERSION = 1
    LEASE_TTL_SECONDS = 300

    def __init__(
        self,
        accounts_dir: str = ".accounts",
        legacy_session_file: Optional[str] = ".session",
        client_class=FastBookingClient,
        now_fn=time.time,
    ):
        self.accounts_dir = Path(accounts_dir)
        self.sessions_dir = self.accounts_dir / "sessions"
        self.registry_file = self.accounts_dir / "accounts.json"
        self.lock_file = self.accounts_dir / ".lock"
        self.legacy_session_file = Path(legacy_session_file) if legacy_session_file else None
        self.client_class = client_class
        self.now_fn = now_fn

        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.accounts_dir.chmod(0o700)
        self.sessions_dir.chmod(0o700)
        self._ensure_registry()
        self._migrate_legacy_session()

    @staticmethod
    def _date_key(target_date: DateLike) -> str:
        if isinstance(target_date, datetime):
            return target_date.date().isoformat()
        if isinstance(target_date, date):
            return target_date.isoformat()
        return str(target_date)[:10]

    @contextmanager
    def _locked(self):
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        with open(self.lock_file, "a+") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _empty_registry(self) -> Dict:
        return {
            "version": self.REGISTRY_VERSION,
            "accounts": {},
            "account_order": [],
            "usage": {},
            "refresh_paused_until": 0,
        }

    def _ensure_registry(self) -> None:
        with self._locked():
            if not self.registry_file.exists():
                self._save_registry_unlocked(self._empty_registry())

    def _load_registry_unlocked(self) -> Dict:
        try:
            data = json.loads(self.registry_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = self._empty_registry()

        data.setdefault("version", self.REGISTRY_VERSION)
        data.setdefault("accounts", {})
        data.setdefault("account_order", list(data["accounts"]))
        data.setdefault("usage", {})
        data.setdefault("refresh_paused_until", 0)
        return data

    def _save_registry_unlocked(self, data: Dict) -> None:
        temp_file = self.registry_file.with_name(
            f"{self.registry_file.name}.tmp-{uuid.uuid4().hex}"
        )
        temp_file.write_text(json.dumps(data, indent=2, sort_keys=True))
        temp_file.chmod(0o600)
        os.replace(temp_file, self.registry_file)

    def _session_path(self, account_id: str) -> Path:
        return self.sessions_dir / f"{account_id}.session"

    def _write_session(self, account_id: str, cookies: Dict[str, str]) -> None:
        session_path = self._session_path(account_id)
        temp_path = session_path.with_name(f"{session_path.name}.tmp-{uuid.uuid4().hex}")
        session_data = {
            "cookies": dict(cookies),
            "authenticated": True,
            "auth_time": self.now_fn(),
        }
        with open(temp_path, "wb") as session_handle:
            pickle.dump(session_data, session_handle)
        temp_path.chmod(0o600)
        os.replace(temp_path, session_path)

    def _migrate_legacy_session(self) -> None:
        if not self.legacy_session_file or not self.legacy_session_file.exists():
            return

        with self._locked():
            registry = self._load_registry_unlocked()
            if registry["accounts"]:
                return

            account_id = uuid.uuid4().hex
            destination = self._session_path(account_id)
            shutil.copy2(self.legacy_session_file, destination)
            destination.chmod(0o600)
            now = self.now_fn()
            registry["accounts"][account_id] = {
                "label": "Default account",
                "status": "unknown",
                "last_validated": None,
                "last_error": None,
                "next_refresh_at": now,
                "created_at": now,
            }
            registry["account_order"].append(account_id)
            self._save_registry_unlocked(registry)

    def store_cookies(
        self,
        label: str,
        cookies: Dict[str, str],
        account_id: Optional[str] = None,
    ) -> str:
        """Add an account or replace one account's cookies after re-login."""
        if not cookies:
            raise ValueError("Cannot store an empty cookie set")

        label = (label or "Account").strip()
        account_id = account_id or uuid.uuid4().hex
        self._write_session(account_id, cookies)

        with self._locked():
            registry = self._load_registry_unlocked()
            existing = registry["accounts"].get(account_id, {})
            now = self.now_fn()
            registry["accounts"][account_id] = {
                "label": label or existing.get("label", "Account"),
                "status": "valid",
                "last_validated": now,
                "last_error": None,
                "next_refresh_at": now + random.randint(480, 720),
                "created_at": existing.get("created_at", now),
            }
            if account_id not in registry["account_order"]:
                registry["account_order"].append(account_id)
            self._save_registry_unlocked(registry)
        return account_id

    def remove_account(self, account_id: str) -> bool:
        with self._locked():
            registry = self._load_registry_unlocked()
            if account_id not in registry["accounts"]:
                return False
            for target_date, day_usage in registry["usage"].items():
                use = day_usage.get(account_id)
                if use and use.get("status") in {"assigned", "leased"}:
                    raise AccountInUseError(
                        f"Account is assigned to a pending booking for {target_date}"
                    )
            del registry["accounts"][account_id]
            registry["account_order"] = [
                item for item in registry["account_order"] if item != account_id
            ]
            for day_usage in registry["usage"].values():
                day_usage.pop(account_id, None)
            self._save_registry_unlocked(registry)

        self._session_path(account_id).unlink(missing_ok=True)
        return True

    def account_count(self) -> int:
        with self._locked():
            return len(self._load_registry_unlocked()["accounts"])

    def list_accounts(self, target_date: Optional[DateLike] = None) -> List[Dict]:
        date_key = self._date_key(target_date) if target_date else None
        with self._locked():
            registry = self._load_registry_unlocked()
            usage = registry["usage"].get(date_key, {}) if date_key else {}
            result = []
            for account_id in registry["account_order"]:
                account = registry["accounts"].get(account_id)
                if not account:
                    continue
                use = usage.get(account_id)
                result.append({
                    "id": account_id,
                    "label": account.get("label", "Account"),
                    "status": account.get("status", "unknown"),
                    "last_validated": account.get("last_validated"),
                    "last_error": account.get("last_error"),
                    "used_for_date": bool(use and use.get("status") == "used"),
                    "leased_for_date": bool(use and use.get("status") == "leased"),
                    "assigned_for_date": bool(use and use.get("status") == "assigned"),
                    "assigned_booking_id": use.get("booking_id") if use else None,
                })
            return result

    def assign_accounts(
        self,
        target_date: DateLike,
        booking_ids: Iterable[str],
        batch_id: Optional[str] = None,
        exclude: Optional[Iterable[str]] = None,
    ) -> Dict[str, str]:
        """Atomically assign one distinct account to every booking ID."""
        date_key = self._date_key(target_date)
        booking_ids = list(booking_ids)
        if not booking_ids or len(set(booking_ids)) != len(booking_ids):
            raise ValueError("Booking IDs must be non-empty and unique")
        excluded = set(exclude or [])

        with self._locked():
            registry = self._load_registry_unlocked()
            day_usage = registry["usage"].setdefault(date_key, {})
            candidates = []
            for account_id in registry["account_order"]:
                account = registry["accounts"].get(account_id)
                if not account or account_id in excluded:
                    continue
                if account.get("status") == "invalid":
                    continue
                existing = day_usage.get(account_id)
                if existing:
                    leased_at = existing.get("leased_at", 0)
                    stale = (
                        existing.get("status") == "leased"
                        and self.now_fn() - leased_at > self.LEASE_TTL_SECONDS
                    )
                    if not stale:
                        continue
                if self._session_path(account_id).exists():
                    candidates.append(account_id)

            if len(candidates) < len(booking_ids):
                if not day_usage:
                    registry["usage"].pop(date_key, None)
                raise NoAvailableAccountError(
                    f"Requested {len(booking_ids)} booking(s), but only "
                    f"{len(candidates)} valid unused account(s) are available for {date_key}"
                )

            now = self.now_fn()
            assignments = {}
            for booking_id, account_id in zip(booking_ids, candidates):
                day_usage[account_id] = {
                    "status": "assigned",
                    "booking_id": booking_id,
                    "batch_id": batch_id,
                    "assigned_at": now,
                }
                assignments[booking_id] = account_id
            self._save_registry_unlocked(registry)
            return assignments

    def release_assignment(
        self, account_id: str, target_date: DateLike, booking_id: str
    ) -> bool:
        """Release a pending schedule-time account assignment."""
        date_key = self._date_key(target_date)
        with self._locked():
            registry = self._load_registry_unlocked()
            day_usage = registry["usage"].get(date_key, {})
            existing = day_usage.get(account_id)
            if not existing or existing.get("status") != "assigned":
                return False
            if existing.get("booking_id") != booking_id:
                return False
            del day_usage[account_id]
            if not day_usage:
                registry["usage"].pop(date_key, None)
            self._save_registry_unlocked(registry)
            return True

    def activate_assignment(
        self,
        account_id: str,
        target_date: DateLike,
        booking_id: str,
        validate: bool = True,
    ) -> AccountLease:
        """Turn a persisted assignment into a validated in-flight lease."""
        date_key = self._date_key(target_date)
        with self._locked():
            registry = self._load_registry_unlocked()
            account = registry["accounts"].get(account_id)
            day_usage = registry["usage"].get(date_key, {})
            existing = day_usage.get(account_id)
            if (
                not account
                or account.get("status") == "invalid"
                or not existing
                or existing.get("status") != "assigned"
                or existing.get("booking_id") != booking_id
            ):
                raise NoAvailableAccountError(
                    f"Assigned account is unavailable for booking {booking_id}"
                )

            lease_id = uuid.uuid4().hex
            day_usage[account_id] = {
                **existing,
                "status": "leased",
                "lease_id": lease_id,
                "leased_at": self.now_fn(),
            }
            label = account.get("label", "Account")
            self._save_registry_unlocked(registry)

        try:
            client = self.client_class(str(self._session_path(account_id)))
        except Exception as exc:
            self.mark_invalid(account_id, str(exc))
            self._release_values(account_id, date_key, lease_id)
            raise NoAvailableAccountError(str(exc)) from exc

        if validate and not client.keep_alive():
            error = getattr(client, "last_keep_alive_error", None) or "Session validation failed"
            if error.startswith("Network error:"):
                self._record_transient_error(account_id, error)
            else:
                self.mark_invalid(account_id, error)
            self._release_values(account_id, date_key, lease_id)
            raise NoAvailableAccountError(error)
        if validate:
            self._record_valid(account_id)

        return AccountLease(
            account_id=account_id,
            label=label,
            target_date=date_key,
            lease_id=lease_id,
            client=client,
        )

    def _candidate_ids(self, target_date: str, excluded: Iterable[str]) -> List[str]:
        excluded = set(excluded)
        with self._locked():
            registry = self._load_registry_unlocked()
            day_usage = registry["usage"].get(target_date, {})
            candidates = []
            for account_id in registry["account_order"]:
                account = registry["accounts"].get(account_id)
                if not account or account_id in excluded:
                    continue
                if account.get("status") == "invalid":
                    continue
                existing = day_usage.get(account_id)
                if existing:
                    leased_at = existing.get("leased_at", 0)
                    stale = (
                        existing.get("status") == "leased"
                        and self.now_fn() - leased_at > self.LEASE_TTL_SECONDS
                    )
                    if not stale:
                        continue
                if not self._session_path(account_id).exists():
                    continue
                candidates.append(account_id)
            return candidates

    def _claim(self, account_id: str, target_date: str) -> Optional[str]:
        with self._locked():
            registry = self._load_registry_unlocked()
            account = registry["accounts"].get(account_id)
            if not account or account.get("status") == "invalid":
                return None
            day_usage = registry["usage"].setdefault(target_date, {})
            existing = day_usage.get(account_id)
            if existing:
                leased_at = existing.get("leased_at", 0)
                stale = (
                    existing.get("status") == "leased"
                    and self.now_fn() - leased_at > self.LEASE_TTL_SECONDS
                )
                if not stale:
                    return None

            lease_id = uuid.uuid4().hex
            day_usage[account_id] = {
                "status": "leased",
                "lease_id": lease_id,
                "leased_at": self.now_fn(),
            }
            self._save_registry_unlocked(registry)
            return lease_id

    def acquire(
        self,
        target_date: DateLike,
        validate: bool = True,
        exclude: Optional[Iterable[str]] = None,
    ) -> AccountLease:
        """Lease the first unused account, validating it before returning."""
        date_key = self._date_key(target_date)
        excluded = set(exclude or [])

        while True:
            candidates = self._candidate_ids(date_key, excluded)
            if not candidates:
                raise NoAvailableAccountError(
                    f"No valid unused accounts are available for {date_key}"
                )

            account_id = candidates[0]
            excluded.add(account_id)
            lease_id = self._claim(account_id, date_key)
            if not lease_id:
                continue

            try:
                client = self.client_class(str(self._session_path(account_id)))
            except Exception as exc:
                self.mark_invalid(account_id, str(exc))
                self._release_values(account_id, date_key, lease_id)
                continue

            if validate and not client.keep_alive():
                error = getattr(client, "last_keep_alive_error", None) or "Session validation failed"
                if error.startswith("Network error:"):
                    self._record_transient_error(account_id, error)
                else:
                    self.mark_invalid(account_id, error)
                self._release_values(account_id, date_key, lease_id)
                continue
            if validate:
                self._record_valid(account_id)

            with self._locked():
                registry = self._load_registry_unlocked()
                account = registry["accounts"].get(account_id, {})
                label = account.get("label", "Account")

            return AccountLease(
                account_id=account_id,
                label=label,
                target_date=date_key,
                lease_id=lease_id,
                client=client,
            )

    def _release_values(self, account_id: str, target_date: str, lease_id: str) -> bool:
        with self._locked():
            registry = self._load_registry_unlocked()
            day_usage = registry["usage"].get(target_date, {})
            existing = day_usage.get(account_id)
            if not existing or existing.get("lease_id") != lease_id:
                return False
            if existing.get("status") != "leased":
                return False
            del day_usage[account_id]
            if not day_usage:
                registry["usage"].pop(target_date, None)
            self._save_registry_unlocked(registry)
            return True

    def release(self, lease: AccountLease) -> bool:
        return self._release_values(
            lease.account_id, lease.target_date, lease.lease_id
        )

    def mark_used(self, lease: AccountLease) -> bool:
        with self._locked():
            registry = self._load_registry_unlocked()
            day_usage = registry["usage"].get(lease.target_date, {})
            existing = day_usage.get(lease.account_id)
            if not existing or existing.get("lease_id") != lease.lease_id:
                return False
            day_usage[lease.account_id] = {
                "status": "used",
                "used_at": self.now_fn(),
            }
            account = registry["accounts"].get(lease.account_id)
            if account is not None:
                account["last_used_at"] = self.now_fn()
            self._save_registry_unlocked(registry)
            return True

    def mark_invalid(self, account_id: str, error: str) -> None:
        with self._locked():
            registry = self._load_registry_unlocked()
            account = registry["accounts"].get(account_id)
            if account is None:
                return
            account["status"] = "invalid"
            account["last_error"] = error
            account["last_validated"] = self.now_fn()
            self._save_registry_unlocked(registry)

    def _record_valid(self, account_id: str) -> None:
        with self._locked():
            registry = self._load_registry_unlocked()
            account = registry["accounts"].get(account_id)
            if account is None:
                return
            account["status"] = "valid"
            account["last_error"] = None
            account["last_validated"] = self.now_fn()
            account["next_refresh_at"] = self.now_fn() + random.randint(480, 720)
            self._save_registry_unlocked(registry)

    def _record_transient_error(self, account_id: str, error: str) -> None:
        with self._locked():
            registry = self._load_registry_unlocked()
            account = registry["accounts"].get(account_id)
            if account is None:
                return
            account["status"] = "unknown"
            account["last_error"] = error
            account["last_validated"] = self.now_fn()
            self._save_registry_unlocked(registry)

    def get_client(self, account_id: Optional[str] = None) -> FastBookingClient:
        """Return a client for non-consuming operations such as slot lookup."""
        with self._locked():
            registry = self._load_registry_unlocked()
            if account_id:
                candidates = [account_id]
            else:
                candidates = registry["account_order"]
            for candidate in candidates:
                account = registry["accounts"].get(candidate)
                if account and account.get("status") != "invalid":
                    path = self._session_path(candidate)
                    if path.exists():
                        return self.client_class(str(path))
        raise NoAvailableAccountError("No stored valid account sessions are available")

    def validate_account(self, account_id: str) -> bool:
        try:
            client = self.get_client(account_id)
            valid = client.keep_alive()
        except Exception as exc:
            self.mark_invalid(account_id, str(exc))
            return False

        with self._locked():
            registry = self._load_registry_unlocked()
            account = registry["accounts"].get(account_id)
            if account is None:
                return False
            error = getattr(client, "last_keep_alive_error", None) if not valid else None
            transient = bool(error and error.startswith("Network error:"))
            account["status"] = "valid" if valid else ("unknown" if transient else "invalid")
            account["last_validated"] = self.now_fn()
            account["last_error"] = None if valid else (error or "Session validation failed")
            account["next_refresh_at"] = self.now_fn() + random.randint(480, 720)
            self._save_registry_unlocked(registry)
        return valid

    def refresh_due_accounts(self, force: bool = False) -> Dict[str, bool]:
        """Keep sessions alive and persist renewed cookies outside booking time."""
        now = self.now_fn()
        with self._locked():
            registry = self._load_registry_unlocked()
            if registry.get("refresh_paused_until", 0) > now:
                return {}
            due = []
            for account_id in registry["account_order"]:
                account = registry["accounts"].get(account_id)
                if not account:
                    continue
                if account.get("status") == "invalid" and not force:
                    continue
                if force or account.get("next_refresh_at", 0) <= now:
                    due.append(account_id)
                    # Claim refresh work so another process will not duplicate it.
                    account["next_refresh_at"] = now + random.randint(480, 720)
            self._save_registry_unlocked(registry)

        return {account_id: self.validate_account(account_id) for account_id in due}

    def pause_background_refresh(self, until_timestamp: float) -> None:
        """Coordinate a cross-process quiet window around reservation release."""
        with self._locked():
            registry = self._load_registry_unlocked()
            registry["refresh_paused_until"] = max(
                registry.get("refresh_paused_until", 0), until_timestamp
            )
            self._save_registry_unlocked(registry)

    def clear_background_refresh_pause(self) -> None:
        with self._locked():
            registry = self._load_registry_unlocked()
            registry["refresh_paused_until"] = 0
            self._save_registry_unlocked(registry)
