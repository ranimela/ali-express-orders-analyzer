"""Module to handle ntfy.sh push notifications."""

import urllib.request
from typing import Any


def send_ntfy_notification(
    topic: str, active_orders: list[dict[str, Any]], repo_url: str
) -> None:
    """Send a push notification to ntfy.sh about active orders.

    Args:
        topic: The ntfy.sh topic name.
        active_orders: A list of active orders.
        repo_url: The GitHub repository URL.
    """
    if not topic:
        return

    order_count = len(active_orders)
    if order_count == 0:
        return

    # Construct status breakdown
    status_counts: dict[str, int] = {}
    for order in active_orders:
        status = order.get("latest_status", "Unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    status_summary = ", ".join(
        f"{count} {status}" for status, count in status_counts.items()
    )

    # Build notification headers and body
    url = f"https://ntfy.sh/{topic}"
    title = f"AliExpress Tracker: {order_count} Open Order(s)"

    # We want to provide direct link to the latest report via GitHub Pages
    if "github.com/" in repo_url:
        parts = repo_url.split("github.com/")[-1].split("/")
        if len(parts) >= 2:
            username = parts[0]
            repo_name = parts[1]
            report_url = f"https://{username}.github.io/{repo_name}/reports/latest_report.html"
        else:
            report_url = f"{repo_url}/blob/main/reports/latest_report.html"
    else:
        report_url = f"{repo_url}/blob/main/reports/latest_report.html"

    # Format message body
    message = (
        f"You have {order_count} active orders on AliExpress.\n"
        f"Status: {status_summary}"
    )

    headers = {
        "Title": title,
        "Priority": "default",
        "Tags": "package,shopping_bags",
        "Actions": f"view, Full Report, {report_url}",
    }

    try:
        # Request uses UTF-8 payload
        req = urllib.request.Request(
            url, data=message.encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                print(
                    f"[Notifier] ntfy notification sent successfully to topic: {topic}"
                )
            else:
                print(f"[Notifier] Failed to send ntfy notification: {response.status}")
    except Exception as e:
        print(f"[Notifier] Error sending ntfy notification: {e}")
