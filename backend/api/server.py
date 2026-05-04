import os
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Query  # type: ignore
from pydantic import BaseModel, field_validator  # type: ignore
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer, HTTPAuthorizationCredentials  # type: ignore
from google import genai  # type: ignore
from google.genai import types  # type: ignore
import urllib.request, urllib.parse, urllib.error
import re
import html
import sqlite3
import sendgrid
from sendgrid.helpers.mail import Mail, Email, To, Content

app = FastAPI()

cors_allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]

# Add CORS middleware (allows frontend to call backend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

clerk_config = ClerkConfig(jwks_url=os.getenv("CLERK_JWKS_URL"))
clerk_guard = ClerkHTTPBearer(clerk_config)
HISTORY_DB_PATH = os.getenv("HISTORY_DB_PATH", "consultation_history.db")
HISTORY_ENABLED = True
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
APP_VERSION = os.getenv("APP_VERSION", "local")


def _dir_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return os.access(path, os.W_OK)
    except Exception:
        return False


def resolve_history_db_path() -> str:
    """
    Prefer an explicit path, but gracefully fall back to /tmp on serverless
    platforms where the deployment directory is read-only (e.g. /var/task).
    """
    configured = (HISTORY_DB_PATH or "").strip()
    candidates = []

    if configured:
        if os.path.isabs(configured):
            candidates.append(configured)
            candidates.append(os.path.join("/tmp", os.path.basename(configured)))
        else:
            candidates.append(os.path.join("/tmp", configured))
    else:
        candidates.append("/tmp/consultation_history.db")

    candidates.append("/tmp/consultation_history.db")

    for candidate in candidates:
        parent = os.path.dirname(candidate) or "."
        if _dir_writable(parent):
            return candidate

    return "/tmp/consultation_history.db"


def get_db_connection() -> sqlite3.Connection:
    db_path = resolve_history_db_path()
    db_dir = os.path.dirname(db_path) or "."
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_history_db() -> None:
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS consultation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                patient_name TEXT NOT NULL,
                patient_email TEXT NOT NULL,
                date_of_visit TEXT NOT NULL,
                notes TEXT NOT NULL,
                summary_markdown TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        # Lightweight migration for existing tables created before `pinned` existed.
        cols = conn.execute("PRAGMA table_info(consultation_history)").fetchall()
        col_names = {col["name"] for col in cols}
        if "pinned" not in col_names:
            conn.execute(
                "ALTER TABLE consultation_history ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
            )


def save_history_item(
    user_id: str,
    patient_name: str,
    patient_email: str,
    date_of_visit: str,
    notes: str,
    summary_markdown: str,
) -> int:
    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO consultation_history
                (user_id, patient_name, patient_email, date_of_visit, notes, summary_markdown)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, patient_name, patient_email, date_of_visit, notes, summary_markdown),
        )
        return int(cursor.lastrowid)


def list_history_items(user_id: str):
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, patient_name, patient_email, date_of_visit, created_at, pinned
            FROM consultation_history
            WHERE user_id = ?
            ORDER BY pinned DESC, id DESC
            LIMIT 100
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_history_item(user_id: str, history_id: int):
    with get_db_connection() as conn:
        row = conn.execute(
            """
            SELECT id, patient_name, patient_email, date_of_visit, notes, summary_markdown, created_at, pinned
            FROM consultation_history
            WHERE user_id = ? AND id = ?
            """,
            (user_id, history_id),
        ).fetchone()
    return dict(row) if row else None


def set_history_pin(user_id: str, history_id: int, pinned: bool):
    with get_db_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE consultation_history
            SET pinned = ?
            WHERE user_id = ? AND id = ?
            """,
            (1 if pinned else 0, user_id, history_id),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            """
            SELECT id, patient_name, patient_email, date_of_visit, created_at, pinned
            FROM consultation_history
            WHERE user_id = ? AND id = ?
            """,
            (user_id, history_id),
        ).fetchone()
    return dict(row) if row else None


try:
    init_history_db()
except Exception as e:
    HISTORY_ENABLED = False
    print(f"History DB disabled due to initialization error: {e}")


def push(text: str) -> None:
    token = os.getenv("PUSHOVER_TOKEN")
    user = os.getenv("PUSHOVER_USER")

    if not token or not user:
        print("Pushover skipped: PUSHOVER_TOKEN or PUSHOVER_USER is missing")
        return

    payload = urllib.parse.urlencode(
        {
            "token": token,
            "user": user,
            "message": text,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.pushover.net/1/messages.json",
        data=payload,
        method="POST",
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            print(f"Pushover status={resp.status}, body={body}")
    except Exception as e:
        print(f"Pushover error: {e}")


def send_email(body: str, recipient_email: str):
    """Send out an email with the given body."""
    emailkey = os.getenv("SENDGRID_API_KEY")
    emailfrom = os.getenv("SENDGRID_SENDER_EMAIL")
    if not emailkey or not emailfrom:
        print("Sendgrid skipped: SENDGRID_API_KEY or SENDGRID_SENDER_EMAIL is missing")
        return {"status": "skipped", "reason": "missing sendgrid env vars"}

    sg = sendgrid.SendGridAPIClient(api_key=emailkey)
    from_email = Email(emailfrom)
    to_email = To(recipient_email)
    content = Content("text/html", body)
    mail = Mail(from_email, to_email, "Consultation Summary", content).get()
    sg.client.mail.send.post(request_body=mail)
    return {"status": "success"}


class Visit(BaseModel):
    patient_name: str
    date_of_visit: str
    patient_email: str
    notes: str

    @field_validator("patient_email")
    @classmethod
    def validate_patient_email(cls, value: str) -> str:
        email = value.strip()
        if not re.fullmatch(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", email):
            raise ValueError("patient_email must be a valid email address")
        return email


class PinUpdate(BaseModel):
    pinned: bool


system_prompt = """
You are a clinical documentation assistant for a doctor.
Use only the visit notes provided by the doctor. Do not invent diagnoses, medications, test results, or follow-up details.
If a diagnosis or clinical impression is not stated in the notes, say "Not specified in the notes."

