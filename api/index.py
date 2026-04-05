import os
from fastapi import FastAPI, Depends  # type: ignore
from fastapi.responses import StreamingResponse  # type: ignore
from pydantic import BaseModel, field_validator  # type: ignore
from fastapi_clerk_auth import ClerkConfig, ClerkHTTPBearer, HTTPAuthorizationCredentials  # type: ignore
from openai import OpenAI  # type: ignore
import urllib.request, urllib.parse, urllib.error
import json
import re
import html
import sendgrid
from sendgrid.helpers.mail import Mail, Email, To, Content

app = FastAPI()
clerk_config = ClerkConfig(jwks_url=os.getenv("CLERK_JWKS_URL"))
clerk_guard = ClerkHTTPBearer(clerk_config)


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

    def event_stream():
        for line in stream_text.split("\n"):
            yield f"data: {line}\n\n"
        push(f"Summary generated for {visit.patient_name} on {visit.date_of_visit}")

    return StreamingResponse(event_stream(), media_type="text/event-stream")
