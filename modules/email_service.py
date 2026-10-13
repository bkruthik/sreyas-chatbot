"""
modules/email_service.py
================================================================================
Generic Resilient Email Service Module.
Sends HTML & Text acknowledgement emails asynchronously using standard smtplib.
Configurable for Gmail, Outlook, or custom college SMTP servers (e.g. mail.sreyas.ac.in).
Includes retries (3 attempts) and error logging without blocking enquiry creation.
================================================================================
"""

import smtplib
import threading
import logging
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional, Tuple

import config

logger = logging.getLogger("sreyas_chatbot.email")


class EmailService:
    """Handles asynchronous email sending with retry logic."""

    @classmethod
    def _send_email_task(
        cls,
        recipients: List[str],
        subject: str,
        html_body: str,
        text_body: str,
        attempts: int = 3,
    ) -> bool:
        """Internal worker for sending emails with retries."""
        if not config.SMTP_USER or not config.SMTP_PASS:
            logger.info("SMTP credentials not configured. Email simulation active.")
            logger.info("[EMAIL DISPATCH] To: %s | Subject: %s", recipients, subject)
            return True

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = config.SMTP_FROM_EMAIL
        msg["To"] = ", ".join(recipients)

        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        for attempt in range(1, attempts + 1):
            try:
                if config.SMTP_PORT == 465:
                    server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=10)
                else:
                    server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10)
                    if config.SMTP_USE_TLS:
                        server.starttls()

                server.login(config.SMTP_USER, config.SMTP_PASS)
                server.sendmail(config.SMTP_FROM_EMAIL, recipients, msg.as_string())
                server.quit()
                logger.info("Successfully sent email to %s (attempt %d).", recipients, attempt)
                return True
            except Exception as e:
                logger.warning("SMTP send attempt %d/%d failed for %s: %s", attempt, attempts, recipients, e)
                if attempt < attempts:
                    time.sleep(2 * attempt)

        logger.error("Failed to send email to %s after %d attempts.", recipients, attempts)
        return False

    @classmethod
    def send_enquiry_acknowledgement(
        cls,
        student_email: str,
        student_name: str,
        reference_id: str,
        question_or_course: str,
        parent_email: Optional[str] = None,
    ) -> bool:
        """
        Dispatch acknowledgement email for General Enquiry in a background thread.
        Returns True if the background dispatch task was initiated.
        """
        recipients = [student_email.strip()]
        if parent_email and parent_email.strip() and "@" in parent_email:
            recipients.append(parent_email.strip())

        subject = f"Enquiry Received – Sreyas Institute ({reference_id})"

        text_body = f"""Dear {student_name},

Thank you for your enquiry to Sreyas Institute of Engineering and Technology (SIET).

We have received your question and registered it under Reference ID: {reference_id}.

Details Submitted:
- Reference ID: {reference_id}
- Name: {student_name}
- Question / Query: {question_or_course}

Our admissions desk team has been notified and will reply to your question within 24 hours.

Best regards,
Admissions Desk
Sreyas Institute of Engineering and Technology
Nagole, Hyderabad
Phone: {config.COLLEGE_PHONE} | Email: {config.COLLEGE_EMAIL}
Website: {config.COLLEGE_WEBSITE}
"""

        html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6; background-color: #f4f6f9; padding: 20px;">
  <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; padding: 24px; border: 1px solid #e2e8f0;">
    <div style="background: linear-gradient(135deg, #1a73e8, #4285f4); padding: 18px; border-radius: 8px; text-align: center; color: #ffffff;">
      <h2 style="margin: 0; font-size: 20px;">Sreyas Institute of Engineering & Technology</h2>
      <p style="margin: 4px 0 0; font-size: 13px; opacity: 0.9;">General Enquiry Acknowledgement</p>
    </div>

    <div style="padding: 20px 0;">
      <p>Dear <strong>{student_name}</strong>,</p>
      <p>Thank you for asking your question. We have informed the admissions desk staff and assigned your query a tracking reference ID.</p>

      <div style="background: #e8f0fe; border-left: 4px solid #1a73e8; padding: 14px; margin: 16px 0; border-radius: 4px;">
        <p style="margin: 0; font-size: 13px; color: #5f6368;">Reference ID:</p>
        <p style="margin: 2px 0 8px; font-size: 18px; font-weight: bold; color: #1a73e8;">{reference_id}</p>
        <p style="margin: 0; font-size: 13px; color: #5f6368;">Your Question:</p>
        <p style="margin: 2px 0 0; font-size: 14px; color: #202124;">{question_or_course}</p>
      </div>

      <p>Our admissions desk will reply to your question within 24 hours.</p>
    </div>

    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;" />
    <div style="font-size: 12px; color: #5f6368; text-align: center;">
      <p style="margin: 2px 0;">📞 Helpline: {config.COLLEGE_PHONE} | ✉️ Email: {config.COLLEGE_EMAIL}</p>
      <p style="margin: 2px 0;">📍 Nagole, Hyderabad, Telangana</p>
      <p style="margin: 2px 0;"><a href="{config.COLLEGE_WEBSITE}" style="color: #1a73e8; text-decoration: none;">www.sreyas.ac.in</a></p>
    </div>
  </div>