Your output is the consultation report that will be shown directly in the browser.
Reply with exactly three Markdown sections with these exact headings:
### Summary of visit for the doctor's records
### Next steps for the doctor
### Draft of email to patient in patient-friendly language

Content requirements:
- In the summary, include the diagnosis, assessment, or clinical impression when it is present in the notes.
- In the next steps, list concrete clinician actions and follow-up items from the notes.
- In the draft email, write patient-friendly language that can be sent to the patient.

Important formatting rules:
- Your response content must be Markdown only.
- Never include raw HTML tags in your visible assistant response.
- Do not say that an email was sent.
- Do not write a status update, confirmation, or notification.
- Do not mention tools, sending, delivery, or notifications.
- The application will send the generated draft after your report is returned.
"""

required_report_headings = [
    "### Summary of visit for the doctor's records",
    "### Next steps for the doctor",
    "### Draft of email to patient in patient-friendly language",
]

invalid_report_phrases = [
    "i've sent",
    "i have sent",
    "email has been sent",
    "email was sent",
    "sent an email",
    "i sent",
    "notification",
]


def user_prompt_for(visit: Visit) -> str:
    return f"""Create the browser-visible consultation report for:
Patient Name: {visit.patient_name}
Date of Visit: {visit.date_of_visit}
Patient Email: {visit.patient_email}
Notes:
{visit.notes}"""


def is_valid_report(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    if any(phrase in normalized for phrase in invalid_report_phrases):
        return False
    return all(heading.lower() in normalized for heading in required_report_headings)


def make_gemini_client() -> genai.Client:
    """Use Vertex AI on GCP, or GEMINI_API_KEY for local development."""
    use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if use_vertex:
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID")
        location = os.getenv("GOOGLE_CLOUD_LOCATION") or os.getenv("GCP_REGION") or "global"
        return genai.Client(vertexai=True, project=project, location=location)

    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def generate_report(client: genai.Client, visit: Visit) -> str:
    """Generate the Markdown report that is shown on-screen and stored."""
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_prompt_for(visit),
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.2,
        ),
    )

    report = response.text or ""
    if is_valid_report(report):
        return report

    print(f"Gemini returned invalid report shape, retrying. First response: {report!r}")
    retry_response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=f"""{user_prompt_for(visit)}

