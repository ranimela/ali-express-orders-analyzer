# AliExpress Gmail Order Analyzer

An autonomous Python script that connects to the Gmail API to retrieve unread AliExpress transactional emails, extracts tracking IDs, carriers, item descriptions, and status updates using the **Google Gemini API** (via the modern `google-genai` SDK with strict Pydantic parsing), stores the status history locally in a SQLite database, and creates elegant daily Markdown summaries.

## Setup & Running

1. Install dependencies using `uv`:
   ```bash
   uv sync
   ```
2. Retrieve your Google Cloud `credentials.json` by following the [Implementation Plan](file:///C:/Users/rmelamed/.gemini/antigravity/brain/2a58b536-6a25-45e8-b21c-8065b455b689/implementation_plan.md#-step-by-step-guide-setting-up-gmail-api-credentials-100-free) and place it in the root folder.
3. Ensure `.env` contains your `GEMINI_API_KEY`.
4. Run the auth check to link your Gmail account:
   ```bash
   uv run src/main.py --auth
   ```
5. Run the daily status checks:
   ```bash
   uv run src/main.py
   ```
