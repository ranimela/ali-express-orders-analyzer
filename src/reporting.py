"""Report Formatting module.

Compiles order states and histories into a structured and premium Markdown report.
"""

from datetime import datetime
from typing import Any


def format_currency(val: float | None) -> str:
    """Format currency values cleanly.

    Args:
        val: Optional price value.

    Returns:
        Formatted currency string.
    """
    if val is None:
        return "N/A"
    return f"₪{val:.2f}"


def process_items_and_orders(orders: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int, float]:
    """Process order database records to extract unique physical items,
    resolve their latest statuses, filter out completed items,
    and group the remaining active items by their definitive orders.

    Args:
        orders: List of raw order dictionaries from the database.

    Returns:
        Tuple containing:
          - List of active grouped order dictionaries (each with order_id, latest_status, carrier, tracking_id, items)
          - Count of active orders
          - Count of completed orders
          - Total spend (active + completed) calculated without duplication
    """
    import json

    status_precedence = {
        "unknown": 0,
        "confirmed": 1,
        "shipped": 2,
        "out for delivery": 3,
        "delivered": 4
    }

    def clean_item_name(name: str) -> str:
        return name.replace("...", "").strip().lower()

    def is_same_item(name1: str, name2: str) -> bool:
        c1 = clean_item_name(name1)
        c2 = clean_item_name(name2)
        if not c1 or not c2:
            return False
        if c1 == c2:
            return True
        if len(c1) >= 10 and len(c2) >= 10:
            if c1.startswith(c2) or c2.startswith(c1):
                return True
            if len(c1) >= 20 and len(c2) >= 20 and c1[:20] == c2[:20]:
                return True
        return False

    def is_real_order_id(oid: str) -> bool:
        clean_oid = oid.strip()
        return clean_oid.isdigit() and len(clean_oid) >= 15

    # Step 1: Pass 1 - Resolve target_order_id for each item instance using name/size-diff matching
    item_instances = []
    for order in orders:
        oid = order["order_id"]
        is_gen = not is_real_order_id(oid)
        items_list = order.get("items", [])
        order_track = order.get("tracking_id")
        
        for item in items_list:
            target_oid = oid
            linked_real_order = None
            
            if is_gen:
                # Try raw tracking ID matches first
                for ro in orders:
                    if not is_real_order_id(ro["order_id"]):
                        continue
                    ro_track = ro.get("tracking_id")
                    if (oid and ro_track == oid) or (order_track and ro_track == order_track):
                        linked_real_order = ro["order_id"]
                        break
            
            if linked_real_order:
                target_oid = linked_real_order
            else:
                matches = []
                for ro in orders:
                    ro_id = ro["order_id"]
                    if not is_real_order_id(ro_id):
                        continue
                    ro_items = ro.get("items", [])
                    
                    # Check if item exists in ro
                    has_item = False
                    for ri in ro_items:
                        if is_same_item(item.get("name", ""), ri.get("name", "")):
                            has_item = True
                            break
                            
                    if has_item:
                        if not is_gen:
                            if len(ro_items) > len(items_list) and ro_id[:5] == oid[:5]:
                                matches.append((ro_id, len(ro_items)))
                        else:
                            matches.append((ro_id, len(ro_items)))
                
                if matches:
                    if not is_gen:
                        matches.sort(key=lambda x: x[1], reverse=True)
                        target_oid = matches[0][0]
                    else:
                        fit_matches = []
                        for ro_id, ro_len in matches:
                            ro = next(x for x in orders if x["order_id"] == ro_id)
                            score = 0
                            for gi in items_list:
                                for ri in ro.get("items", []):
                                    if is_same_item(gi.get("name", ""), ri.get("name", "")):
                                        score += 1
                                        break
                            size_diff = abs(ro_len - len(items_list))
                            fit_matches.append((ro_id, score, size_diff, ro_len))
                        fit_matches.sort(key=lambda x: (x[1], -x[2], x[3]), reverse=True)
                        target_oid = fit_matches[0][0]

            item_instances.append({
                "item_name": item.get("name", "Unknown Item"),
                "quantity": item.get("quantity", 1),
                "price": item.get("price"),
                "target_order_id": target_oid,
                "orig_order_id": oid,
                "status": order.get("latest_status", "Unknown"),
                "carrier": order.get("carrier"),
                "tracking_id": order.get("tracking_id"),
                "last_updated_at": order.get("last_updated_at", "")
            })

    # Pass 2: Build tracking-to-real map from resolved instances, and link placeholder items
    tracking_to_real_map = {}
    for inst in item_instances:
        toid = inst["target_order_id"]
        if is_real_order_id(toid):
            orig = inst["orig_order_id"]
            track = inst["tracking_id"]
            if not is_real_order_id(orig):
                tracking_to_real_map[orig] = toid
            if track:
                tracking_to_real_map[track] = toid

    # Update any instances that resolved to generic IDs if we now have a mapping
    for inst in item_instances:
        toid = inst["target_order_id"]
        if not is_real_order_id(toid):
            orig = inst["orig_order_id"]
            track = inst["tracking_id"]
            if toid in tracking_to_real_map:
                inst["target_order_id"] = tracking_to_real_map[toid]
            elif orig in tracking_to_real_map:
                inst["target_order_id"] = tracking_to_real_map[orig]
            elif track in tracking_to_real_map:
                inst["target_order_id"] = tracking_to_real_map[track]


    # Step 1.5: Track highest status precedence for each target order ID
    order_statuses = {}
    for inst in item_instances:
        toid = inst["target_order_id"]
        status = inst["status"]
        if toid not in order_statuses:
            order_statuses[toid] = status
        else:
            p1 = status_precedence.get(order_statuses[toid].lower(), 0)
            p2 = status_precedence.get(status.lower(), 0)
            if p2 > p1:
                order_statuses[toid] = status

    # Step 2: Group by (clean_item_name, target_order_id) to form unique items
    groups = {}
    for inst in item_instances:
        clean_name = clean_item_name(inst["item_name"])
        group_key = None
        for k in groups.keys():
            if is_same_item(clean_name, k[0]) and inst["target_order_id"] == k[1]:
                group_key = k
                break
        if group_key is None:
            group_key = (inst["item_name"], inst["target_order_id"])
            groups[group_key] = []
        groups[group_key].append(inst)

    # Step 3: Find definitive status/details for each group
    unique_items = []
    for (item_name, target_oid), insts in groups.items():
        def get_sort_key(inst):
            prec = status_precedence.get(inst["status"].lower(), 0)
            has_tracking = 1 if inst["tracking_id"] else 0
            has_carrier = 1 if inst["carrier"] else 0
            return (prec, has_tracking, has_carrier, inst["last_updated_at"])
            
        insts.sort(key=get_sort_key, reverse=True)
        def_inst = insts[0]
        
        max_qty = max(i["quantity"] for i in insts)
        longest_name = max((i["item_name"] for i in insts), key=len)
        prices = [i["price"] for i in insts if i["price"] is not None]
        price = prices[0] if prices else None
        
        unique_items.append({
            "name": longest_name,
            "quantity": max_qty,
            "price": price,
            "target_order_id": target_oid,
            "status": def_inst["status"],
            "carrier": def_inst["carrier"],
            "tracking_id": def_inst["tracking_id"]
        })

    # Step 4: Calculate stats and group active items
    total_spend = sum(ui.get("quantity", 1) * ui["price"] for ui in unique_items if ui.get("price") is not None)
    
    # Filter active items based on target order status
    active_items = []
    for ui in unique_items:
        toid = ui["target_order_id"]
        overall_status = order_statuses.get(toid, ui["status"]).lower()
        if overall_status not in ("delivered", "completed", "complete"):
            active_items.append(ui)
    
    grouped_orders = {}
    for ui in active_items:
        oid = ui["target_order_id"]
        if oid not in grouped_orders:
            orig_order = next((o for o in orders if o["order_id"] == oid), None)
            status = order_statuses.get(oid, orig_order["latest_status"] if orig_order else ui["status"])
            
            grouped_orders[oid] = {
                "order_id": oid,
                "latest_status": status,
                "carrier": None,
                "tracking_id": None,
                "items": []
            }
        grouped_orders[oid]["items"].append(ui)

    # Discard "Unknown Item" placeholder if there are other named items in the same order
    for oid, o in grouped_orders.items():
        items = o["items"]
        has_real_items = any(item["name"].lower() != "unknown item" for item in items)
        if has_real_items:
            o["items"] = [item for item in items if item["name"].lower() != "unknown item"]

    # Resolve definitive tracking and carrier for active orders
    for oid, o in grouped_orders.items():
        orig_order = next((x for x in orders if x["order_id"] == oid), None)
        
        t_id = orig_order["tracking_id"] if (orig_order and orig_order["tracking_id"]) else None
        if not t_id:
            for item in o["items"]:
                if item.get("tracking_id"):
                    t_id = item["tracking_id"]
                    break
                    
        c_name = orig_order["carrier"] if (orig_order and orig_order["carrier"]) else None
        if not c_name:
            for item in o["items"]:
                if item.get("carrier"):
                    c_name = item["carrier"]
                    break
                    
        event_text = orig_order.get("latest_event_text") if orig_order else None
        if not event_text and t_id:
            for ro in orders:
                if ro.get("tracking_id") == t_id and ro.get("latest_event_text"):
                    event_text = ro["latest_event_text"]
                    break
                    
        o["tracking_id"] = t_id
        o["carrier"] = c_name
        o["latest_event_text"] = event_text

    active_order_ids = set(grouped_orders.keys())
    completed_order_ids = set()
    for o in orders:
        oid = o["order_id"]
        if oid not in active_order_ids:
            if o["latest_status"].lower() in ("delivered", "completed", "complete"):
                completed_order_ids.add(oid)

    return list(grouped_orders.values()), len(active_order_ids), len(completed_order_ids), total_spend



