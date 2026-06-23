"""
Email sending service.

Handles SMTP email delivery with IPv4 fallback.
Extracted from legacy_app.py.
"""

import logging
import os
import smtplib
import socket
import ssl

from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_email(to, subject, body):
    """Send an email via Gmail SMTP with IPv4 forcing."""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = os.environ.get("EMAIL_FROM")
    msg["To"] = to

    server = None
    try:
        smtp_server = "smtp.gmail.com"
        smtp_port = 465
        password = os.environ.get("EMAIL_PASSWORD")

        logger.info("Sending email to %s via %s:%s...", to, smtp_server, smtp_port)

        addr_info = socket.getaddrinfo(
            smtp_server, smtp_port, socket.AF_INET, socket.SOCK_STREAM,
        )
        family, socktype, proto, canonname, sa = addr_info[0]
        target_ip = sa[0]

        logger.info("Connecting to Gmail IPv4: %s", target_ip)

        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(target_ip, smtp_port, context=context, timeout=30)

        logger.info("Logging in...")
        server.login(msg["From"], password)
        server.send_message(msg)
        server.quit()

        logger.info("Email sent successfully to %s", to)
        return True

    except Exception as e:
        logger.error("Failed to send email: %s", e)
        return False
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass
