"""
Notification Service Module
Handles Email and SMS notifications for ID Card Generator
"""

import os
import logging
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from models import db, Student, Template, NotificationLog, NotificationPreference
from flask import render_template_string
import requests

logger = logging.getLogger(__name__)

# ================== Email Configuration ==================
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")

# ================== SMS Configuration (Twilio) ==================
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

# ================== Email Notification Templates ==================
DEADLINE_APPROACHING_EMAIL = """
<html>
<head>
    <style>
        body { font-family: Arial, sans-serif; color: #333; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background-color: #2196F3; color: white; padding: 15px; text-align: center; }
        .content { padding: 20px; background-color: #f9f9f9; }
        .footer { text-align: center; font-size: 12px; color: #999; padding: 10px; }
        .button { background-color: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>ID Card Deadline Reminder</h2>
        </div>
        <div class="content">
            <p>Dear {{ student_name }},</p>
            <p>This is a reminder that your ID card generation deadline is approaching.</p>
            <p><strong>Deadline:</strong> {{ deadline_date }}</p>
            <p><strong>School:</strong> {{ school_name }}</p>
            <p>Please ensure you have completed the ID card generation process before the deadline.</p>
            <p style="text-align: center; margin-top: 20px;">
                <a href="{{ app_url }}" class="button">Go to App</a>
            </p>
        </div>
        <div class="footer">
            <p>This is an automated message. Please do not reply to this email.</p>
        </div>
    </div>
</body>
</html>
"""

CARD_READY_EMAIL = """
<html>
<head>
    <style>
        body { font-family: Arial, sans-serif; color: #333; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background-color: #4CAF50; color: white; padding: 15px; text-align: center; }
        .content { padding: 20px; background-color: #f9f9f9; }
        .footer { text-align: center; font-size: 12px; color: #999; padding: 10px; }
        .button { background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>Your ID Card is Ready!</h2>
        </div>
        <div class="content">
            <p>Dear {{ student_name }},</p>
            <p>Good news! Your ID card has been successfully generated and is ready for download.</p>
            <p><strong>School:</strong> {{ school_name }}</p>
            <p><strong>Generated At:</strong> {{ generated_time }}</p>
            <p style="text-align: center; margin-top: 20px;">
                <a href="{{ download_url }}" class="button">Download Your Card</a>
            </p>
        </div>
        <div class="footer">
            <p>This is an automated message. Please do not reply to this email.</p>
        </div>
    </div>
</body>
</html>
"""

ERROR_NOTIFICATION_EMAIL = """
<html>
<head>
    <style>
        body { font-family: Arial, sans-serif; color: #333; }
        .container { max-width: 600px; margin: 0 auto; padding: 20px; }
        .header { background-color: #f44336; color: white; padding: 15px; text-align: center; }
        .content { padding: 20px; background-color: #fff3cd; }
        .footer { text-align: center; font-size: 12px; color: #999; padding: 10px; }
        .error-box { background-color: #ffebee; border-left: 4px solid #f44336; padding: 15px; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>ID Card Generation Error</h2>
        </div>
        <div class="content">
            <p>Dear {{ student_name }},</p>
            <p>We encountered an error while generating your ID card.</p>
            <div class="error-box">
                <strong>Error Details:</strong><br/>
                {{ error_message }}
            </div>
            <p>Please contact your administrator for assistance.</p>
            <p><strong>School:</strong> {{ school_name }}</p>
        </div>
        <div class="footer">
            <p>This is an automated message. Please do not reply to this email.</p>
        </div>
    </div>
</body>
</html>
"""


# ================== Email Functions ==================
def send_email(recipient_email, subject, html_content):
    """
    Send an email notification.
    
    Args:
        recipient_email (str): Recipient email address
        subject (str): Email subject
        html_content (str): HTML content for the email
    
    Returns:
        tuple: (success: bool, message: str)
    """
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        logger.warning("Email credentials not configured. Skipping email notification.")
        return False, "Email not configured"
    
    try:
        # Create message
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = EMAIL_FROM
        message["To"] = recipient_email
        
        # Attach HTML content
        part = MIMEText(html_content, "html")
        message.attach(part)
        
        # Send via SMTP
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10)
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, recipient_email, message.as_string())
        server.quit()
        
        logger.info(f"Email sent successfully to {recipient_email}")
        return True, "Email sent successfully"
        
    except smtplib.SMTPAuthenticationError:
        msg = "SMTP authentication failed. Check EMAIL_FROM and EMAIL_PASSWORD."
        logger.error(msg)
        return False, msg
    except smtplib.SMTPException as e:
        msg = f"SMTP error: {str(e)}"
        logger.error(msg)
        return False, msg
    except Exception as e:
        msg = f"Error sending email: {str(e)}"
        logger.error(msg)
        return False, msg