Your previous response was not acceptable because it did not contain the browser-visible consultation report.
Return only the three required Markdown sections with the exact headings. Do not say that an email was sent.
""",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.1,
        ),
    )

    return retry_response.text or ""


def report_to_email_html(report_markdown: str) -> str:
    """Convert the generated Markdown report into simple, readable email HTML."""
    html_parts = [
        "<html><body style=\"font-family: Arial, sans-serif; line-height: 1.6; color: #0f172a;\">"
    ]
    in_list = False

    for raw_line in report_markdown.splitlines():
        line = raw_line.strip()
        if not line:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            continue

        escaped = html.escape(line.lstrip("#").strip())

        if line.startswith("### "):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<h2 style=\"margin-top: 24px; color: #0f172a;\">{escaped}</h2>")
        elif line.startswith("- "):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{html.escape(line[2:].strip())}</li>")
        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append(f"<p>{html.escape(line)}</p>")

    if in_list:
        html_parts.append("</ul>")

    html_parts.append("</body></html>")
    return "".join(html_parts)


def normalize_stream_text(text: str) -> str:
    """Convert accidental HTML in model output into readable markdown-like text."""
    if not re.search(r"<[a-zA-Z][^>]*>", text):
        return text

    normalized = text
    normalized = re.sub(r"(?i)<br\\s*/?>", "\n", normalized)
    normalized = re.sub(r"(?i)</(p|div|h[1-6])>", "\n\n", normalized)
    normalized = re.sub(r"(?i)<li[^>]*>", "- ", normalized)
    normalized = re.sub(r"(?i)</li>", "\n", normalized)
    normalized = re.sub(r"(?i)<[^>]+>", "", normalized)
    normalized = html.unescape(normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    return normalized


@app.post("/api/consultation")
def consultation_summary(
    visit: Visit,
    creds: HTTPAuthorizationCredentials = Depends(clerk_guard),
):
    user_id = creds.decoded["sub"]  # Available for tracking/auditing
    client = make_gemini_client()

    final_text = generate_report(client, visit)
    stream_text = normalize_stream_text(final_text)
    if not is_valid_report(stream_text):
        print(f"Gemini failed to return a valid consultation report: {stream_text!r}")
        raise HTTPException(
            status_code=502,
            detail="The AI model did not return a valid consultation report. Please try again.",
        )

    try:
        email_result = send_email(report_to_email_html(stream_text), visit.patient_email)
        print(f"SendGrid result for {visit.patient_email}: {email_result}")
    except Exception as e:
        print(f"SendGrid error: {e}")

    history_id = None
    if HISTORY_ENABLED:
        try:
            history_id = save_history_item(
                user_id=user_id,
                patient_name=visit.patient_name,
                patient_email=visit.patient_email,
                date_of_visit=visit.date_of_visit,
                notes=visit.notes,
                summary_markdown=stream_text,
            )
        except Exception as e:
            print(f"History save error: {e}")

    def event_stream():
        if history_id is not None:
            print(f"Saved consultation history id={history_id} for user={user_id}")
        for line in stream_text.split("\n"):
            yield f"data: {line}\n\n"
        push(f"Summary generated for {visit.patient_name} on {visit.date_of_visit}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/history")
@app.get("/history")
def consultation_history(
    creds: HTTPAuthorizationCredentials = Depends(clerk_guard),
):
    if not HISTORY_ENABLED:
        return {"items": []}
    user_id = creds.decoded["sub"]
    return {"items": list_history_items(user_id)}


@app.get("/api")
def consultation_history_compat(
    action: str = Query(default="none"),
    history_id: int = Query(default=0),
    creds: HTTPAuthorizationCredentials = Depends(clerk_guard),
):
    """
    Compatibility endpoint for deployments where /api/history subpaths are not routed
    to this ASGI app. Use /api?action=history|detail.
    """
    user_id = creds.decoded["sub"]

    if action == "history":
        if not HISTORY_ENABLED:
            return {"items": []}
        return {"items": list_history_items(user_id)}

    if action == "detail":
        if not HISTORY_ENABLED:
            raise HTTPException(status_code=503, detail="History service unavailable")
        if history_id <= 0:
            raise HTTPException(status_code=400, detail="history_id is required")
        item = get_history_item(user_id, history_id)
        if not item:
            raise HTTPException(status_code=404, detail="History item not found")
        return item

    raise HTTPException(status_code=400, detail="Invalid action")


@app.get("/api/history/{history_id}")
@app.get("/history/{history_id}")
def consultation_history_detail(
    history_id: int,
    creds: HTTPAuthorizationCredentials = Depends(clerk_guard),
):
    if not HISTORY_ENABLED:
        raise HTTPException(status_code=503, detail="History service unavailable")
    user_id = creds.decoded["sub"]
    item = get_history_item(user_id, history_id)
    if not item:
        raise HTTPException(status_code=404, detail="History item not found")
    return item


@app.patch("/api/history/{history_id}/pin")
@app.patch("/history/{history_id}/pin")
def consultation_history_pin(
    history_id: int,
    payload: PinUpdate,
    creds: HTTPAuthorizationCredentials = Depends(clerk_guard),
):
    if not HISTORY_ENABLED:
        raise HTTPException(status_code=503, detail="History service unavailable")
    user_id = creds.decoded["sub"]
    updated = set_history_pin(user_id, history_id, payload.pinned)
    if not updated:
        raise HTTPException(status_code=404, detail="History item not found")
    return updated


@app.patch("/api")
def consultation_history_pin_compat(
    payload: PinUpdate,
    action: str = Query(default="none"),
    history_id: int = Query(default=0),
    creds: HTTPAuthorizationCredentials = Depends(clerk_guard),
):
    """
    Compatibility endpoint for deployments where /api/history/{id}/pin is not routed.
    Use /api?action=pin&history_id=<id>.
    """
    if action != "pin":
        raise HTTPException(status_code=400, detail="Invalid action")
    if not HISTORY_ENABLED:
        raise HTTPException(status_code=503, detail="History service unavailable")
    if history_id <= 0:
        raise HTTPException(status_code=400, detail="history_id is required")

    user_id = creds.decoded["sub"]
    updated = set_history_pin(user_id, history_id, payload.pinned)
    if not updated:
        raise HTTPException(status_code=404, detail="History item not found")
    return updated


@app.get("/health")
def health_check():
    """Health check endpoint for AWS App Runner"""
    return {"status": "healthy", "version": APP_VERSION}


@app.get("/version")
def version_check():
    return {
        "version": APP_VERSION,
        "model": GEMINI_MODEL,
        "report_contract": "three-section-clinical-report-v2",
    }

# Serve static files (our Next.js export) - MUST BE LAST!
static_path = Path("static")
if static_path.exists():
    # Serve index.html for the root path
    @app.get("/")
    async def serve_root():
        return FileResponse(static_path / "index.html")
    
    # Mount static files for all other routes
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