def build_markdown_report(orders: list[dict[str, Any]]) -> str:
    """Construct a beautiful, user-ready Markdown report from stored orders.

    Args:
        orders: List of order dictionaries from the database.

    Returns:
        Markdown-formatted report string.
    """
    today_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    active_orders, total_active, total_completed, total_spend = process_items_and_orders(orders)
    
    report_lines = [
        "# 📦 AliExpress Order Status Dashboard",
        f"Generated on: `{today_str}`",
        "",
        "## 📊 Summary",
        f"- 🚚 **Active/In-Transit Orders:** {total_active}",
        "",
        "---",
        "",
    ]

    # --- Active Orders Section ---
    report_lines.append("## 🚚 Active Orders")
    report_lines.append("")

    if not active_orders:
        report_lines.append("*No active orders currently in transit.*")
        report_lines.append("")
    else:
        status_priority = {
            "out for delivery": 0,
            "shipped": 1,
            "confirmed": 2,
            "unknown": 3
        }
        active_orders.sort(key=lambda x: status_priority.get(x["latest_status"].lower(), 99))

        for idx, order in enumerate(active_orders, 1):
            items_lines = []
            total_price = 0.0
            price_available = True

            for item in order.get("items", []):
                name = item.get("name", "Unknown Item")
                qty = item.get("quantity", 1)
                price = item.get("price")

                if price is not None:
                    total_price += price * qty
                    price_str = format_currency(price)
                    items_lines.append(f"  - **{name}** (x{qty}) - {price_str}")
                else:
                    price_available = False
                    items_lines.append(f"  - **{name}** (x{qty})")

            tracking_str = (
                f"`{order['tracking_id']}`"
                if order["tracking_id"]
                else "*Not available yet*"
            )
            carrier_str = (
                order["carrier"] if order["carrier"] else "*Not available yet*"
            )

            status_badge = "🔵 Confirmed"
            status_lower = order["latest_status"].lower()
            if "shipped" in status_lower:
                status_badge = "🚚 Shipped"
            elif "delivery" in status_lower:
                status_badge = "📦 Out for Delivery"
            elif "cancelled" in status_lower:
                status_badge = "🔴 Cancelled"

            clean_oid = order["order_id"].replace("TRACKING_", "").replace("GENERIC_", "")
            report_lines.append(f"### {idx}. Order ID: `{clean_oid}`")
            report_lines.append(f"- **Status:** **{status_badge}**")
            report_lines.append(f"- **Tracking ID:** {tracking_str}")
            report_lines.append(f"- **Logistics Carrier:** {carrier_str}")
            report_lines.append("- **Items Purchased:**")
            report_lines.extend(items_lines)

            if price_available and total_price > 0:
                report_lines.append(
                    f"- **Total Price:** **{format_currency(total_price)}**"
                )

            last_updated = ""
            for item in order.get("items", []):
                orig = next((o for o in orders if o["order_id"] == order["order_id"]), None)
                if orig:
                    last_updated = orig["last_updated_at"]
                    break
            
            if last_updated:
                try:
                    dt = datetime.fromisoformat(last_updated)
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                    report_lines.append(f"- **Last Updated:** *{formatted_time}*")
                except Exception:
                    report_lines.append(f"- **Last Updated:** *{last_updated}*")

            report_lines.append("")
            report_lines.append("---")
            report_lines.append("")

    return "\n".join(report_lines)



