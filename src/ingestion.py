"""Standard IMAP Email Ingestion module.

Connects directly to Gmail's IMAP mail servers using secure SSL and App Passwords,
downloads AliExpress emails, and extracts text content for Gemini queries.
"""

import email
import html
import imaplib
import re
import socket
from email import policy
from typing import Any

# Set a global socket timeout of 45 seconds to prevent infinite hangs on slow networks
socket.setdefaulttimeout(45.0)


def clean_html(raw_html: str) -> str:
    """Strip HTML tags and CSS/scripts to extract clean text.

    Args:
        raw_html: The raw HTML content of the email.

    Returns:
        Cleaned plain text.
    """
    # Remove head, script, and style elements completely
    text = re.sub(
        r"<(head|script|style).*?>.*?</\1>",
        "",
        raw_html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Replace structural tags with newlines/spaces
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</td>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", "\n", text, flags=re.IGNORECASE)

    # Strip all remaining HTML tags
    text = re.sub(r"<[^>]*>", " ", text)

    # Decode HTML entities (e.g., &amp; to &)
    text = html.unescape(text)

    # Clean up excessive whitespaces
    lines = [line.strip() for line in text.splitlines()]
    # Remove empty lines
    cleaned_lines = [line for line in lines if line]

    return "\n".join(cleaned_lines)


def fetch_imap_emails(
    username: str,
    password: str,
    imap_server: str = "imap.gmail.com",
    search_query: str = '(FROM "aliexpress" UNSEEN)',
    max_results: int = 150,
) -> list[dict[str, Any]]:
    """Retrieve target emails from Gmail's IMAP server using an App Password.

    Args:
        username: User's email address (e.g. ranimel@gmail.com).
        password: User's 16-character Google App Password.
        imap_server: Address of the IMAP mail server.
        search_query: Standard IMAP search command string.
        max_results: Maximum number of emails to retrieve.

    Returns:
        List of dicts with keys: message_id, subject, sender, date, body_text.
    """
    results: list[dict[str, Any]] = []

    # Strip any spaces from the App Password
    clean_password = password.replace(" ", "")

    # Connect to IMAP server using SSL with strict 45-second timeout
    try:
        mail = imaplib.IMAP4_SSL(imap_server, timeout=45.0)
        mail.login(username, clean_password)
    except Exception as e:
        print(f"[ERROR] IMAP Login failed: {e}")
        return results

    try:
        # Select active inbox
        mail.select("inbox")

        # Search matching emails
        status, data = mail.search(None, search_query)
        if status != "OK":
            print(f"[WARNING] IMAP search failed: {status}")
            return results

        mail_ids = data[0].split()
        if not mail_ids:
            return results

        # Process the latest max_results messages
        mail_ids = mail_ids[-max_results:]

        # Traverse in chronological order (oldest first) so newer statuses overwrite older ones
        for mail_id in mail_ids:
            msg_id_str = mail_id.decode("utf-8")
            try:
                status, msg_data = mail.fetch(mail_id, "(RFC822)")
                if status != "OK":
                    continue

                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        # Parse complete MIME structure
                        msg = email.message_from_bytes(
                            response_part[1], policy=policy.default
                        )

                        subject = msg.get("subject", "No Subject")
                        sender = msg.get("from", "Unknown Sender")
                        date = msg.get("date", "Unknown Date")

                        # Extract text or HTML body
                        body_text = ""
                        body_part = msg.get_body(preferencelist=("plain", "html"))
                        if body_part:
                            payload = body_part.get_content()
                            if body_part.get_content_type() == "text/html":
                                body_text = clean_html(payload)
                            else:
                                body_text = payload
                        else:
                            # Fallback: manual walk
                            for part in msg.walk():
                                content_type = part.get_content_type()
                                if content_type == "text/plain":
                                    body_text = part.get_payload(decode=True).decode(
                                        "utf-8", errors="replace"
                                    )
                                    break
                                elif content_type == "text/html":
                                    html_content = part.get_payload(decode=True).decode(
                                        "utf-8", errors="replace"
                                    )
                                    body_text = clean_html(html_content)

                        results.append(
                            {
                                "message_id": msg_id_str,
                                "subject": subject,
                                "sender": sender,
                                "date": date,
                                "body_text": body_text,
                            }
                        )
            except Exception as e:
                print(f"Error reading message ID {msg_id_str}: {e}")

    except Exception as e:
        print(f"Error during IMAP download: {e}")
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return results
