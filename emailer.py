import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def send_digest(candidates: List[Dict[str, Any]], run_id: str, topic: str) -> bool:
    smtp_host = _env("SMTP_HOST")
    smtp_port = int(_env("SMTP_PORT", "587"))
    smtp_user = _env("SMTP_USER")
    smtp_password = _env("SMTP_PASSWORD")
    email_from = _env("EMAIL_FROM", smtp_user)
    email_to = _env("EMAIL_TO", smtp_user)

    if not smtp_host or not smtp_user or not smtp_password:
        return False

    base_url = _env("BASE_URL", "http://localhost:3010")

    rows = ""
    for c in candidates[:10]:
        title = (c.get("title") or "Untitled").replace("<", "&lt;")
        source = (c.get("source") or "unknown").replace("<", "&lt;")
        score = c.get("relevance_score", 0)
        summary = ((c.get("summary") or "")[:200]).replace("<", "&lt;")
        url = c.get("url", "")
        rows += f"""
    <tr>
      <td style="padding:8px;border-bottom:1px solid #e5e7eb;">
        <strong>[{score:.1f}]</strong> {title}<br/>
        <span style="color:#6b7280;font-size:13px;">{source} — {summary}</span><br/>
        <a href="{url}" style="font-size:13px;">Read article</a>
      </td>
    </tr>"""

    html = f"""<html><body style="font-family:sans-serif;max-width:640px;margin:0 auto;padding:16px;">
<h2>Article Pipeline Digest</h2>
<p>Topic: <strong>{topic}</strong></p>
<p>{len(candidates)} candidates found in scheduled run <code>{run_id}</code>.</p>
<table style="width:100%;border-collapse:collapse;">{rows}</table>
<p style="margin-top:16px;">
  <a href="{base_url}/?run={run_id}" style="display:inline-block;padding:10px 20px;background:#0e7490;color:#fff;border-radius:8px;text-decoration:none;">Review & Generate Draft</a>
  &nbsp;
  <a href="{base_url}/api/scheduled-runs/{run_id}/skip" style="display:inline-block;padding:10px 20px;color:#6b7280;text-decoration:none;">Skip This Batch</a>
</p>
<p style="color:#9ca3af;font-size:12px;margin-top:24px;">Article Pipeline — automated digest</p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Article Pipeline — {len(candidates)} candidates for {run_id}"
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(email_from, [email_to], msg.as_string())
        return True
    except Exception:
        return False
