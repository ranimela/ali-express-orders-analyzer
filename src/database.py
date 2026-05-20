"""Local SQLite Database Manager.

Persists the latest status, tracking details, items, and history of orders
to allow consistent daily summaries even when no new emails arrive.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def get_db_connection(db_path: Path = Path("orders.db")) -> sqlite3.Connection:
    """Create and return a database connection.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        sqlite3.Connection object.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database(db_path: Path = Path("orders.db")) -> None:
    """Initialize the SQLite tables if they do not exist.

    Args:
        db_path: Path to the SQLite database file.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Create orders table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                tracking_id TEXT,
                carrier TEXT,
                items_json TEXT NOT NULL,
                latest_status TEXT NOT NULL,
                last_updated_at TEXT NOT NULL
            )
            """
        )

        # Create history table to track status over time
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                status TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders (order_id) ON DELETE CASCADE
            )
            """
        )

        # Create processed_emails table to avoid duplicate Gemini extraction calls
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_emails (
                message_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            )
            """
        )
        conn.commit()

        # Migration: add latest_event_text column if it doesn't exist yet
        try:
            cursor.execute("ALTER TABLE orders ADD COLUMN latest_event_text TEXT")
            conn.commit()
        except Exception:
            pass  # Column already exists


def upsert_order(
    order_id: str,
    items: list[dict[str, Any]],
    status: str,
    tracking_id: str | None = None,
    carrier: str | None = None,
    db_path: Path = Path("orders.db"),
) -> bool:
    """Insert or update an order in the database.

    If the order exists, it updates status, items list, carrier, and tracking ID
    if new information is provided. If the status has changed, logs the event
    to the history table.

    Args:
        order_id: The AliExpress Order ID.
        items: List of dictionaries, each representing an order item.
        status: The parsed latest shipment status (e.g. Confirmed, Shipped, Delivered).
        tracking_id: Optional shipment tracking number/ID.
        carrier: Optional shipment carrier.
        db_path: Path to the SQLite database file.

    Returns:
        True if the order status was updated or a new order was added; False otherwise.
    """
    now_iso = datetime.now(UTC).isoformat()
    items_str = json.dumps(items)
    status_changed = False

    # Normalize order_id if generic or missing to avoid clashing and overwriting different orders
    is_generic = order_id.lower().strip() in ["unknown", "not provided", "none", ""]

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Case A: If order_id is generic but tracking_id is provided, check if it matches an existing order
        if is_generic and tracking_id:
            cursor.execute(
                "SELECT order_id FROM orders WHERE tracking_id = ?", (tracking_id,)
            )
            found = cursor.fetchone()
            if found:
                order_id = found["order_id"]
                is_generic = False
            else:
                order_id = tracking_id
                is_generic = False

        # Case B: If order_id is still generic, generate a unique hash-based ID
        if is_generic:
            import hashlib

            items_hash = hashlib.md5(items_str.encode("utf-8")).hexdigest()[:10]
            order_id = f"GENERIC_{items_hash}"

        # Check if order already exists
        cursor.execute(
            "SELECT latest_status, tracking_id, carrier, items_json FROM orders WHERE order_id = ?",
            (order_id,),
        )
        row = cursor.fetchone()

        if row is None:
            # Insert new order
            cursor.execute(
                """
                INSERT INTO orders (order_id, tracking_id, carrier, items_json, latest_status, last_updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (order_id, tracking_id, carrier, items_str, status, now_iso),
            )

            # Log initial history status
            cursor.execute(
                """
                INSERT INTO history (order_id, status, timestamp)
                VALUES (?, ?, ?)
                """,
                (order_id, status, now_iso),
            )
            status_changed = True
        else:
            current_status = row["latest_status"]
            current_tracking = row["tracking_id"]
            current_carrier = row["carrier"]

            # Determine if we should update fields (preserve existing if new is None)
            new_tracking = tracking_id if tracking_id else current_tracking
            new_carrier = carrier if carrier else current_carrier

            # Check status change with precedence protection (prevent downgrades e.g. Delivered -> Confirmed)
            status_precedence = {
                "unknown": 0,
                "confirmed": 1,
                "shipped": 2,
                "out for delivery": 3,
                "delivered": 4,
            }

            curr_val = status_precedence.get(current_status.lower(), 0)
            new_val = status_precedence.get(status.lower(), 0)

            if curr_val > new_val:
                final_status = current_status
            else:
                final_status = status
                if current_status != status:
                    status_changed = True

            # Merge new items with existing items under the same order
            try:
                current_items = json.loads(row["items_json"])
            except Exception:
                current_items = []

            # Deduplicate items using fuzzy name matching
            merged_list = []
            for item in current_items:
                merged_list.append(dict(item))

            for new_item in items:
                new_name = new_item.get("name", "Unknown Item")
                found_idx = -1
                for idx, ext_item in enumerate(merged_list):
                    ext_name = ext_item.get("name", "")

                    # Clean names for comparison
                    c1 = new_name.replace("...", "").strip().lower()
                    c2 = ext_name.replace("...", "").strip().lower()

                    is_same = False
                    if c1 == c2:
                        is_same = True
                    elif len(c1) >= 10 and len(c2) >= 10:
                        if c1.startswith(c2) or c2.startswith(c1):
                            is_same = True
                        elif len(c1) >= 20 and len(c2) >= 20 and c1[:20] == c2[:20]:
                            is_same = True

                    if is_same:
                        found_idx = idx
                        break

                if found_idx >= 0:
                    ext_item = merged_list[found_idx]
                    # Keep the longer, more detailed item name
                    if len(new_name) > len(ext_item.get("name", "")):
                        ext_item["name"] = new_name
                    # Merge quantity
                    ext_item["quantity"] = max(
                        ext_item.get("quantity", 1), new_item.get("quantity", 1)
                    )
                    # Prefer new price if it is provided
                    if new_item.get("price") is not None:
                        ext_item["price"] = new_item["price"]
                else:
                    merged_list.append(dict(new_item))

            items_str = json.dumps(merged_list)

            # If items list updated or status changed or tracking details changed
            # Update the order entry
            cursor.execute(
                """
                UPDATE orders
                SET tracking_id = ?,
                    carrier = ?,
                    items_json = ?,
                    latest_status = ?,
                    last_updated_at = ?
                WHERE order_id = ?
                """,
                (new_tracking, new_carrier, items_str, final_status, now_iso, order_id),
            )

            # If status changed, log to history
            if status_changed:
                cursor.execute(
                    """
                    INSERT INTO history (order_id, status, timestamp)
                    VALUES (?, ?, ?)
                    """,
                    (order_id, final_status, now_iso),
                )

        conn.commit()

    return status_changed


def get_all_orders(db_path: Path = Path("orders.db")) -> list[dict[str, Any]]:
    """Retrieve all orders from the database.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List of order dictionaries.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders ORDER BY last_updated_at DESC")
        rows = cursor.fetchall()

        orders = []
        for row in rows:
            orders.append(
                {
                    "order_id": row["order_id"],
                    "tracking_id": row["tracking_id"],
                    "carrier": row["carrier"],
                    "items": json.loads(row["items_json"]),
                    "latest_status": row["latest_status"],
                    "last_updated_at": row["last_updated_at"],
                    "latest_event_text": row["latest_event_text"]
                    if "latest_event_text" in row.keys()
                    else None,
                }
            )
        return orders


def get_order_history(
    order_id: str, db_path: Path = Path("orders.db")
) -> list[dict[str, str]]:
    """Retrieve status history for a specific order.

    Args:
        order_id: The order ID to retrieve history for.
        db_path: Path to the SQLite database file.

    Returns:
        List of status change dictionaries containing 'status' and 'timestamp'.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, timestamp FROM history WHERE order_id = ? ORDER BY timestamp ASC",
            (order_id,),
        )
        rows = cursor.fetchall()
        return [
            {"status": row["status"], "timestamp": row["timestamp"]} for row in rows
        ]


def is_email_processed(message_id: str, db_path: Path = Path("orders.db")) -> bool:
    """Check if an email message ID has already been parsed and processed.

    Args:
        message_id: Unique IMAP email ID.
        db_path: Path to the SQLite database file.

    Returns:
        True if already processed; False otherwise.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM processed_emails WHERE message_id = ?", (message_id,)
        )
        return cursor.fetchone() is not None


