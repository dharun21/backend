import html
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import (
    SMTP_FROM_EMAIL,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_SSL,
    SMTP_USERNAME,
)

logger = logging.getLogger(__name__)


def send_welcome_email(name: str, recipient: str) -> None:
    if not SMTP_HOST or not SMTP_FROM_EMAIL:
        logger.info("Welcome email skipped: SMTP is not configured.")
        return

    message = EmailMessage()
    message["Subject"] = "Welcome to ZengenAI"
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = recipient
    message.set_content(
        f"Hi {name},\n\nWelcome to ZengenAI. Your account is ready. Sign in to set up your AI workspace.\n\nThe ZengenAI team"
    )
    message.add_alternative(
        f"""\
        <html><body style="margin:0;background:#0c0b12;color:#f5f3ff;font-family:Arial,sans-serif">
          <main style="max-width:560px;margin:32px auto;padding:36px;border:1px solid #302a3d;border-radius:18px;background:#15131d">
            <p style="color:#c4b5fd;font-size:12px;letter-spacing:2px;text-transform:uppercase">Welcome to ZengenAI</p>
            <h1 style="font-size:28px">Good to have you here, {html.escape(name)}.</h1>
            <p style="color:#b6b0c5;line-height:1.7">Your account is ready. Sign in to set up your AI workspace and meet the specialists built to help with your work.</p>
            <p style="margin-top:28px;color:#b6b0c5">Thoughtfully built for better work,<br><strong style="color:#f5f3ff">The ZengenAI team</strong></p>
          </main>
        </body></html>
        """,
        subtype="html",
    )

    try:
        context = ssl.create_default_context()
        if SMTP_USE_SSL:
            connection = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15, context=context)
        else:
            connection = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        with connection as server:
            if not SMTP_USE_SSL:
                server.starttls(context=context)
            if SMTP_USERNAME:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
    except (OSError, smtplib.SMTPException):
        logger.exception("Welcome email could not be delivered to %s.", recipient)
