"""ParcelsApp Live Tracker.

Uses Playwright to intercept the internal parcelsapp.com API (api/v2/parcels)
and retrieve real-time tracking status for given tracking IDs.

The approach mirrors the open-source parcelsapp-cli by dustindog101:
https://github.com/dustindog101/parcelsapp-cli
"""

import time
from dataclasses import dataclass
from datetime import datetime

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# Status keyword → normalized status string (maps parcelsapp event text)
_STATUS_KEYWORDS: dict[str, str] = {
    "available for pickup": "Out for delivery",
    "awaiting for you to pick up": "Out for delivery",
    "awaiting to pick up": "Out for delivery",
    "out for delivery": "Out for delivery",
    "delivered": "Delivered",
    "delivery complete": "Delivered",
    "import clearance complete": "Shipped",
    "received by local delivery": "Shipped",
    "arrived at": "Shipped",
    "in transit": "Shipped",
    "departed": "Shipped",
    "left from": "Shipped",
    "leaving from": "Shipped",
    "export clearance": "Shipped",
    "inbound in sorting": "Shipped",
    "outbound in sorting": "Shipped",
    "last-mile delivery forecast": "Shipped",
    "handed over": "Shipped",
    "dispatched": "Shipped",
}

_STATUS_PRECEDENCE: dict[str, int] = {
    "unknown": 0,
    "confirmed": 1,
    "shipped": 2,
    "out for delivery": 3,
    "delivered": 4,
}


@dataclass
class TrackingResult:
    """Result of a parcelsapp.com tracking lookup.

    Attributes:
        tracking_id: The tracking number queried.
        status: Normalized status string (e.g. "Shipped", "Out for delivery").
        carrier: Detected carrier name, if any.
        latest_event: Human-readable description of the most recent event.
        latest_event_date: ISO-formatted date of the most recent event, if available.
        raw_states: Full list of raw state dicts from the API response.
        error: Error code string if the lookup failed.
    """

    tracking_id: str
    status: str = "Unknown"
    carrier: str | None = None
    latest_event: str | None = None
    latest_event_date: str | None = None
    raw_states: list[dict] | None = None
    error: str | None = None


def _normalize_status(event_text: str) -> str:
    """Map a raw parcelsapp event string to a normalized status.

    Args:
        event_text: Raw event description from the API.

    Returns:
        A normalized status string.
    """
    lower = event_text.lower()
    for keyword, status in _STATUS_KEYWORDS.items():
        if keyword in lower:
            return status
    return "Shipped"  # Default for any in-transit event


def _extract_carrier(data: dict) -> str | None:
    """Extract carrier name from a parcelsapp API response payload.

    Args:
        data: The raw JSON response dict.

    Returns:
        Carrier name string or None.
    """
    carriers_raw = data.get("carriers") or []
    if not carriers_raw:
        return None
    first = carriers_raw[0]
    if isinstance(first, dict):
        return first.get("name") or first.get("slug") or str(first)
    return str(first)


def _parse_date(val) -> str | None:
    """Parse a date value from the parcelsapp API into an ISO string.

    Args:
        val: Date value from the API (str, dict with 'period', or None).

    Returns:
        ISO date string or None.
    """
    if not val:
        return None
    if isinstance(val, dict):
        period = val.get("period", [])
        if period:
            val = period[0]
        else:
            return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        return dt.isoformat()
    except (ValueError, AttributeError):
        return str(val)


def _best_status_from_states(states: list[dict]) -> tuple[str, str | None, str | None]:
    """Determine the best (highest precedence) status from a list of tracking states.

    Args:
        states: List of state dicts from parcelsapp API.

    Returns:
        Tuple of (normalized_status, latest_event_text, latest_event_date).
    """
    best_status = "Shipped"
    best_precedence = _STATUS_PRECEDENCE.get("shipped", 2)
    latest_event: str | None = None
    latest_date: str | None = None

    for i, state in enumerate(states):
        raw_status = state.get("status", "")
        normalized = _normalize_status(raw_status)
        prec = _STATUS_PRECEDENCE.get(normalized.lower(), 2)
        if prec >= best_precedence:
            best_status = normalized
            best_precedence = prec
        if i == 0:
            latest_event = raw_status
            latest_date = _parse_date(state.get("date"))

    return best_status, latest_event, latest_date


def fetch_tracking(tracking_id: str, timeout_ms: int = 20_000) -> TrackingResult:
    """Fetch real-time tracking status for a single tracking ID from parcelsapp.com.

    Uses Playwright to navigate to the parcelsapp.com tracking page and intercept
    the internal api/v2/parcels POST response. Requires playwright to be installed
    with chromium browsers: `playwright install chromium`.

    Args:
        tracking_id: The shipment tracking number to look up.
        timeout_ms: Maximum wait time in milliseconds for the API response.

    Returns:
        A TrackingResult dataclass with status, carrier, and event details.
    """
    best: dict | None = None

    def on_response(response):
        nonlocal best
        if "api/v2/parcels" in response.url and response.request.method == "POST":
            try:
                payload = response.json()
                if payload.get("states"):
                    best = payload
                elif best is None:
                    best = payload
            except Exception:
                pass

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--headless=new",
                ],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page.on("response", on_response)

            try:
                page.goto(
                    f"https://parcelsapp.com/en/tracking/{tracking_id}",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                # Poll up to timeout_ms in 3-second increments for API response
                elapsed = 0
                step = 3_000
                while elapsed < timeout_ms:
                    page.wait_for_timeout(step)
                    elapsed += step
                    if best and best.get("states"):
                        break
            except PlaywrightTimeoutError:
                pass
            finally:
                browser.close()
    except Exception as e:
        return TrackingResult(tracking_id=tracking_id, error=str(e))

    if not best:
        return TrackingResult(tracking_id=tracking_id, error="NO_DATA")

    error = best.get("error")
    if error:
        return TrackingResult(tracking_id=tracking_id, error=error)

    states = best.get("states", [])
    carrier = _extract_carrier(best)

    if not states:
        return TrackingResult(
            tracking_id=tracking_id, carrier=carrier, error="NO_STATES"
        )

    status, latest_event, latest_date = _best_status_from_states(states)

    return TrackingResult(
        tracking_id=tracking_id,
        status=status,
        carrier=carrier,
        latest_event=latest_event,
        latest_event_date=latest_date,
        raw_states=states,
    )


def fetch_all_tracking(
    tracking_ids: list[str],
    delay_between_ms: int = 2_000,
) -> dict[str, TrackingResult]:
    """Fetch real-time tracking for multiple tracking IDs sequentially.

    Args:
        tracking_ids: List of tracking numbers to look up.
        delay_between_ms: Delay in ms to wait between consecutive lookups.

    Returns:
        Dict mapping tracking_id -> TrackingResult.
    """
    results: dict[str, TrackingResult] = {}
    for i, tid in enumerate(tracking_ids):
        print(f"  [Tracker] Fetching live status for: {tid}")
        result = fetch_tracking(tid)
        results[tid] = result
        if result.error:
            print(f"  [Tracker] {tid} -> Error: {result.error}")
        else:
            print(f"  [Tracker] {tid} -> {result.status} (carrier: {result.carrier})")
        if i < len(tracking_ids) - 1 and delay_between_ms > 0:
            time.sleep(delay_between_ms / 1000)
    return results
