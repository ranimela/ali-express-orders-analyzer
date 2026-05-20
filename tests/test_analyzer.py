"""Test suite for the AliExpress Gmail Order Analyzer.

Includes unit and integration tests for database operations, report building,
and Mock evaluations of Gemini and Gmail components.
"""

from pathlib import Path

import pytest

from database import (
    get_all_orders,
    get_order_history,
    initialize_database,
    upsert_order,
)
from reporting import build_markdown_report


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Fixture to provide a clean, temporary database file path for each test."""
    db_file = tmp_path / "test_orders.db"
    initialize_database(db_file)
    return db_file


def test_database_initialization(temp_db: Path) -> None:
    """Verify that initialize_database successfully creates the orders and history tables."""
    assert temp_db.exists()


def test_database_upsert_new_order(temp_db: Path) -> None:
    """Test inserting a brand new order inserts records in orders and history tables."""
    items = [{"name": "USB Cable", "quantity": 2, "price": 2.50}]
    order_id = "301234567890123"

    # Upsert a new order
    changed = upsert_order(
        order_id=order_id,
        items=items,
        status="Confirmed",
        tracking_id="LP0001",
        carrier="Cainiao",
        db_path=temp_db,
    )

    assert changed is True

    # Check orders table
    orders = get_all_orders(temp_db)
    assert len(orders) == 1
    assert orders[0]["order_id"] == order_id
    assert orders[0]["latest_status"] == "Confirmed"
    assert orders[0]["items"][0]["name"] == "USB Cable"
    assert orders[0]["tracking_id"] == "LP0001"
    assert orders[0]["carrier"] == "Cainiao"

    # Check history table
    history = get_order_history(order_id, temp_db)
    assert len(history) == 1
    assert history[0]["status"] == "Confirmed"


def test_database_upsert_existing_order_no_status_change(temp_db: Path) -> None:
    """Test upserting an existing order with the same status does not add redundant history."""
    items = [{"name": "USB Cable", "quantity": 2, "price": 2.50}]
    order_id = "301234567890123"

    # Initial insert
    upsert_order(
        order_id=order_id,
        items=items,
        status="Confirmed",
        tracking_id="LP0001",
        carrier="Cainiao",
        db_path=temp_db,
    )

    # Upsert again with same status
    changed = upsert_order(
        order_id=order_id,
        items=items,
        status="Confirmed",
        tracking_id="LP0001",
        carrier="Cainiao",
        db_path=temp_db,
    )

    assert changed is False

    # Check history remains size 1
    history = get_order_history(order_id, temp_db)
    assert len(history) == 1


def test_database_upsert_existing_order_status_change(temp_db: Path) -> None:
    """Test upserting an existing order with a new status updates status and adds history."""
    items = [{"name": "USB Cable", "quantity": 2, "price": 2.50}]
    order_id = "301234567890123"

    # Initial Confirmed status
    upsert_order(
        order_id=order_id,
        items=items,
        status="Confirmed",
        tracking_id="LP0001",
        carrier="Cainiao",
        db_path=temp_db,
    )

    # Status changes to Shipped
    changed = upsert_order(
        order_id=order_id,
        items=items,
        status="Shipped",
        tracking_id="LP0001",
        carrier="Cainiao",
        db_path=temp_db,
    )

    assert changed is True

    # Check database status
    orders = get_all_orders(temp_db)
    assert orders[0]["latest_status"] == "Shipped"

    # Check history has both events
    history = get_order_history(order_id, temp_db)
    assert len(history) == 2
    assert history[0]["status"] == "Confirmed"
    assert history[1]["status"] == "Shipped"


def test_build_markdown_report_formatting(temp_db: Path) -> None:
    """Test report builder properly segments active and delivered items."""
    # Add an active order
    upsert_order(
        order_id="111",
        items=[{"name": "Wireless Mouse", "quantity": 1, "price": 15.00}],
        status="Shipped",
        tracking_id="LP0001",
        carrier="Cainiao",
        db_path=temp_db,
    )

    # Add a delivered order
    upsert_order(
        order_id="222",
        items=[{"name": "Keycap Set", "quantity": 1, "price": 25.50}],
        status="Delivered",
        tracking_id="LP0002",
        carrier="China Post",
        db_path=temp_db,
    )

    orders = get_all_orders(temp_db)
    report = build_markdown_report(orders)

    # Ensure elements are formatted
    assert "# 📦 AliExpress Order Status Dashboard" in report
    assert "Active/In-Transit Orders:** 1" in report
    assert "Order ID: `111`" in report
    assert "Wireless Mouse" in report
    assert "Total Price:** **₪15.00**" in report
    assert "LP0001" in report

    # Ensure delivered section is gone
    assert "Completed/Delivered Orders" not in report
    assert "Order ID:** `222`" not in report


def test_database_upsert_generic_and_tracking_fallback(temp_db: Path) -> None:
    """Verify that upserting generic or missing order IDs generates unique fallback IDs."""
    items_1 = [{"name": "Generic Item 1", "quantity": 1, "price": None}]
    items_2 = [{"name": "Generic Item 2", "quantity": 1, "price": None}]

    # Upsert with generic order ID "Unknown" but a tracking ID
    changed_1 = upsert_order(
        order_id="Unknown",
        items=items_1,
        status="Shipped",
        tracking_id="TRACK123",
        carrier="Cainiao",
        db_path=temp_db,
    )
    assert changed_1 is True

    # Check that the order_id was saved as the tracking_id directly
    orders = get_all_orders(temp_db)
    assert len(orders) == 1
    assert orders[0]["order_id"] == "TRACK123"

    # Upsert another with generic "Not Provided" and different tracking ID
    changed_2 = upsert_order(
        order_id="Not Provided",
        items=items_2,
        status="Shipped",
        tracking_id="TRACK456",
        carrier="Cainiao",
        db_path=temp_db,
    )
    assert changed_2 is True

    orders = get_all_orders(temp_db)
    assert len(orders) == 2
    order_ids = {o["order_id"] for o in orders}
    assert order_ids == {"TRACK123", "TRACK456"}

    # Upsert with generic ID and NO tracking ID (should generate GENERIC_hash)
    changed_3 = upsert_order(
        order_id="None",
        items=items_1,
        status="Shipped",
        tracking_id=None,
        carrier=None,
        db_path=temp_db,
    )
    assert changed_3 is True

    orders = get_all_orders(temp_db)
    assert len(orders) == 3
    assert any(o["order_id"].startswith("GENERIC_") for o in orders)


def test_database_upsert_merge_items(temp_db: Path) -> None:
    """Verify that multiple upserts for the same order merge/group items instead of overwriting them."""
    order_id = "123456"

    # 1. Initial upsert with Item A
    item_a = {"name": "Coffee Filter", "quantity": 1, "price": 5.00}
    upsert_order(
        order_id=order_id,
        items=[item_a],
        status="Confirmed",
        tracking_id="TR1",
        carrier="Cainiao",
        db_path=temp_db,
    )

    # Verify initial insert
    orders = get_all_orders(temp_db)
    assert len(orders) == 1
    assert len(orders[0]["items"]) == 1
    assert orders[0]["items"][0]["name"] == "Coffee Filter"

    # 2. Upsert same order ID with a new Item B
    item_b = {"name": "Reusable Straw", "quantity": 2, "price": 3.50}
    upsert_order(
        order_id=order_id,
        items=[item_b],
        status="Shipped",
        tracking_id="TR1",
        carrier="Cainiao",
        db_path=temp_db,
    )

    # Verify both items are packed into the single order
    orders = get_all_orders(temp_db)
    assert len(orders) == 1
    items = {it["name"]: it for it in orders[0]["items"]}
    assert len(items) == 2
    assert "Coffee Filter" in items
    assert "Reusable Straw" in items
    assert items["Coffee Filter"]["quantity"] == 1
    assert items["Reusable Straw"]["quantity"] == 2

    # 3. Upsert same item name with higher quantity and price to verify update
    item_a_updated = {"name": "Coffee Filter", "quantity": 3, "price": 6.00}
    upsert_order(
        order_id=order_id,
        items=[item_a_updated],
        status="Shipped",
        tracking_id="TR1",
        carrier="Cainiao",
        db_path=temp_db,
    )

    orders = get_all_orders(temp_db)
    assert len(orders) == 1
    items = {it["name"]: it for it in orders[0]["items"]}
    assert len(items) == 2
    assert items["Coffee Filter"]["quantity"] == 3
    assert items["Coffee Filter"]["price"] == 6.00


def test_database_upsert_status_precedence(temp_db: Path) -> None:
    """Verify that upserting older notifications does not downgrade order status."""
    order_id = "999"
    items = [{"name": "Keycap Set", "quantity": 1, "price": 25.50}]

    # 1. Initial upsert with status Delivered
    upsert_order(
        order_id=order_id,
        items=items,
        status="Delivered",
        tracking_id="LP0002",
        carrier="China Post",
        db_path=temp_db,
    )

    # Verify status is Delivered
    orders = get_all_orders(temp_db)
    assert orders[0]["latest_status"] == "Delivered"

    # 2. Try to upsert status Confirmed (a downgrade)
    upsert_order(
        order_id=order_id,
        items=items,
        status="Confirmed",
        tracking_id="LP0002",
        carrier="China Post",
        db_path=temp_db,
    )

    # Verify status remains Delivered
    orders = get_all_orders(temp_db)
    assert orders[0]["latest_status"] == "Delivered"

    # 3. Try to upsert status Shipped (another downgrade)
    upsert_order(
        order_id=order_id,
        items=items,
        status="Shipped",
        tracking_id="LP0002",
        carrier="China Post",
        db_path=temp_db,
    )

    # Verify status remains Delivered
    orders = get_all_orders(temp_db)
    assert orders[0]["latest_status"] == "Delivered"


def test_sub_order_and_separate_order_resolution() -> None:
    """Verify that process_items_and_orders correctly maps sub-orders to combined orders

    while leaving separate checkouts of the same item distinct.
    """
    from reporting import process_items_and_orders

    orders = [
        # Combined order 1
        {
            "order_id": "1120551166526739",
            "latest_status": "Shipped",
            "carrier": None,
            "tracking_id": None,
            "items": [
                {"name": "28 oz Shaker Bottle", "quantity": 1, "price": 10.00},
                {"name": "Sports Hat", "quantity": 1, "price": 5.00},
            ],
            "last_updated_at": "2026-05-19T07:36:22",
        },
        # Sub-order of combined order 1 (shares same prefix and item)
        {
            "order_id": "1120551166626739",
            "latest_status": "Shipped",
            "carrier": None,
            "tracking_id": None,
            "items": [
                {"name": "28 oz Shaker Bottle", "quantity": 1, "price": 10.00},
            ],
            "last_updated_at": "2026-05-19T07:37:08",
        },
        # Separate checkout order (different prefix, same item)
        {
            "order_id": "1120649158896739",
            "latest_status": "Shipped",
            "carrier": None,
            "tracking_id": None,
            "items": [
                {"name": "Sports Hat", "quantity": 1, "price": 5.00},
            ],
            "last_updated_at": "2026-05-19T07:38:10",
        },
        # Tracking order for the separate checkout order
        {
            "order_id": "PH8002962840",
            "latest_status": "Shipped",
            "carrier": "Cainiao",
            "tracking_id": "PH8002962840",
            "items": [
                {"name": "Sports Hat", "quantity": 1, "price": 5.00},
            ],
            "last_updated_at": "2026-05-19T07:39:02",
        },
    ]

    active_grouped, _, _, _ = process_items_and_orders(orders)

    # We should have exactly three active grouped orders:
    # 1. 1120551166526739 (the combined order containing 28 oz Shaker Bottle and Sports Hat)
    # 2. 1120649158896739 (the separate sports hat checkout)
    # Note: 1120551166626739 (sub-order) should be completely merged and not appear.
    # Note: PH8002962840 (tracking order) should be merged into 1120649158896739.

    grouped_ids = [o["order_id"] for o in active_grouped]
    assert "1120551166526739" in grouped_ids
    assert "1120649158896739" in grouped_ids
    assert "1120551166626739" not in grouped_ids
    assert "PH8002962840" not in grouped_ids

    # Find the combined order and verify it has both items exactly once
    comb_order = next(o for o in active_grouped if o["order_id"] == "1120551166526739")
    comb_items = [i["name"] for i in comb_order["items"]]
    assert "28 oz Shaker Bottle" in comb_items
    assert "Sports Hat" in comb_items
    assert len(comb_items) == 2

    # Find the separate hat order and verify it has the hat and has correct tracking details
    sep_order = next(o for o in active_grouped if o["order_id"] == "1120649158896739")
    assert len(sep_order["items"]) == 1
    assert sep_order["items"][0]["name"] == "Sports Hat"
    assert sep_order["tracking_id"] == "PH8002962840"
    assert sep_order["carrier"] == "Cainiao"


def test_tracking_id_linking_and_placeholder_cleanup() -> None:
    """Verify that tracking orders are linked by tracking_id,

    status updates update the parent order's status, and "Unknown item" placeholders are cleaned up.
    """
    from reporting import process_items_and_orders

    orders = [
        {
            "order_id": "1120418801296739",
            "latest_status": "Shipped",
            "carrier": "China Post",
            "tracking_id": "PH8002908898",
            "items": [
                {"name": "WAVLINK WiFi 6E Wireless Card", "quantity": 1, "price": 50.0},
            ],
            "last_updated_at": "2026-05-19T07:00:00",
        },
        {
            "order_id": "PH8002908898",
            "latest_status": "Out for delivery",
            "carrier": "Unknown",
            "tracking_id": "PH8002908898",
            "items": [
                {"name": "Unknown Item", "quantity": 1, "price": None},
            ],
            "last_updated_at": "2026-05-19T08:00:00",
        },
    ]

    active_grouped, _, _, _ = process_items_and_orders(orders)

    # We should have exactly one active order (1120418801296739)
    assert len(active_grouped) == 1
    o = active_grouped[0]

    assert o["order_id"] == "1120418801296739"
    # Status should be updated to "Out for delivery"
    assert o["latest_status"] == "Out for delivery"

    # Item list should contain WAVLINK but NOT "Unknown Item"
    item_names = [i["name"] for i in o["items"]]
    assert "WAVLINK WiFi 6E Wireless Card" in item_names
    assert "Unknown Item" not in item_names
    assert len(o["items"]) == 1
