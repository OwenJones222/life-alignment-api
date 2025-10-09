import io
import os
import json
import smtplib
import ssl
from email.message import EmailMessage

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# ----------------------------
# App & CORS
# ----------------------------
app = FastAPI(title="Life Alignment API")

# Allow your WP domain(s) – add more as needed
ALLOWED_ORIGINS = [
    "https://queensparkfitness.com",
    "https://www.queensparkfitness.com",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Report builder resolver
# ----------------------------
def _resolve_report_builder():
    """
    Locate a callable in generate_report_json that will build the PDF.
    Supports:
      - build_pdf_report(data) -> bytes
      - build_pdf_report(data, out_pdf: io.BytesIO) -> None (writes to buffer)
    Select with env REPORT_FUNC (default: build_pdf_report).
    """
    from generate_report_json import __dict__ as rpt

    name = os.getenv("REPORT_FUNC", "build_pdf_report")
    func = rpt.get(name)
    if not callable(func):
        raise ImportError(
            f"REPORT_FUNC '{name}' not found/callable in generate_report_json.py"
        )
    print(f"[report] Using discovered builder: {name}()")
    return func

BUILD_REPORT = _resolve_report_builder()

# ----------------------------
# Email helper (Gmail SMTP)
# ----------------------------
SMTP_USER = os.getenv("SMTP_USER")  # your Gmail address
SMTP_PASS = os.getenv("SMTP_PASS")  # the Gmail "App password"

def send_email_with_attachment(
    to_email: str,
    subject: str,
    body_text: str,
    filename: str,
    file_bytes: bytes,
) -> None:
    if not (SMTP_USER and SMTP_PASS):
        raise RuntimeError("SMTP_USER/SMTP_PASS not configured in environment.")

    msg = EmailMessage()
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)

    msg.add_attachment(
        file_bytes,
        maintype="application",
        subtype="pdf",
        filename=filename,
    )

    context = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls(context=context)
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)

# ----------------------------
# Utilities
# ----------------------------
async def _read_tolerant_json(request: Request) -> dict:
    """
    Try to parse JSON from WordPress/Elementor in a tolerant way.
    - First, request.json()
    - If that fails (e.g., urlencoded), read body and json.loads()
    """
    try:
        return await request.json()
    except Exception:
        raw = await request.body()
        try:
            return json.loads(raw.decode("utf-8", errors="ignore"))
        except Exception:
            # last chance: empty / invalid
            return {}

def _build_pdf_bytes(payload: dict) -> bytes:
    """
    Call the discovered report builder in a signature-tolerant way.
    """
    buf = io.BytesIO()
    try:
        # Try 2-arg signature: (data, out_pdf)
        result = BUILD_REPORT(payload, buf)  # type: ignore
        if buf.getbuffer().nbytes > 0:
            return buf.getvalue()
        # If nothing wrote to buffer but result returned bytes, handle that too:
        if isinstance(result, (bytes, bytearray)):
            return bytes(result)
    except TypeError:
        # Fallback to 1-arg signature: (data) -> bytes
        result = BUILD_REPORT(payload)  # type: ignore
        if isinstance(result, (bytes, bytearray)):
            return bytes(result)
        # Some builders might still write to buf even if they accept one arg (unlikely)
        if buf.getbuffer().nbytes > 0:
            return buf.getvalue()

    raise RuntimeError(
        "Report builder did not return bytes or write to buffer. "
        "Ensure it implements either (data)->bytes or (data, out_pdf)->None."
    )

# ----------------------------
# Routes
# ----------------------------
@app.get("/")
def root():
    return {"ok": True, "service": "life-alignment-api"}

@app.post("/generate")
async def generate_report(request: Request):
    """
    Accept the Elementor JSON payload, build the PDF, and email it to the user.
    Also prints the raw payload (pretty JSON) to Render logs for debugging.
    """
    # 1) Read payload (tolerant)
    payload = await _read_tolerant_json(request)

    # 2) DEBUG: dump the exact payload your form is sending
    try:
        print("\n==== PAYLOAD DEBUG ====")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print("==== END PAYLOAD DEBUG ====\n", flush=True)
    except Exception as e:
        print(f"[warn] Could not pretty-print payload: {e}")

    # 3) Build the PDF
    pdf_bytes = _build_pdf_bytes(payload)

    # 4) Determine recipient
    #    Adjust the keys below if your Elementor field names differ
    to_email = (
        payload.get("email")
        or payload.get("user", {}).get("email")
        or SMTP_USER  # fallback so we can still test
    )

    if not to_email:
        return {"ok": False, "error": "No destination email in payload and no SMTP_USER fallback."}

    # 5) Email it
    subject = "Your Life Alignment Diagnostic Report"
    body = (
        "Hi there,\n\n"
        "Thank you for completing the Life Alignment Diagnostic.\n"
        "Attached is your personalised PDF report.\n\n"
        "What to do next:\n"
        "1) Skim the spiderweb chart to see your overall pattern\n"
        "2) Look at each pillar’s bar chart – the ‘Priority Gap’ bars show where focus will pay off fastest\n"
        "3) Start with the largest gap – one meaningful action this week is enough to build momentum\n\n"
        "I’ll follow up with guidance on how to interpret the results and options for next steps.\n\n"
        "Warm regards,\n"
        "Owen Jones\n—\nAutomated email from your Life Alignment system."
        Hi [First Name],

Thank you for completing the Life Alignment Diagnostic.

Attached is your personalised PDF report — a clear visual snapshot of where your priorities and day-to-day experience may not always align.

Here’s how to read your results:

1) Each page shows one of the four pillars — Health, Wealth, Self and Social — with a set of bar charts.
   The lighter bars show your overall “Strength” in that area, while the darker “Priority Gap” bars show where there’s most room to grow.

2) Below each chart you’ll see your Wildcard reflections — your own written notes.
   These can give valuable insight when thinking about why certain areas scored the way they did or what might help them shift.

3) Interpreting the Priority Gaps:
   • When a sub-category is marked as “most important to me” and the individual question scores were already high (4s or 5s), the gap will be small — that usually means things are on track and no major action is needed.
   • When a sub-category is “most important” but the question scores were lower (1s or 2s), the gap will be larger — this is where small, deliberate changes are likely to make the biggest difference.

The report is designed to highlight opportunities, not shortcomings. Even one small action in a high-priority area can start to restore balance quickly.

If you opted for the coaching session, we’ll explore these findings together and agree your first steps. 
Otherwise, take a few minutes to reflect on what stands out — awareness is the first step toward realignment.

Warm regards,
Owen Jones
—
Automated email from your Life Alignment system.

    )

    filename = "Life_Alignment_Report.pdf"
    send_email_with_attachment(to_email, subject, body, filename, pdf_bytes)

    return {"ok": True, "sent_to": to_email}
