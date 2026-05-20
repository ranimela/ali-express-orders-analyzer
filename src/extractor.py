"""AliExpress Email Data Extractor using Gemini API.

Leverages the modern google-genai SDK to parse email text and return structured,
type-safe JSON records matching strict Pydantic schemas.
"""

import time

from google import genai
from google.genai import types
from pydantic import BaseModel, Field


class OrderItem(BaseModel):
    """Schema representing an individual item in an order."""

    name: str = Field(description="Name/title of the item purchased")
    quantity: int = Field(default=1, description="Quantity purchased")
    price: float | None = Field(None, description="Price of the item (as a float)")


class AliExpressOrder(BaseModel):
    """Schema representing structured AliExpress order details."""

    order_id: str = Field(
        description="The AliExpress Order ID (typically a long number)"
    )
    tracking_id: str | None = Field(
        None, description="Shipment tracking number/ID if present in the email"
    )
    carrier: str | None = Field(
        None,
        description="Logistics carrier/company name (e.g. Cainiao, China Post, DHL, FedEx) if present",
    )
    items: list[OrderItem] = Field(
        description="List of items parsed from the order confirmation or shipping email"
    )
    order_date: str | None = Field(
        None, description="Date the order was placed or email sent, if visible"
    )
    status: str = Field(
        description="Latest shipment status. MUST be one of: 'Confirmed', 'Shipped', 'Out for delivery', 'Delivered', 'Cancelled', or 'Unknown'"
    )


def get_gemini_client() -> genai.Client:
    """Initialize and return the Google GenAI client.

    Retrieves the API key from the environment variables.

    Returns:
        genai.Client instance.
    """
    # client = genai.Client() will automatically load the GEMINI_API_KEY from environment
    return genai.Client()


def extract_order_from_email(
    email_text: str, client: genai.Client | None = None
) -> AliExpressOrder | None:
    """Analyze email content and extract structured AliExpress order details.

    Args:
        email_text: Raw plain text body of the email.
        client: Optional pre-configured GenAI client.

    Returns:
        AliExpressOrder Pydantic object, or None if extraction fails.
    """
    if not email_text.strip():
        return None

    if client is None:
        client = get_gemini_client()

    prompt = f"""
    You are an expert logistics email parsing assistant. Your task is to analyze the following email text from AliExpress and extract structured information about the purchase order.

    Analyze the email content carefully and extract:
    1. Order ID (usually a long sequence of digits, e.g. 3023485749204928).
    2. Tracking ID / tracking number (if the email states the order has shipped or is in transit).
    3. Shipping carrier / logistics company (e.g. Cainiao, USPS, China Post, Israel Post, etc.).
    4. List of items: For each item, extract the exact name, quantity, and price (if available).
    5. Order date (when the email was received or order placed).
    6. Status of the order:
       - Use "Confirmed" if the order was just paid/confirmed.
       - Use "Shipped" if the email says the order has been shipped or tracking has been updated.
       - Use "Out for delivery" if it is out for local delivery.
        - Use "Delivered" if it is confirmed delivered, or if the email is an order review request/feedback email (e.g. "how did it go?", "Review your order").
       - Use "Cancelled" if the order was cancelled.
       - Use "Unknown" if it's unclear.

    Here is the email text:
    ---
    {email_text}
    ---
    """

    models_to_try = [
        "gemini-flash-lite-latest",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-2.5-flash",
    ]

    for model_name in models_to_try:
        max_retries = 3
        base_delay = 30.0

        for attempt in range(1, max_retries + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=AliExpressOrder,
                        temperature=0.1,  # low temperature for maximum reliability/accuracy
                    ),
                )

                # Parse the JSON response back into the Pydantic model
                if response.text:
                    return AliExpressOrder.model_validate_json(response.text)
                return None

            except Exception as e:
                err_str = str(e)
                # Check for daily rate limit exhaustion
                if "GenerateRequestsPerDay" in err_str or "limit: 0" in err_str:
                    print(
                        f"   [DAILY QUOTA EXHAUSTED] {model_name} daily limit hit. Trying fallback model..."
                    )
                    break  # Break retry loop to try the next model

                if any(
                    k in err_str
                    for k in ["429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE"]
                ):
                    if attempt < max_retries:
                        print(
                            f"   [TEMPORARY ERROR] Gemini API temporarily busy or rate limited ({err_str[:80]}...). Waiting {base_delay}s before retry (Attempt {attempt}/{max_retries})..."
                        )
                        time.sleep(base_delay)
                        base_delay += 10.0  # increase backoff slightly
                        continue

                print(
                    f"Error during Gemini structured order extraction ({model_name}): {e}"
                )
                break

    return None