# ================== SMS Functions (Twilio) ==================
def send_sms(phone_number, message_text):
    """
    Send SMS notification via Twilio.
    
    Args:
        phone_number (str): Recipient phone number (with country code)
        message_text (str): SMS message content
    
    Returns:
        tuple: (success: bool, message: str)
    """
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_PHONE_NUMBER:
        logger.warning("Twilio credentials not configured. Skipping SMS notification.")
        return False, "SMS not configured"
    
    try:
        from twilio.rest import Client
        
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=message_text,
            from_=TWILIO_PHONE_NUMBER,
            to=phone_number
        )
        
        logger.info(f"SMS sent successfully to {phone_number} (SID: {message.sid})")
        return True, f"SMS sent (SID: {message.sid})"
        
    except Exception as e:
        msg = f"Error sending SMS: {str(e)}"
        logger.error(msg)
        return False, msg


# ================== Notification Logging ==================
def log_notification(recipient_email, recipient_phone, notification_type, channel, subject, message, status, error_message=None, student_id=None, template_id=None):
    """
    Log a notification to the database for audit trail.
    
    Args:
        recipient_email (str): Recipient email
        recipient_phone (str): Recipient phone number
        notification_type (str): 'deadline', 'card_ready', 'error'
        channel (str): 'email' or 'sms'
        subject (str): Notification subject
        message (str): Notification message
        status (str): 'pending', 'sent', 'failed'
        error_message (str): Error details if failed
        student_id (int): Associated student ID
        template_id (int): Associated template ID
    """
    try:
        notif_log = NotificationLog(
            recipient_email=recipient_email,
            recipient_phone=recipient_phone,
            notification_type=notification_type,
            channel=channel,
            subject=subject,
            message=message,
            status=status,
            error_message=error_message,
            student_id=student_id,
            template_id=template_id,
            sent_at=datetime.now(timezone.utc) if status == 'sent' else None
        )
        db.session.add(notif_log)
        db.session.commit()
        logger.info(f"Notification logged: {notification_type} ({channel}) to {recipient_email or recipient_phone}")
    except Exception as e:
        logger.error(f"Error logging notification: {e}")
        db.session.rollback()


# ================== High-Level Notification Functions ==================
def notify_deadline_approaching(student_id, template_id, days_remaining=3):
    """
    Send deadline approaching notification to a student.
    
    Args:
        student_id (int): Student ID
        template_id (int): Template ID with deadline
        days_remaining (int): Days until deadline
    """
    try:
        student = db.session.get(Student, student_id)
        template = db.session.get(Template, template_id)
        
        if not student or not template:
            logger.warning(f"Student {student_id} or Template {template_id} not found")
            return
        
        # Check notification preference
        pref = student.notification_preference
        if pref and not pref.notify_deadline_approaching:
            logger.info(f"Student {student_id} has disabled deadline notifications")
            return
        
        if not student.email:
            logger.warning(f"Student {student_id} has no email address")
            return
        
        # Prepare email
        deadline_str = template.deadline.strftime("%d %B %Y, %I:%M %p") if template.deadline else "N/A"
        html_content = DEADLINE_APPROACHING_EMAIL.replace("{{ student_name }}", student.name or "Student")
        html_content = html_content.replace("{{ deadline_date }}", deadline_str)
        html_content = html_content.replace("{{ school_name }}", student.school_name or template.school_name or "Our School")
        html_content = html_content.replace("{{ app_url }}", os.environ.get("APP_URL", "https://app.example.com"))
        
        # Send email
        success, msg = send_email(student.email, "ID Card Deadline Reminder", html_content)
        
        # Log notification
        log_notification(
            recipient_email=student.email,
            recipient_phone=None,
            notification_type='deadline',
            channel='email',
            subject="ID Card Deadline Reminder",
            message=html_content,
            status='sent' if success else 'failed',
            error_message=msg if not success else None,
            student_id=student_id,
            template_id=template_id
        )
        
        # Send SMS if enabled
        if pref and pref.sms_enabled and pref.phone_number:
            sms_text = f"Hi {student.name}, your ID card deadline is in {days_remaining} days. Please complete it before {deadline_str}. Contact your school for help."
            sms_success, sms_msg = send_sms(pref.phone_number, sms_text)
            
            log_notification(
                recipient_email=None,
                recipient_phone=pref.phone_number,
                notification_type='deadline',
                channel='sms',
                subject="ID Card Deadline Reminder",
                message=sms_text,
                status='sent' if sms_success else 'failed',
                error_message=sms_msg if not sms_success else None,
                student_id=student_id,
                template_id=template_id
            )
        
    except Exception as e:
        logger.error(f"Error notifying deadline approaching for student {student_id}: {e}")


