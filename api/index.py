import os
from fastapi import FastAPI, Depends, HTTPException, Query  # type: ignore
from fastapi.responses import StreamingResponse  # type: ignore
from pydantic import BaseModel, field_validator  # type: ignore
from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer, HTTPAuthorizationCredentials  # type: ignore
from openai import OpenAI  # type: ignore
import urllib.request, urllib.parse, urllib.error
import json
import re
import html
import sqlite3
import sendgrid
from sendgrid.helpers.mail import Mail, Email, To, Content

app = FastAPI()
clerk_config = ClerkConfig(jwks_url=os.getenv("CLERK_JWKS_URL"))
clerk_guard = ClerkHTTPBearer(clerk_config)
HISTORY_DB_PATH = os.getenv("HISTORY_DB_PATH", "consultation_history.db")
HISTORY_ENABLED = True


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


send_email_json = {
    "name": "send_email",
    "description": "Use this tool to send an email to the patient",
    "parameters": {
        "type": "object",
        "properties": {
            "body": {
                "type": "string",
                "description": "The body of the email to send to the patient",
            }
        },
        "required": ["body"],
        "additionalProperties": False,
    },
}

tools = [{"type": "function", "function": send_email_json}]


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
You are provided with notes written by a doctor from a patient's visit.
Your job is to summarize the visit for the doctor and provide an email.
Reply with exactly three sections with the headings:
### Summary of visit for the doctor's records
### Next steps for the doctor
### Draft of email to patient in patient-friendly language
You are able use the send_email tool to send the email to the patient.
You should use your tool to send one email, providing the report converted into clean, well presented HTML.
Important formatting rules:
- The tool argument `body` must be HTML.
- Your visible assistant response content must be Markdown only.
- Never include raw HTML tags in your visible assistant response.
"""


def user_prompt_for(visit: Visit) -> str:
    return f"""Create the summary, next steps and draft email for:
Patient Name: {visit.patient_name}
Date of Visit: {visit.date_of_visit}
Patient Email: {visit.patient_email}
Notes:
{visit.notes}"""


def run_with_tools(client: OpenAI, messages, recipient_email: str):
    """Run chat completions and execute tool calls until the model returns final text."""
    for _ in range(5):
        completion = client.chat.completions.create(
            model="gpt-5.4-nano",
            messages=messages,
            tools=tools,
            tool_choice="auto",
            stream=False,
        )

        choice = completion.choices[0]
        message = choice.message
        tool_calls = message.tool_calls or []

        if not tool_calls:
            return message.content or ""

        assistant_tool_call_message = {
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        }
        messages.append(assistant_tool_call_message)

        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            raw_args = tool_call.function.arguments or "{}"

            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError:
                arguments = {}

            if tool_name != "send_email":
                result = {"status": "error", "error": f"Unknown tool: {tool_name}"}
            else:
                result = send_email(
                    body=arguments.get("body", ""),
                    recipient_email=recipient_email,
                )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

    return "Unable to complete after multiple tool-call rounds."


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


@app.post("/api")
def consultation_summary(
    visit: Visit,
    creds: HTTPAuthorizationCredentials = Depends(clerk_guard),
):
    user_id = creds.decoded["sub"]  # Available for tracking/auditing
    client = OpenAI()

    user_prompt = user_prompt_for(visit)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    final_text = run_with_tools(client, messages, visit.patient_email)
    stream_text = normalize_stream_text(final_text)
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