def build_html_report(orders: list[dict[str, Any]]) -> str:
    """Construct an incredibly gorgeous, glassmorphic HTML report dashboard from tracked orders.

    Presents active orders in a table, sorted by their tracking status, 
    and excludes items details entirely for completed/delivered orders.

    Args:
        orders: List of order dictionaries from the database.

    Returns:
        Premium single-page HTML dashboard string.
    """
    from datetime import datetime

    today_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Define tracking status priority (Out for Delivery -> Shipped -> Confirmed -> Unknown)
    status_priority = {
        "out for delivery": 0,
        "shipped": 1,
        "confirmed": 2,
        "unknown": 3
    }

    active_orders, total_active_orders, total_completed_orders, total_spend = process_items_and_orders(orders)

    # Sort active orders by status priority
    active_orders.sort(key=lambda x: status_priority.get(x["latest_status"].lower(), 99))

    # 1. Main Styles and HTML Document structure
    html_lines = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '    <meta charset="UTF-8">',
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0">',
        "    <title>📦 AliExpress Orders Analyzer Dashboard</title>",
        '    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">',
        "    <style>",
        "        :root {",
        "            --bg-primary: #f8fafc;",
        "            --bg-secondary: none;",
        "            --glass-bg: #ffffff;",
        "            --glass-border: #e2e8f0;",
        "            --glass-glow: rgba(99, 102, 241, 0.04);",
        "            --text-primary: #0f172a;",
        "            --text-muted: #64748b;",
        "            --color-emerald: #16a34a;",
        "            --color-amber: #d97706;",
        "            --color-sky: #0284c7;",
        "            --color-indigo: #4f46e5;",
        "        }",
        "        ",
        "        * {",
        "            box-sizing: border-box;",
        "            margin: 0;",
        "            padding: 0;",
        "        }",
        "        ",
        "        body {",
        "            font-family: 'Outfit', sans-serif;",
        "            background: var(--bg-primary);",
        "            color: var(--text-primary);",
        "            min-height: 100vh;",
        "            padding: 2.5rem 1.5rem;",
        "            line-height: 1.5;",
        "            -webkit-font-smoothing: antialiased;",
        "        }",
        "        ",
        "        .container {",
        "            max-width: 1200px;",
        "            margin: 0 auto;",
        "        }",
        "        ",
        "        header {",
        "            display: flex;",
        "            justify-content: space-between;",
        "            align-items: center;",
        "            margin-bottom: 2.5rem;",
        "            padding-bottom: 1.5rem;",
        "            border-bottom: 1px solid var(--glass-border);",
        "        }",
        "        ",
        "        .header-title h1 {",
        "            font-size: 2rem;",
        "            font-weight: 700;",
        "            background: linear-gradient(to right, #4f46e5, #818cf8);",
        "            -webkit-background-clip: text;",
        "            -webkit-text-fill-color: transparent;",
        "            margin-bottom: 0.25rem;",
        "        }",
        "        ",
        "        .header-title p {",
        "            color: var(--text-muted);",
        "            font-size: 0.9rem;",
        "        }",
        "        ",
        "        /* Stats Section */",
        "        .stats-grid {",
        "            display: grid;",
        "            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));",
        "            gap: 1.25rem;",
        "            margin-bottom: 2.5rem;",
        "        }",
        "        ",
        "        .stat-card {",
        "            background: var(--glass-bg);",
        "            border: 1px solid var(--glass-border);",
        "            border-radius: 1rem;",
        "            padding: 1.25rem 1.5rem;",
        "            box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px 0 rgba(0, 0, 0, 0.03);",
        "            transition: transform 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease;",
        "        }",
        "        ",
        "        .stat-card:hover {",
        "            transform: translateY(-2px);",
        "            border-color: rgba(99, 102, 241, 0.25);",
        "            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);",
        "        }",
        "        ",
        "        .stat-label {",
        "            color: var(--text-muted);",
        "            font-size: 0.8rem;",
        "            font-weight: 600;",
        "            text-transform: uppercase;",
        "            letter-spacing: 0.05em;",
        "            margin-bottom: 0.35rem;",
        "        }",
        "        ",
        "        .stat-value {",
        "            font-size: 1.75rem;",
        "            font-weight: 700;",
        "        }",
        "        ",
        "        /* Section headers */",
        "        .section-header {",
        "            font-size: 1.35rem;",
        "            font-weight: 600;",
        "            margin-bottom: 1.25rem;",
        "            color: var(--text-primary);",
        "            display: flex;",
        "            align-items: center;",
        "            gap: 0.5rem;",
        "        }",
        "        ",
        "        /* Badges */",
        "        .badge {",
        "            font-size: 0.72rem;",
        "            font-weight: 600;",
        "            padding: 0.25rem 0.6rem;",
        "            border-radius: 9999px;",
        "            border: 1px solid transparent;",
        "            display: inline-flex;",
        "            align-items: center;",
        "            justify-content: center;",
        "            gap: 0.25rem;",
        "            white-space: nowrap;",
        "        }",
        "        ",
        "        .badge-confirmed {",
        "            color: var(--color-sky);",
        "            background: rgba(2, 132, 199, 0.08);",
        "            border-color: rgba(2, 132, 199, 0.15);",
        "        }",
        "        ",
        "        .badge-shipped {",
        "            color: var(--color-amber);",
        "            background: rgba(217, 119, 6, 0.08);",
        "            border-color: rgba(217, 119, 6, 0.15);",
        "        }",
        "        ",
        "        .badge-delivered {",
        "            color: var(--color-emerald);",
        "            background: rgba(22, 163, 74, 0.08);",
        "            border-color: rgba(22, 163, 74, 0.15);",
        "        }",
        "        ",
        "        /* Table Layout */",
        "        .table-card {",
        "            background: var(--glass-bg);",
        "            border: 1px solid var(--glass-border);",
        "            border-radius: 1rem;",
        "            padding: 1.25rem;",
        "            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);",
        "            overflow-x: auto;",
        "            margin-bottom: 3.5rem;",
        "        }",
        "        ",
        "        table {",
        "            width: 100%;",
        "            border-collapse: collapse;",
        "            text-align: left;",
        "        }",
        "        ",
        "        th {",
        "            padding: 0.5rem 1rem;",
        "            font-size: 0.75rem;",
        "            font-weight: 600;",
        "            color: var(--text-muted);",
        "            text-transform: uppercase;",
        "            letter-spacing: 0.05em;",
        "            border-bottom: 1px solid var(--glass-border);",
        "            white-space: nowrap;",
        "            vertical-align: middle;",
        "        }",
        "        ",
        "        td {",
        "            padding: 0.75rem 1rem;",
        "            font-size: 0.85rem;",
        "            border-bottom: 1px solid var(--glass-border);",
        "            vertical-align: middle;",
        "        }",
        "        ",
        "        tr:last-child td {",
        "            border-bottom: none;",
        "        }",
        "        ",
        "        .tbl-order-id {",
        "            font-weight: 600;",
        "            color: var(--text-primary);",
        "            white-space: nowrap;",
        "        }",
        "        ",
        "        .tbl-tracking {",
        "            font-family: monospace;",
        "            font-size: 0.8rem;",
        "            color: var(--text-primary);",
        "            white-space: nowrap;",
        "        }",
        "        ",
        "        .tbl-carrier {",
        "            white-space: nowrap;",
        "        }",
        "        ",
        "        .tbl-event-text {",
        "            font-size: 0.75rem;",
        "            color: var(--text-muted);",
        "            margin-top: 0.4rem;",
        "            max-width: 250px;",
        "            line-height: 1.2;",
        "            white-space: normal;",
        "        }",
        "        ",
        "        /* Items list inside table */",
        "        .nested-items-table {",
        "            width: 100%;",
        "            border-collapse: collapse;",
        "            margin: 0;",
        "            background: transparent;",
        "            min-width: 320px;",
        "        }",
        "        ",
        "        .nested-items-table td {",
        "            padding: 0.35rem 0.5rem;",
        "            font-size: 0.8rem;",
        "            border-bottom: 1px solid var(--glass-border);",
        "            vertical-align: middle;",
        "            background: transparent;",
        "        }",
        "        ",
        "        .nested-items-table tr:last-child td {",
        "            border-bottom: none;",
        "        }",
        "        ",
        "        .nested-item-name {",
        "            color: var(--text-primary);",
        "            font-weight: 500;",
        "            line-height: 1.3;",
        "            text-align: left;",
        "        }",
        "        ",
        "        .nested-item-qty {",
        "            color: var(--text-muted);",
        "            text-align: center;",
        "            white-space: nowrap;",
        "            width: 40px;",
        "            font-weight: 500;",
        "        }",
        "        ",
        "        .nested-item-price {",
        "            color: var(--color-indigo);",
        "            font-weight: 600;",
        "            text-align: right;",
        "            white-space: nowrap;",
        "            width: 70px;",
        "        }",
        "    </style>",
        "</head>",
        "<body>",
        '    <div class="container">',
        "        <header>",
        '            <div class="header-title">',
        "                <h1>AliExpress Order Status Dashboard</h1>",
        f"                <p>Last Sync: {today_str}</p>",
        "            </div>",
        "        </header>",
        "        <!-- Summary Stats Section -->",
        '        <div class="stats-grid">',
        '            <div class="stat-card">',
        '                <div class="stat-label">Active Orders In Transit</div>',
        f'                <div class="stat-value" style="color: var(--color-amber);">{total_active_orders}</div>',
        "            </div>",
        "        </div>",
        "        ",
        "        <!-- Active In-Transit Orders Section -->",
        '        <div class="section-header">',
        "            <span>🚚</span> Active Orders In Transit (Sorted by Status)",
        "        </div>",
        "        ",
    ]

    if not active_orders:
        html_lines.append(
            '        <div style="background: var(--glass-bg); border: 1px solid var(--glass-border); border-radius: 1rem; padding: 2rem; text-align: center; color: var(--text-muted); margin-bottom: 3.5rem;">'
        )
        html_lines.append("            No active orders currently in transit.")
        html_lines.append("        </div>")
    else:
        html_lines.extend(
            [
                '        <div class="table-card">',
                "            <table>",
                "                <thead>",
                "                    <tr>",
                "                        <th>Status</th>",
                "                        <th>Tracking ID</th>",
                "                        <th>Order ID</th>",
                "                        <th>Items Grouped</th>",
                "                        <th>Logistics Carrier</th>",
                "                        <th>Latest Update</th>",
                "                    </tr>",
                "                </thead>",
                "                <tbody>",
            ]
        )

        for order in active_orders:
            status_lower = order["latest_status"].lower()
            badge_class = "badge-confirmed"
            badge_emoji = "🔵"
            if "shipped" in status_lower:
                badge_class = "badge-shipped"
                badge_emoji = "🚚"
            elif "delivery" in status_lower:
                badge_class = "badge-shipped"
                badge_emoji = "📦"
            elif "cancelled" in status_lower:
                badge_class = "badge-confirmed"
                badge_emoji = "🔴"

            tracking = order["tracking_id"] if order["tracking_id"] else "Not available yet"
            carrier = order["carrier"] if order["carrier"] else "Not available yet"

            event_text = order.get("latest_event_text")
            event_html = f'<div class="tbl-event-text">{event_text}</div>' if event_text else ''

            html_lines.extend(
                [
                    "                    <tr>",
                    f'                        <td><span class="badge {badge_class}">{badge_emoji} {order["latest_status"]}</span></td>',
                    f'                        <td class="tbl-tracking">{tracking}</td>',
                    f'                        <td class="tbl-order-id">{order["order_id"].replace("TRACKING_", "").replace("GENERIC_", "")}</td>',
                    '                        <td>',
                    '                            <table class="nested-items-table">',
                    "                                <tbody>",
                ]
            )

            for item in order.get("items", []):
                name = item.get("name", "Unknown Item")
                qty = item.get("quantity", 1)
                price = item.get("price")
                price_tag = f"₪{price:.2f}" if price is not None else "N/A"

                html_lines.extend(
                    [
                        "                                    <tr>",
                        f'                                        <td class="nested-item-name">{name}</td>',
                        f'                                        <td class="nested-item-qty">x{qty}</td>',
                        f'                                        <td class="nested-item-price">{price_tag}</td>',
                        "                                    </tr>",
                    ]
                )

            html_lines.extend(
                [
                    "                                </tbody>",
                    "                            </table>",
                    "                        </td>",
                    f'                        <td class="tbl-carrier">{carrier}</td>',
                    f'                        <td>{event_html}</td>',
                    "                    </tr>",
                ]
            )

        html_lines.extend(
            [
                "                </tbody>",
                "            </table>",
                "        </div>",
            ]
        )

    html_lines.extend(["    </div>", "</body>", "</html>"])

    return "\n".join(html_lines)