def mark_email_as_processed(message_id: str, db_path: Path = Path("orders.db")) -> None:
    """Mark an email message ID as successfully processed.

    Args:
        message_id: Unique IMAP email ID.
        db_path: Path to the SQLite database file.
    """
    now_iso = datetime.now(UTC).isoformat()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO processed_emails (message_id, processed_at) VALUES (?, ?)",
            (message_id, now_iso),
        )
        conn.commit()


def find_matching_order_for_item(
    item_name: str, db_path: Path = Path("orders.db")
) -> str | None:
    """Find the best existing order containing a similar item.

    Analyzes database orders to find matches based on the item name prefix.
    Prefers active orders over delivered/cancelled ones to match current updates.

    Args:
        item_name: The name of the item to match.
        db_path: Path to the SQLite database file.

    Returns:
        The matched order ID or None if not found.
    """
    clean_name = item_name.replace("...", "").strip()
    prefix = clean_name[:15].lower()
    if len(prefix) < 5:
        return None

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT order_id, items_json, latest_status FROM orders")
        rows = cursor.fetchall()

        candidates = []

        for row in rows:
            try:
                # Do not match to generic/temporary order entries if we are looking for the source order
                order_id = row["order_id"]
                is_generic = (
                    order_id.startswith("GENERIC_")
                    or order_id.startswith("TRACKING_")
                    or order_id.lower().strip() in ["unknown", "none", ""]
                )

                existing_items = json.loads(row["items_json"])
                for ext_item in existing_items:
                    ext_name = (
                        ext_item.get("name", "").replace("...", "").strip().lower()
                    )
                    if (
                        prefix in ext_name
                        or ext_name[:15].lower() in clean_name.lower()
                    ):
                        is_active = row["latest_status"].lower() not in [
                            "delivered",
                            "cancelled",
                        ]
                        candidates.append((order_id, is_active, is_generic))
                        break
            except Exception:
                continue

        if not candidates:
            return None

        # Sorting: We prefer matches in:
        # 1. Non-generic (real) order IDs
        # 2. Active orders (latest_status != 'delivered')
        candidates.sort(key=lambda x: (not x[2], x[1]), reverse=True)
        return candidates[0][0]