</body>
</html>
"""

        try:
            thread = threading.Thread(
                target=cls._send_email_task,
                args=(recipients, subject, html_body, text_body),
                daemon=True,
            )
            thread.start()
            logger.info("Enquiry acknowledgement email task initiated for %s", recipients)
            return True
        except Exception as err:
            logger.error("Failed to start email thread for %s: %s", recipients, err)
            return False

    @classmethod
    def send_admission_acknowledgement(
        cls,
        student_email: str,
        student_name: str,
        reference_id: str,
        course: str,
        parent_name: Optional[str] = None,
        parent_phone: Optional[str] = None,
        percentage: Optional[str] = None,
        entrance_rank: Optional[str] = None,
        quota: Optional[str] = None,
        parent_email: Optional[str] = None,
    ) -> None:
        """
        Dispatch acknowledgement email specifically for B.Tech Admission Application.
        """
        recipients = [student_email.strip()]
        if parent_email and parent_email.strip() and "@" in parent_email:
            recipients.append(parent_email.strip())

        subject = f"B.Tech Admission Application Received – Sreyas Institute ({reference_id})"

        text_body = f"""Dear {student_name},

Thank you for filling out the B.Tech Admission Application Form! We appreciate your interest in Sreyas Institute of Engineering and Technology (SIET).

We have received your admission application and registered it under Reference ID: {reference_id}.

Application Details:
- Application Ref ID: {reference_id}
- Student Name: {student_name}
- Preferred Branch: {course}
- Admission Category: {quota or 'B.Tech Regular'}
- 12th / Intermediate Marks: {percentage}%
- Entrance Exam Rank: {entrance_rank or 'N/A'}
- Parent / Guardian Name: {parent_name or 'N/A'}
- Parent Phone: {parent_phone or 'N/A'}

Our admissions committee is reviewing your details and will contact you within 24 hours regarding seats, fee structure, and document submission.

