import html
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import (
    SMTP_FROM_EMAIL,
    FRONTEND_URL,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_SSL,
    SMTP_USERNAME,
)

logger = logging.getLogger(__name__)


def _send(message: EmailMessage, recipient: str) -> None:
    if not SMTP_HOST or not SMTP_FROM_EMAIL:
        logger.info("Email skipped: SMTP is not configured.")
        return
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
        logger.exception("Email could not be delivered to %s.", recipient)


def send_welcome_email(name: str, recipient: str) -> None:
    message = EmailMessage()
    message["Subject"] = "Welcome to ZengenAI"
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = recipient
    message.set_content(
        f"Hi {name},\n\nWelcome to ZengenAI. Your account is ready. Sign in to explore your workspace and available AI specialists.\n\nThoughtfully built for better work,\nThe ZengenAI team"
    )
    message.add_alternative(
        f"""\
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <link href="https://fonts.googleapis.com/css2?family=Great+Vibes&display=swap" rel="stylesheet">
            <title>Welcome to ZengenAI</title>
          </head>
          <body style="margin:0;padding:0;background-color:#0c0b12;color:#f5f3ff;font-family:Arial,Helvetica,sans-serif">
            <div style="display:none;max-height:0;overflow:hidden;opacity:0">Your ZengenAI workspace is ready. Come meet your AI team.</div>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#0c0b12">
              <tr><td align="center" style="padding:40px 16px">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;border:1px solid #342c45;border-radius:22px;background-color:#15131d;overflow:hidden">
                  <tr><td align="center" style="padding:34px 28px 26px;background-color:#1d1929;border-bottom:1px solid #342c45">
                    <div style="font-family:'Great Vibes','Brush Script MT',cursive;font-size:52px;line-height:1.12;color:#ffffff">Zengen <span style="display:inline-block;vertical-align:middle;margin-left:5px;padding:7px 9px;border:1px solid #66538c;border-radius:7px;color:#d8c9ff;font-family:Arial,Helvetica,sans-serif;font-size:11px;font-weight:bold;letter-spacing:2px">AI</span></div>
                    <p style="margin:14px 0 0;color:#c4b5fd;font-size:10px;font-weight:bold;letter-spacing:2px;text-transform:uppercase">A little more room for meaningful work</p>
                  </td></tr>
                  <tr><td style="padding:34px 38px 38px">
                    <p style="margin:0 0 12px;color:#aaa4b8;font-size:13px">Welcome, {html.escape(name)}.</p>
                    <h1 style="margin:0;color:#f5f3ff;font-size:27px;line-height:1.25;font-weight:700">Your workspace is ready.</h1>
                    <p style="margin:16px 0 0;color:#b6b0c5;font-size:15px;line-height:1.75">ZengenAI brings purpose-built AI teammates into one calm workspace, so you can spend more time moving your work forward.</p>
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin-top:26px;border:1px solid #342c45;border-radius:13px;background-color:#100f17">
                      <tr><td style="padding:16px 18px;color:#d8d2e5;font-size:13px;line-height:1.7"><span style="color:#b99aff">&#10022;</span>&nbsp; Explore the AI team and choose the specialists that fit your work.<br><span style="color:#b99aff">&#10022;</span>&nbsp; Your Personal Assistant can be included with an AI purchase, depending on your plan.</td></tr>
                    </table>
                    <p style="margin:28px 0 0;color:#aaa4b8;font-size:13px;line-height:1.7">We're glad you're here.<br><strong style="color:#f5f3ff;font-weight:600">The ZengenAI team</strong></p>
                  </td></tr>
                </table>
                <p style="margin:18px 0 0;color:#716b80;font-size:11px;line-height:1.6">Thoughtfully built for better work.</p>
              </td></tr>
            </table>
          </body>
        </html>
        """,
        subtype="html",
    )

    _send(message, recipient)


def send_password_reset_email(name: str, recipient: str, token: str, code: str) -> None:
    reset_url = f"{FRONTEND_URL}/reset-password?token={token}"
    safe_name = html.escape(name)
    message = EmailMessage()
    message["Subject"] = "Reset your ZengenAI password"
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = recipient
    message.set_content(
        f"Hi {name},\n\nUse this link to reset your ZengenAI password (valid for 20 minutes):\n{reset_url}\n\nOr enter this one-time code on the reset page: {code}\n\nIf you didn't request this, you can ignore this email."
    )
    message.add_alternative(
        f"""\
        <!doctype html><html lang="en"><body style="margin:0;padding:0;background:#0c0b12;color:#f5f3ff;font-family:Arial,Helvetica,sans-serif">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#0c0b12"><tr><td align="center" style="padding:36px 16px">
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;border:1px solid #342c45;border-radius:20px;background:#15131d">
              <tr><td align="center" style="padding:28px 24px;background:#1d1929;border-bottom:1px solid #342c45">
                <div style="font-family:'Great Vibes','Brush Script MT',cursive;font-size:46px;line-height:1.2;color:#fff">Zengen <span style="font-family:Arial,sans-serif;font-size:11px;font-weight:bold;letter-spacing:2px;color:#d8c9ff">AI</span></div>
                <p style="margin:12px 0 0;color:#c4b5fd;font-size:10px;font-weight:bold;letter-spacing:2px">ACCOUNT SECURITY</p>
              </td></tr>
              <tr><td style="padding:32px">
                <p style="margin:0 0 10px;color:#aaa4b8;font-size:13px">Hi {safe_name},</p>
                <h1 style="margin:0;font-size:25px;line-height:1.3">Create a new password.</h1>
                <p style="margin:14px 0 24px;color:#b6b0c5;font-size:14px;line-height:1.7">Use the secure button below, or enter the one-time code on the reset page. Both options expire in 20 minutes.</p>
                <p style="margin:0 0 24px"><a href="{html.escape(reset_url, quote=True)}" style="display:inline-block;padding:13px 20px;border-radius:9px;background:#c4b5fd;color:#171222;text-decoration:none;font-size:13px;font-weight:bold">Reset my password</a></p>
                <p style="margin:0 0 8px;color:#aaa4b8;font-size:12px">Or use this one-time code:</p>
                <p style="display:inline-block;margin:0;padding:12px 18px;border:1px solid #493c61;border-radius:10px;background:#100f17;color:#e8ddff;font-size:23px;font-weight:bold;letter-spacing:7px">{code}</p>
                <p style="margin:24px 0 0;color:#817b8e;font-size:11px;line-height:1.6">If you didn't request a password reset, ignore this email. Your password won't change.</p>
              </td></tr>
            </table>
            <p style="margin:16px 0 0;color:#716b80;font-size:11px">Thoughtfully built for better work.</p>
          </td></tr></table>
        </body></html>
        """,
        subtype="html",
    )
    _send(message, recipient)