def get_active_tracking_ids(db_path: Path = Path("orders.db")) -> list[tuple[str, str]]:
    """Return (order_id, tracking_id) pairs for all active (non-delivered) orders.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List of (order_id, tracking_id) tuples where tracking_id is not None.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT order_id, tracking_id FROM orders WHERE tracking_id IS NOT NULL"
        )
        rows = cursor.fetchall()
        return [
            (row["order_id"], row["tracking_id"])
            for row in rows
            if row["tracking_id"] and row["tracking_id"].strip()
        ]


def update_status_from_tracker(
    order_id: str,
    new_status: str,
    new_carrier: str | None,
    new_event_text: str | None = None,
    db_path: Path = Path("orders.db"),
) -> bool:
    """Apply a live tracking status update to an order in the database.

    Respects status precedence (will not downgrade a status).
    Logs to history if status changes.

    Args:
        order_id: The order to update.
        new_status: Normalized status string from parcelsapp (e.g. "Out for delivery").
        new_carrier: Carrier name from parcelsapp, or None.
        new_event_text: Raw latest event description from parcelsapp, or None.
        db_path: Path to the SQLite database file.

    Returns:
        True if the status was updated; False otherwise.
    """
    status_precedence = {
        "unknown": 0,
        "confirmed": 1,
        "shipped": 2,
        "out for delivery": 3,
        "delivered": 4,
    }

    now_iso = datetime.now(UTC).isoformat()
    status_changed = False

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT latest_status, carrier FROM orders WHERE order_id = ?",
            (order_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return False

        current_status = row["latest_status"]
        current_carrier = row["carrier"]

        curr_prec = status_precedence.get(current_status.lower(), 0)
        new_prec = status_precedence.get(new_status.lower(), 0)

        final_status = new_status if new_prec >= curr_prec else current_status
        final_carrier = new_carrier if new_carrier else current_carrier

        if final_status != current_status:
            status_changed = True

        cursor.execute(
            """
            UPDATE orders
            SET latest_status = ?, carrier = ?, last_updated_at = ?, latest_event_text = ?
            WHERE order_id = ?
            """,
            (final_status, final_carrier, now_iso, new_event_text, order_id),
        )

        if status_changed:
            cursor.execute(
                """
                INSERT INTO history (order_id, status, timestamp)
                VALUES (?, ?, ?)
                """,
                (order_id, final_status, now_iso),
            )

        conn.commit()

    return status_changed