Best regards,
Admissions Committee
Sreyas Institute of Engineering and Technology
Nagole, Hyderabad
Phone: {config.COLLEGE_PHONE} | Email: {config.COLLEGE_EMAIL}
Website: {config.COLLEGE_WEBSITE}
"""

        html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6; background-color: #f4f6f9; padding: 20px;">
  <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; padding: 24px; border: 1px solid #e2e8f0;">
    <div style="background: linear-gradient(135deg, #1a73e8, #1557b0); padding: 18px; border-radius: 8px; text-align: center; color: #ffffff;">
      <h2 style="margin: 0; font-size: 20px;">🎓 Sreyas Institute of Engineering & Technology</h2>
      <p style="margin: 4px 0 0; font-size: 13px; opacity: 0.95;">B.Tech Admission Application Acknowledgement</p>
    </div>

    <div style="padding: 20px 0;">
      <p>Dear <strong>{student_name}</strong>,</p>
      <p>Thanks for filling out the B.Tech Admission Application Form! Thank you for your interest in joining Sreyas Institute.</p>

      <div style="background: #f8f9fa; border: 1.5px solid #1a73e8; padding: 16px; margin: 16px 0; border-radius: 8px;">
        <p style="margin: 0; font-size: 12px; color: #5f6368; text-transform: uppercase; font-weight: bold;">Application Reference ID:</p>
        <p style="margin: 2px 0 10px; font-size: 20px; font-weight: bold; color: #1a73e8;">{reference_id}</p>

        <table style="width: 100%; font-size: 13px; color: #202124; border-collapse: collapse;">
          <tr><td style="padding: 4px 0; font-weight: bold;">Branch:</td><td>{course}</td></tr>
          <tr><td style="padding: 4px 0; font-weight: bold;">Quota / Category:</td><td>{quota or 'Regular'}</td></tr>
          <tr><td style="padding: 4px 0; font-weight: bold;">12th / Inter Marks:</td><td>{percentage}%</td></tr>
          <tr><td style="padding: 4px 0; font-weight: bold;">Entrance Rank:</td><td>{entrance_rank or 'N/A'}</td></tr>
          <tr><td style="padding: 4px 0; font-weight: bold;">Parent Name:</td><td>{parent_name or 'N/A'}</td></tr>
          <tr><td style="padding: 4px 0; font-weight: bold;">Parent Contact:</td><td>{parent_phone or 'N/A'}</td></tr>
        </table>
      </div>

      <p>Our admissions committee will review your application details and reach out to you within 24 hours.</p>
    </div>

    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;" />
    <div style="font-size: 12px; color: #5f6368; text-align: center;">
      <p style="margin: 2px 0;">📞 Admissions Helpline: {config.COLLEGE_PHONE} | ✉️ Email: {config.COLLEGE_EMAIL}</p>
      <p style="margin: 2px 0;">📍 Nagole, Hyderabad, Telangana - 500068</p>
      <p style="margin: 2px 0;"><a href="{config.COLLEGE_WEBSITE}" style="color: #1a73e8; text-decoration: none;">www.sreyas.ac.in</a></p>
    </div>
  </div>
</body>
</html>
"""

        try:
            thread = threading.Thread(
                target=cls._send_email_task,
                args=(recipients, subject, html_body, text_body),
                daemon=True,
            )
            thread.start()
            logger.info("Admission acknowledgement email task initiated for %s", recipients)
            return True
        except Exception as err:
            logger.error("Failed to start admission email thread for %s: %s", recipients, err)
            return False

    @classmethod
    def is_smtp_configured(cls) -> bool:
        """Checks if real SMTP credentials are configured in the environment."""
        return bool(config.SMTP_USER and config.SMTP_PASS and config.SMTP_HOST)

    @classmethod
    def send_parent_otp(
        cls,
        parent_email: str,
        parent_name: str,
        otp: str,
        expiry_minutes: int = 5,
    ) -> Tuple[bool, str]:
        """
        Dispatches a single-use verification OTP to the approved parent's real email.
        Enforces:
          - If SMTP is not configured, returns False with a clear honest explanation.
            Does NOT display fake OTPs or pretend email was sent.
          - If SMTP is configured, sends via smtplib (with TLS/SSL and retries).
        """
        email = (parent_email or "").strip()
        if not email or "@" not in email:
            return False, "Invalid email address format."

        if not cls.is_smtp_configured():
            logger.warning("Attempted to send Parent OTP to %s but SMTP is not configured.", email)
            return False, (
                "Email verification cannot be completed because SMTP server credentials (SMTP_USER / SMTP_PASS) "
                "are not configured in the college server environment (.env). "
                "Please configure valid SMTP credentials to enable live email delivery."
            )

        subject = f"🔐 Your Sreyas Parent Portal Verification Code: {otp}"

        text_body = f"""Dear {parent_name or 'Parent'},

Your parent registration request has been approved by the Sreyas Institute College Administration.

Your one-time verification code (OTP) is:

    {otp}

This code is valid for {expiry_minutes} minutes. Please enter this code in the Sreyas Parent Portal to activate your account and view your student's academic records.

If you did not request this code, please ignore this email or contact the college office.

Best regards,
Examination & Academic Branch
Sreyas Institute of Engineering and Technology
Nagole, Hyderabad - 500068
Phone: {config.COLLEGE_PHONE} | Email: {config.COLLEGE_EMAIL}
"""

        html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; color: #202124; line-height: 1.6; background-color: #f8fafc; padding: 20px;">
  <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 12px; padding: 28px; border: 1px solid #e2e8f0; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
    <div style="background: linear-gradient(135deg, #1a73e8, #1557b0); padding: 18px; border-radius: 8px; text-align: center; color: #ffffff;">
      <h2 style="margin: 0; font-size: 20px;">🎓 Sreyas Institute of Engineering & Technology</h2>
      <p style="margin: 4px 0 0; font-size: 13px; opacity: 0.95;">Parent Academic Verification Portal</p>
    </div>

    <div style="padding: 24px 0;">
      <p style="font-size: 15px;">Dear <strong>{parent_name or 'Parent'}</strong>,</p>
      <p>Your parent registration request has been approved by the college administration. Please use the following one-time verification code (OTP) to activate your account and view your linked student's academic records:</p>

      <div style="background: #eff6ff; border: 2px dashed #1a73e8; padding: 18px; margin: 20px 0; border-radius: 10px; text-align: center;">
        <span style="font-size: 11px; color: #5f6368; text-transform: uppercase; font-weight: bold; letter-spacing: 1px; display: block; margin-bottom: 6px;">Single-Use Verification Code</span>
        <span style="font-size: 32px; font-weight: 800; color: #1a73e8; letter-spacing: 6px; font-family: monospace;">{otp}</span>
        <span style="font-size: 12px; color: #d93025; display: block; margin-top: 8px; font-weight: 600;">Valid for {expiry_minutes} minutes only</span>
      </div>

      <p style="font-size: 13px; color: #5f6368;">For your security, do not share this code with anyone. This OTP can only be used once.</p>
    </div>

    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;" />
    <div style="font-size: 11.5px; color: #5f6368; text-align: center;">
      <p style="margin: 2px 0;">Sreyas Institute of Engineering and Technology</p>
      <p style="margin: 2px 0;">Nagole, Bandlaguda, Hyderabad, Telangana - 500068</p>
      <p style="margin: 2px 0;">Helpline: {config.COLLEGE_PHONE} | <a href="{config.COLLEGE_WEBSITE}" style="color: #1a73e8; text-decoration: none;">www.sreyas.ac.in</a></p>
    </div>
  </div>
</body>
</html>
"""
        # Synchronous worker call for OTP so we immediately know if delivery succeeded
        sent = cls._send_email_task([email], subject, html_body, text_body, attempts=2)
        if sent:
            return True, f"Verification OTP successfully sent to {email}."
        else:
            return False, f"Failed to deliver email to {email}. Please check SMTP connectivity or try again later."

