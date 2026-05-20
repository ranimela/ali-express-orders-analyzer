"""Report Dispatcher and Email State Manager module.

Archives processed email messages (marking them as read in the Gmail inbox via IMAP)
to prevent double processing, and delivers formatted Markdown reports locally.
"""

import imaplib
import socket
from datetime import datetime
from pathlib import Path
from typing import Any

socket.setdefaulttimeout(15.0)


def mark_imap_emails_as_read(
    username: str,
    password: str,
    message_ids: list[str],
    imap_server: str = "imap.gmail.com",
) -> bool:
    """Mark successfully processed email message IDs as read/seen in the mailbox via IMAP.

    Args:
        username: User's email address.
        password: User's 16-character App Password.
        message_ids: List of IMAP email message IDs.
        imap_server: Address of the IMAP mail server.

    Returns:
        True if marking read succeeded, False otherwise.
    """
    if not message_ids:
        return True

    try:
        clean_password = password.replace(" ", "")
        mail = imaplib.IMAP4_SSL(imap_server, timeout=15.0)
        mail.login(username, clean_password)
        mail.select("inbox")

        for mail_id in message_ids:
            # Store '\Seen' flag to mark message as read
            mail.store(mail_id, "+FLAGS", "\\Seen")
        return True
    except Exception as e:
        print(f"Error marking IMAP emails as read: {e}")
        return False
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def dispatch_local_report(
    report_content: str, reports_dir: Path = Path("reports")
) -> Path:
    """Save the daily Markdown report to a local gitignored reports directory.

    Args:
        report_content: Complete formatted Markdown report text.
        reports_dir: Directory where reports will be saved.

    Returns:
        Path object pointing to the written file.
    """
    # Create the reports directory if it doesn't exist
    reports_dir.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime("%Y-%m-%d")
    report_file = reports_dir / f"daily_report_{today_str}.md"

    # Write the report with utf-8 encoding to support emojis and descriptions safely
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"Daily report saved locally to: {report_file}")
    return report_file


def dispatch_html_report(
    html_content: str, reports_dir: Path = Path("reports")
) -> Path:
    """Save the daily HTML report dashboard locally and create a static latest link.

    Args:
        html_content: Complete formatted HTML report text.
        reports_dir: Directory where reports will be saved.

    Returns:
        Path object pointing to the written dated file.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime("%Y-%m-%d")
    report_file = reports_dir / f"daily_report_{today_str}.html"

    # Save the dated report
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Save a convenient "latest_report.html" copy
    latest_file = reports_dir / "latest_report.html"
    with open(latest_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"Daily HTML report saved locally to: {report_file}")
    print(f"Latest HTML dashboard available at: {latest_file}")
    return report_file


def print_console_summary(orders: list[dict[str, Any]]) -> None:
    """Output a clean, concise ASCII table summary of current orders to the console.

    Args:
        orders: List of order dictionaries from the database.
    """
    if not orders:
        print("\nNo orders found in database to display.")
        return

    print("\n" + "=" * 80)
    print(
        f"| {'ORDER ID':<18} | {'STATUS':<15} | {'CARRIER':<15} | {'TRACKING ID':<22} |"
    )
    print("=" * 80)

    for order in orders:
        o_id = order["order_id"]
        status = order["latest_status"]
        carrier = order["carrier"] if order["carrier"] else "N/A"
        tracking = order["tracking_id"] if order["tracking_id"] else "N/A"

        # Trim long strings
        if len(carrier) > 15:
            carrier = carrier[:12] + "..."
        if len(tracking) > 22:
            tracking = tracking[:19] + "..."

        print(f"| {o_id:<18} | {status:<15} | {carrier:<15} | {tracking:<22} |")

    print("=" * 80 + "\n")
