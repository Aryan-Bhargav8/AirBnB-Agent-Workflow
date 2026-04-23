import csv
import smtplib
import tempfile
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings

_llm = ChatAnthropic(
    base_url="http://host.docker.internal:11434",
    api_key="ollama",
    model="minimax-m2.7:cloud",
    temperature=0.9,
)


def _generate_email_body(listings: list[dict]) -> str:
    count = len(listings)
    names = [l.get("name", "") for l in listings[:3] if l.get("name")]
    sample = ", ".join(names) + ("…" if count > 3 else "")
    prompt = (
        f"You are writing a short, warm and slightly playful email body for someone "
        f"who just searched for Airbnb listings. They found {count} listing(s). "
        f"A few of the places: {sample or 'various lovely spots'}. "
        "Write 2-3 sentences max. Be friendly, excited, and encouraging — like a helpful travel buddy. "
        "End by telling them the full list is in the attached Excel file. No subject line, just the body."
    )
    try:
        response = _llm.invoke([
            SystemMessage(content="You write short, friendly email messages for a travel assistant app."),
            HumanMessage(content=prompt),
        ])
        return response.content.strip()
    except Exception:
        return f"Great news! We found {count} Airbnb listing(s) for you. Check the attached Excel file for the full list."

# Fields to extract from each raw listing dict
_LISTING_FIELDS = [
    "id",
    "name",
    "url",
    "price",
    "rating",
    "reviewsCount",
    "beds",
    "badges",
]


def listings_to_csv(listings: list[dict]) -> str:
    """Write pre-flattened listing rows to a temp CSV. Returns the file path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="")
    writer = csv.DictWriter(tmp, fieldnames=_LISTING_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(listings)
    tmp.flush()
    tmp.close()
    return tmp.name


def csv_to_xlsx(csv_path: str) -> str:
    """Convert CSV to XLSX using pandas. Returns the XLSX file path."""
    df = pd.read_csv(csv_path)
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    df.to_excel(tmp.name, index=False, engine="openpyxl")
    return tmp.name


def send_email(xlsx_path: str, to_email: str, listings: list[dict] | None = None) -> None:
    """Attach XLSX and send via Gmail SMTP SSL (port 465)."""
    body = _generate_email_body(listings or [])

    msg = MIMEMultipart()
    msg["From"] = settings.gmail_address
    msg["To"] = to_email
    msg["Subject"] = "Your Airbnb Listings"

    msg.attach(MIMEText(body, "plain"))

    with open(xlsx_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", 'attachment; filename="airbnb_listings.xlsx"')
        msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(settings.gmail_address, settings.gmail_app_password.get_secret_value())
        smtp.send_message(msg)
