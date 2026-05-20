"""Main Orchestrator CLI.

Interlaces IMAP Email Ingestion, Gemini Extraction, Local Database persistence,
and Report Dispatching into a cohesive pipeline.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from database import (
    find_matching_order_for_item,
    get_active_tracking_ids,
    get_all_orders,
    initialize_database,
    is_email_processed,
    mark_email_as_processed,
    update_status_from_tracker,
    upsert_order,
)
from dispatcher import (
    dispatch_html_report,
    dispatch_local_report,
    mark_imap_emails_as_read,
    print_console_summary,
)
from extractor import extract_order_from_email, get_gemini_client
from ingestion import fetch_imap_emails
from reporting import build_html_report, build_markdown_report, process_items_and_orders
from tracker import fetch_all_tracking


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        argparse.Namespace with selected flags.
    """
    parser = argparse.ArgumentParser(
        description="AliExpress IMAP Order Analyzer & Tracker"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run ingestion and Gemini extraction, printing results without saving to DB or marking emails as read.",
    )
    parser.add_argument(
        "--query",
        type=str,
        default='(FROM "aliexpress" UNSEEN)',
        help="Custom IMAP search query (default: '(FROM \"aliexpress\" UNSEEN)').",
    )
    return parser.parse_args()


def main() -> None:
    """Main execution orchestrator."""
    # 1. Load local environment configuration (.env file)
    load_dotenv()

    args = parse_arguments()

    # Check for Gmail IMAP Credentials
    email_user = os.environ.get("EMAIL_USER")
    email_password = os.environ.get("EMAIL_PASSWORD")

    if not email_user or not email_password:
        print(
            "[ERROR] EMAIL_USER or EMAIL_PASSWORD environment variables are not set.\n"
            "Please check that your .env file exists and contains:\n"
            "EMAIL_USER=your_gmail_address@gmail.com\n"
            "EMAIL_PASSWORD=your_16_character_app_password",
            file=sys.stderr,
        )
        sys.exit(1)

    # Check for Gemini API key
    if not os.environ.get("GEMINI_API_KEY"):
        print(
            "[ERROR] GEMINI_API_KEY environment variable is not set.\n"
            "Please check that your .env file exists and contains: GEMINI_API_KEY=your_api_key",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Initialize local order tracking database
    db_file = Path("orders.db")
    initialize_database(db_file)

    print("Connecting to Gemini API...")
    try:
        gemini_client = get_gemini_client()
    except Exception as e:
        print(f"[ERROR] Failed to connect to Gemini: {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Ingest raw target emails via IMAP
    from datetime import datetime, timedelta

    cutoff_date = (datetime.now() - timedelta(days=30)).strftime("%d-%b-%Y")

    query = args.query
    if "SINCE" not in query.upper():
        if query.startswith("(") and query.endswith(")"):
            inner = query[1:-1].strip()
            query = f"({inner} SINCE {cutoff_date})"
        else:
            query = f"{query} SINCE {cutoff_date}"

    print(f"Connecting to Gmail IMAP and searching query: {query}...")
    emails = fetch_imap_emails(
        username=email_user,
        password=email_password,
        search_query=query,
    )

    if not emails:
        print("No new emails found matching the search query.")
    else:
        print(f"Found {len(emails)} email(s). Commencing Gemini extraction...")

    processed_ids: list[str] = []

    # 4. Extract order details and updates using Gemini
    for email in emails:
        print("-" * 50)
        print(f"Email ID: {email['message_id']}")
        print(f"Subject:  {email['subject']}")
        print(f"From:     {email['sender']}")

        # Skip if already successfully processed in a prior run
        if is_email_processed(email["message_id"], db_path=db_file):
            print("   [INFO] Email already processed and recorded. Skipping.")
            # Ensure it is in processed_ids so we mark it as read in IMAP if it got unmarked
            processed_ids.append(email["message_id"])
            continue

        # Extract structured Pydantic object
        order_record = extract_order_from_email(email["body_text"], gemini_client)

        if not order_record:
            print(
                "[WARNING] Extraction Failed: Gemini was unable to parse structured order data from this email."
            )
            # Skip this email, do NOT mark it as read so the user can verify
            continue

        print("[SUCCESS] Extraction Successful!")
        print(f"   Order ID:    {order_record.order_id}")
        print(f"   Status:      {order_record.status}")
        print(f"   Tracking ID: {order_record.tracking_id}")
        print(f"   Items:       {len(order_record.items)} item(s) detected")

        if args.dry_run:
            print("   [Dry Run] Database updates and mark-read skipped.")
        else:
            # Check if order ID is generic and has no tracking ID
            is_generic = order_record.order_id.lower().strip() in [
                "unknown",
                "not provided",
                "none",
                "",
            ]
            if is_generic and not order_record.tracking_id:
                # We have a generic order with no tracking ID (e.g. summary update email)
                # Let's map individual items to existing orders in the database
                matched_orders = {}
                unmatched_items = []
                for item in order_record.items:
                    matched_id = find_matching_order_for_item(
                        item.name, db_path=db_file
                    )
                    if matched_id:
                        if matched_id not in matched_orders:
                            matched_orders[matched_id] = []
                        matched_orders[matched_id].append(item)
                    else:
                        unmatched_items.append(item)

                if matched_orders:
                    print(
                        f"   [INFO] Resolved {len(matched_orders)} item(s) to existing orders."
                    )

                # Upsert matched groups to their respective orders
                for matched_id, items_list in matched_orders.items():
                    items_dict_list = [it.model_dump() for it in items_list]
                    upsert_order(
                        order_id=matched_id,
                        items=items_dict_list,
                        status=order_record.status,
                        tracking_id=order_record.tracking_id,
                        carrier=order_record.carrier,
                        db_path=db_file,
                    )

                # Store any unmatched items under a single generic order ID
                if unmatched_items:
                    items_dict_list = [it.model_dump() for it in unmatched_items]
                    upsert_order(
                        order_id=order_record.order_id,
                        items=items_dict_list,
                        status=order_record.status,
                        tracking_id=order_record.tracking_id,
                        carrier=order_record.carrier,
                        db_path=db_file,
                    )
            else:
                # Standard single-order path
                items_list = [item.model_dump() for item in order_record.items]
                status_changed = upsert_order(
                    order_id=order_record.order_id,
                    items=items_list,
                    status=order_record.status,
                    tracking_id=order_record.tracking_id,
                    carrier=order_record.carrier,
                    db_path=db_file,
                )
                if status_changed:
                    print("   Status change detected and recorded in database history.")
                else:
                    print(
                        "   Order state matched existing database record. Saved update."
                    )

            # Record email ID to database processed emails list
            mark_email_as_processed(email["message_id"], db_path=db_file)

            # Record email ID to mark as read
            processed_ids.append(email["message_id"])

    # 5. Finalization & Dispatching
    if args.dry_run:
        print("\n" + "=" * 50)
        print("[DRY RUN COMPLETE] No emails were marked as read, no reports saved.")
        print("=" * 50)
    else:
        # Mark successfully processed emails as read
        if processed_ids:
            print(
                f"\nMarking {len(processed_ids)} processed email(s) as read in Gmail..."
            )
            mark_imap_emails_as_read(email_user, email_password, processed_ids)

        # 5a. Live tracking refresh via parcelsapp.com
        active_pairs = get_active_tracking_ids(db_file)
        if active_pairs:
            # Deduplicate tracking IDs (multiple orders may share same tracking ID)
            seen: dict[str, str] = {}
            for oid, tid in active_pairs:
                if tid not in seen:
                    seen[tid] = oid
            unique_ids = list(seen.keys())

            print(
                f"\nFetching live tracking status for {len(unique_ids)} shipment(s) from parcelsapp.com..."
            )
            tracking_results = fetch_all_tracking(unique_ids)

            for tid, result in tracking_results.items():
                if result.error:
                    print(f"  [Tracker] {tid} -> skipped ({result.error})")
                    continue
                # Apply to all orders that use this tracking ID
                for oid, t in active_pairs:
                    if t == tid:
                        changed = update_status_from_tracker(
                            order_id=oid,
                            new_status=result.status,
                            new_carrier=result.carrier,
                            new_event_text=result.latest_event,
                            db_path=db_file,
                        )
                        if changed:
                            print(
                                f"  [Tracker] {oid} status updated to: {result.status}"
                            )
        else:
            print("\nNo active tracking IDs found for live update.")

        # Retrieve all tracked orders from database to compile the report dashboard
        all_orders = get_all_orders(db_file)

        if all_orders:
            # Build and write the daily Markdown report
            report_md = build_markdown_report(all_orders)
            dispatch_local_report(report_md)

            # Build and write the premium daily HTML report
            report_html = build_html_report(all_orders)
            dispatch_html_report(report_html)

            # Display active summary table on terminal
            print_console_summary(all_orders)

            # Send ntfy notification if there are open orders
            grouped_active, active_count, _, _ = process_items_and_orders(all_orders)
            ntfy_topic = os.environ.get("NTFY_TOPIC")
            if ntfy_topic and active_count > 0:
                repo_url = os.environ.get(
                    "GITHUB_REPOSITORY_URL",
                    "https://github.com/ranimela/ali-express-orders-analyzer",
                )
                from notifier import send_ntfy_notification

                send_ntfy_notification(ntfy_topic, grouped_active, repo_url)
        else:
            print("\nNo tracked orders currently exist in the database.")


if __name__ == "__main__":
    main()