def notify_card_ready(student_id, template_id, download_url):
    """
    Send card ready notification to a student.
    
    Args:
        student_id (int): Student ID
        template_id (int): Template ID
        download_url (str): URL to download generated card
    """
    try:
        student = db.session.get(Student, student_id)
        template = db.session.get(Template, template_id)
        
        if not student or not template:
            logger.warning(f"Student {student_id} or Template {template_id} not found")
            return
        
        # Check notification preference
        pref = student.notification_preference
        if pref and not pref.notify_card_ready:
            logger.info(f"Student {student_id} has disabled card ready notifications")
            return
        
        if not student.email:
            logger.warning(f"Student {student_id} has no email address")
            return
        
        # Prepare email
        html_content = CARD_READY_EMAIL.replace("{{ student_name }}", student.name or "Student")
        html_content = html_content.replace("{{ school_name }}", student.school_name or template.school_name or "Our School")
        html_content = html_content.replace("{{ generated_time }}", datetime.now(timezone.utc).strftime("%d %B %Y, %I:%M %p"))
        html_content = html_content.replace("{{ download_url }}", download_url)
        
        # Send email
        success, msg = send_email(student.email, "Your ID Card is Ready!", html_content)
        
        # Log notification
        log_notification(
            recipient_email=student.email,
            recipient_phone=None,
            notification_type='card_ready',
            channel='email',
            subject="Your ID Card is Ready!",
            message=html_content,
            status='sent' if success else 'failed',
            error_message=msg if not success else None,
            student_id=student_id,
            template_id=template_id
        )
        
        # Send SMS if enabled
        if pref and pref.sms_enabled and pref.phone_number:
            sms_text = f"Hi {student.name}, your ID card is ready! Download it now: {download_url}"
            sms_success, sms_msg = send_sms(pref.phone_number, sms_text)
            
            log_notification(
                recipient_email=None,
                recipient_phone=pref.phone_number,
                notification_type='card_ready',
                channel='sms',
                subject="Your ID Card is Ready!",
                message=sms_text,
                status='sent' if sms_success else 'failed',
                error_message=sms_msg if not sms_success else None,
                student_id=student_id,
                template_id=template_id
            )
        
    except Exception as e:
        logger.error(f"Error notifying card ready for student {student_id}: {e}")


def notify_generation_error(student_id, template_id, error_message):
    """
    Send error notification to student about failed card generation.
    
    Args:
        student_id (int): Student ID
        template_id (int): Template ID
        error_message (str): Error details
    """
    try:
        student = db.session.get(Student, student_id)
        template = db.session.get(Template, template_id)
        
        if not student or not template:
            logger.warning(f"Student {student_id} or Template {template_id} not found")
            return
        
        # Check notification preference
        pref = student.notification_preference
        if pref and not pref.notify_errors:
            logger.info(f"Student {student_id} has disabled error notifications")
            return
        
        if not student.email:
            logger.warning(f"Student {student_id} has no email address")
            return
        
        # Prepare email
        html_content = ERROR_NOTIFICATION_EMAIL.replace("{{ student_name }}", student.name or "Student")
        html_content = html_content.replace("{{ error_message }}", error_message or "Unknown error occurred")
        html_content = html_content.replace("{{ school_name }}", student.school_name or template.school_name or "Our School")
        
        # Send email
        success, msg = send_email(student.email, "ID Card Generation Error", html_content)
        
        # Log notification
        log_notification(
            recipient_email=student.email,
            recipient_phone=None,
            notification_type='error',
            channel='email',
            subject="ID Card Generation Error",
            message=html_content,
            status='sent' if success else 'failed',
            error_message=msg if not success else None,
            student_id=student_id,
            template_id=template_id
        )
        
    except Exception as e:
        logger.error(f"Error notifying generation error for student {student_id}: {e}")


# ================== Scheduled Tasks (for checking deadlines) ==================
def check_and_notify_approaching_deadlines():
    """
    Check for templates with approaching deadlines and notify students.
    This should be run as a scheduled task (e.g., daily via APScheduler).
    """
    try:
        # Find templates with deadlines in the next 3 days
        three_days_future = datetime.now(timezone.utc) + timedelta(days=3)
        three_days_past = datetime.now(timezone.utc)
        
        templates_with_deadlines = Template.query.filter(
            Template.deadline.isnot(None),
            Template.deadline >= three_days_past,
            Template.deadline <= three_days_future
        ).all()
        
        for template in templates_with_deadlines:
            # Get all students for this template
            students = Student.query.filter_by(template_id=template.id).all()
            
            for student in students:
                # Check if we already notified this student
                existing_notif = NotificationLog.query.filter_by(
                    student_id=student.id,
                    template_id=template.id,
                    notification_type='deadline',
                    status='sent'
                ).first()
                
                if not existing_notif:
                    # Calculate days remaining
                    days_remaining = (template.deadline - datetime.now(timezone.utc)).days
                    notify_deadline_approaching(student.id, template.id, days_remaining)
        
        logger.info(f"Deadline check completed. Found {len(templates_with_deadlines)} templates with approaching deadlines.")
        
    except Exception as e:
        logger.error(f"Error checking approaching deadlines: {e}")
