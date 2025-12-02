# src/tools.py

import os
import io
import smtplib
from email.message import EmailMessage
from datetime import datetime

from dotenv import load_dotenv
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "..", "outputs")


def _ensure_output_dir() -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def save_summary_to_file(content: str, file_type: str = "docx") -> str:
    """
    BHC/DI 한국어 텍스트를 파일로 저장하고 파일 경로를 반환합니다.
    file_type: 'docx' 또는 'pdf'
    """
    _ensure_output_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if file_type == "pdf":
        filename = f"discharge_summary_{ts}.pdf"
        path = os.path.join(OUTPUT_DIR, filename)

        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        x = 40
        y = height - 40
        for line in content.splitlines():
            if y < 40:
                p.showPage()
                y = height - 40
            p.drawString(x, y, line)
            y -= 14
        p.showPage()
        p.save()

        with open(path, "wb") as f:
            f.write(buffer.getvalue())

    else:
        filename = f"discharge_summary_{ts}.docx"
        path = os.path.join(OUTPUT_DIR, filename)

        doc = Document()
        for line in content.splitlines():
            doc.add_paragraph(line)
        doc.save(path)

    return path


def send_summary_email_with_file(
    to_email: str,
    subject: str,
    body: str,
    content: str,
    file_type: str = "docx",
) -> str:
    """
    요약 내용을 파일로 저장하고, 해당 파일을 첨부하여 이메일을 전송합니다.
    .env에 SMTP_USER, SMTP_PASSWORD (앱 비밀번호) 필요.
    """
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")

    if not smtp_user or not smtp_password:
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD 환경변수가 설정되어 있지 않습니다.")

    path = save_summary_to_file(content, file_type)
    filename = os.path.basename(path)

    with open(path, "rb") as f:
        file_bytes = f.read()

    msg = EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    msg.add_attachment(
        file_bytes,
        maintype="application",
        subtype="octet-stream",
        filename=filename,
    )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)

    return f"Email successfully sent to {to_email} with attachment {filename}."
