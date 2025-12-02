# src/graph.py

from typing import TypedDict, Literal
from langgraph.graph import StateGraph, START, END
import os
import requests
import io
import smtplib
import re   # 🔹 정규식 사용
from email.message import EmailMessage

from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ============================================================
# 1. Colab 서버 기본 설정 (.env에서 COLAB_API_BASE 읽기)
# ============================================================
COLAB_API_BASE = os.getenv("COLAB_API_BASE", "").rstrip("/")

ROUTE_ACTION_ENDPOINT = f"{COLAB_API_BASE}/route_action"
AGENT_CHAT_ENDPOINT = f"{COLAB_API_BASE}/agent_chat"


# ============================================================
# 2. PDF용 한글 폰트 등록
#    - 프로젝트 루트 기준 fonts/MALGUN.ttf 있다고 가정
# ============================================================
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")
FONT_PATH = os.path.join(FONT_DIR, "MALGUN.ttf")

if os.path.exists(FONT_PATH):
    try:
        pdfmetrics.registerFont(TTFont("KOREAN_FONT", FONT_PATH))
        print(f"[graph.py] ✅ PDF 한글 폰트 등록 완료: {FONT_PATH}")
    except Exception as e:
        print("[graph.py] ⚠️ 폰트 등록 실패:", e)
else:
    print("[graph.py] ⚠️ 경고: 한글 폰트 파일을 찾을 수 없습니다:", FONT_PATH)


# ============================================================
# 3. LangGraph 상태 정의
# ============================================================
class AgentState(TypedDict, total=False):   # TypeDict으로 해서 이 딕셔너리에는 아무 키나 넣는게 아니라 내가 정한 키만 들어갈 수 있음
    # 입력
    command: str           # 사용자가 채팅창에 입력한 문장
    bhc_di_en: str         # 영어 BHC/DI (있으면)
    bhc_di_ko: str         # 한국어 BHC/DI (있으면)

    # /route_action 결과
    action: Literal["none", "save_file", "send_email"]  # 해야할 일의 종류를 딱 3가지만 허용
    file_type: str         # "pdf" / "docx" / "none"
    email: str             # 이메일 주소 (없으면 "")

    # 실제 수행 결과
    file_path: str         # 생성된 파일 경로 (있다면)
    result_message: str    # 사용자에게 보여줄 최종 한국어 답변


# ============================================================
# 3-1. 응답에서 한국어 줄만 남기는 헬퍼 (개선됨)
# ============================================================
# [수정] src/graph.py 내부의 _keep_korean_lines 함수 교체

def _keep_korean_lines(text: str) -> str:
    """
    모델 응답에서 '영어 사고 과정'을 강력하게 제거하고 순수 한국어 답변만 남깁니다.
    """
    lines = text.splitlines()
    kept_lines = []
    
    # 1. <think> 태그가 있다면 그 안의 내용은 무조건 삭제
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 2. 해당 줄에 한글이 단 한 글자도 없다면 -> 무조건 삭제 (영어 문장)
        # (특수문자, 숫자, 영어로만 구성된 줄은 사고 과정일 확률이 99%)
        if not re.search(r"[\uAC00-\uD7A3]", stripped):
            continue

        # 3. 한글이 포함되어 있더라도, 영어 단어 비중이 너무 높으면 삭제
        # (예: "Okay, user asked about '췌장절제술'..." 같은 문장 제거)
        
        # 한글 글자 수 세기
        korean_count = len(re.findall(r"[\uAC00-\uD7A3]", stripped))
        # 전체 글자 수 (공백 제외)
        total_count = len(stripped.replace(" ", ""))
        
        if total_count > 0:
            korean_ratio = korean_count / total_count
            # 한글 비중이 20% 미만이면 영어 문장으로 간주하고 삭제
            # (단, 아주 짧은 문장은 제외 - 예: "수술입니다.")
            if total_count > 10 and korean_ratio < 0.2:
                continue

        kept_lines.append(stripped)

    result = "\n".join(kept_lines).strip()
    
    # 만약 다 지워버려서 남은 게 없다면? -> 혹시 모르니 원본 반환 (에러 방지)
    if not result:
        return text
        
    return result


# ============================================================
# 4. Colab 엔드포인트 호출 헬퍼들
# ============================================================
def _call_route_action(command: str, summary_ko: str) -> dict:
    """
    Colab의 /route_action 엔드포인트를 호출해서
    - action: "save_file" | "send_email" | "none"
    - file_type: "pdf" | "docx"
    - email: "..."
    을 받아온다.
    """
    if not COLAB_API_BASE:
        # 환경변수 문제
        return {"action": "none", "file_type": "none", "email": ""}

    payload = {
        "command": command,
        "summary_ko": summary_ko or "",
    }

    # 사용자가 000@gmail.com으로 pdf파일 보내달라 했을 경우 모델이 이를 판단해서
    # action:"send_email", file_type:"pdf", email:"000@gmail.com"으로 채워서 JSON을 돌려줌
    try:
        resp = requests.post(ROUTE_ACTION_ENDPOINT, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()  # AI가 작성해준 json값 도착
    except Exception as e:
        print(f"[graph.py] route_action 호출 실패: {e}")
        return {"action": "none", "file_type": "docx", "email": ""}

    # 모델이 보내준 json값을 변수에 옮겨 적음. 만약 없는 값이 있을경우 디폴트값으로 채워둠.
    action = data.get("action", "none")
    if action not in ["save_file", "send_email", "none"]:
        action = "none"

    file_type = data.get("file_type", "docx")
    if file_type not in ["pdf", "docx"]:
        file_type = "docx" if action != "none" else "none"

    email = (data.get("email") or "").strip()

    return {
        "action": action,
        "file_type": file_type,
        "email": email,
    }


def _call_agent_chat_for_reply(
    command: str,
    bhc_di_ko: str,
    action: str,
    file_type: str,
    email: str,
) -> str:
    """
    Colab의 /agent_chat 엔드포인트를 호출해서
    사람에게 보여 줄 한국어 답변 한 덩어리만 받아온다.
    (영어 사고 과정/JSON 없이 → 한국어 줄만 최종 필터링)
    """
    if not COLAB_API_BASE:
        return "서버 설정(COLAB_API_BASE)에 문제가 있어서 답변을 생성할 수 없습니다. 관리자에게 문의해 주세요."

    # 공통 지침: 한국어만 사용, 사고 과정 출력 금지, 사용자 입력 반복 금지
# [수정] common_instruction 변경
    common_instruction = """
STRICT INSTRUCTION:
1. Output ONLY the final answer in Korean.
2. DO NOT include any reasoning, thinking process, or English explanation.
3. DO NOT repeat the user's question.
4. Just give the answer directly.
    """.strip()

    # 액션 종류에 따라 프롬프트를 다르게 구성
    # 그냥 일반 챗봇 모드
    if action == "none":
        prompt = f"""
You are a medical/general knowledge chatbot.
{common_instruction}

[사용자 입력]
{command}

[한국어 BHC/DI 요약]
{bhc_di_ko or "(아직 생성되지 않았음)"}
        """.strip()
    # 파일 생성 요청인 경우
    elif action == "save_file":
        prompt = f"""
You are a medical chatbot.
{common_instruction}

Context: The system is creating a "{file_type}" file for the discharge summary.
Your role: Briefly and politely explain in Korean that the file is being created.

[사용자 입력]
{command}
        """.strip()

    # 이메일 전송 요청인 경우
    elif action == "send_email":
        prompt = f"""
You are a medical chatbot.
{common_instruction}

Context: The system is sending a "{file_type}" file to "{email}".
Your role: Briefly and politely explain in Korean that the email is being sent.

[사용자 입력]
{command}
        """.strip()
    else:
        # 방어용
        prompt = f"""
You are a chatbot.
{common_instruction}

[사용자 입력]
{command}
        """.strip()

    payload = {"prompt": prompt}
    try:
        resp = requests.post(AGENT_CHAT_ENDPOINT, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        reply = (data.get("response") or "").strip()
    except Exception as e:
        return f"답변 생성 중 오류가 발생했습니다: {e}"

    if not reply:
        reply = "답변 생성에 문제가 발생했습니다. 다시 한 번 시도해 주세요."

    # 🔹 영어 사고과정/설명 줄 제거 → 한글 포함 줄만 남기기
    reply = _keep_korean_lines(reply)

    return reply


# ============================================================
# 5. 파일 생성 + 이메일 전송 헬퍼들 (예전 graph.py 기능 유지)
# ============================================================
def create_docx_file(text: str, out_path: str) -> None:
    """한국어 텍스트를 DOCX 파일로 저장."""
    # 1. 폴더가 없을경우 에러 방지
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # 2. 빈 워드 문서 하나 생성
    doc = Document()
    # 3. 텍스트를 한 줄씩 읽어서 '문단(Paragraph)'으로 추가
    for line in text.splitlines():
        doc.add_paragraph(line)
    # 저장
    doc.save(out_path)


def create_pdf_file(text: str, out_path: str) -> None:
    """한국어 텍스트를 PDF 파일로 저장 (자동 줄바꿈 포함)."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # A4용지 준비
    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4

    # 여백 설정
    left_margin = 40
    right_margin = 40
    top_margin = 40
    bottom_margin = 40

    # 폰트 설정
    font_name = "KOREAN_FONT" if "KOREAN_FONT" in pdfmetrics.getRegisteredFontNames() else "Helvetica"
    font_size = 11

    def wrap_line(line: str, max_width: float):
        """
        한 줄(line)을 max_width 안에 들어가도록 여러 줄로 잘라주는 헬퍼.
        공백 기준으로 단어 단위 나눔.
        """
        # 1. 문장을 단어 단위로 쪼갬
        words = line.split(" ")
        current = ""
        for w in words:
            # 2. 단어를 하나씩 붙여봄
            candidate = (current + " " + w).strip() if current else w
            # w_width를 기준으로 길이를 잼
            w_width = pdfmetrics.stringWidth(candidate, font_name, font_size)
            # 종이 여백(max_width) 보다 짧으면 계속 붙임
            if w_width <= max_width:
                current = candidate
            # 종이보다 길어지면 지금까지 붙인거 내리고(yield) 다음 줄로 넘어감
            else:
                if current:
                    yield current   # 현재 줄 반환
                current = w         # 넘친 단어는 다음 줄의 시작이 됨
        if current:
            yield current

    usable_width = width - left_margin - right_margin

    # 페이지 넘쳤을 경우
    # 글 쓸 위치 잡기(왼쪽 위)
    text_obj = c.beginText()
    text_obj.setTextOrigin(left_margin, height - top_margin)
    text_obj.setFont(font_name, font_size)

    for line in text.splitlines():
        if not line.strip():
            # 빈 줄은 한 줄 띄우기
            text_obj.textLine("")
        else:
            for wrapped in wrap_line(line, usable_width):
                text_obj.textLine(wrapped)
                # 2. 페이지 끝 검사
                # 현재 Y좌표(text_obj.getY())가 바닥 여백 (bottom_margin)보다 밑으로 내려 갔는가?
                if text_obj.getY() < bottom_margin:
                    c.drawText(text_obj)        # 지금까지 쓴 거 종이에 작성
                    c.showPage()                # 새 종이 넘기기(New Page)
                    text_obj = c.beginText()
                    text_obj.setTextOrigin(left_margin, height - top_margin)
                    text_obj.setFont(font_name, font_size)

    c.drawText(text_obj)
    c.save()


def send_email_with_attachment(
    to_email: str,
    subject: str,
    body: str,
    filename: str,
    file_bytes: bytes,
) -> None:
    """Gmail SMTP로 첨부파일 메일 전송."""
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")

    if not smtp_user or not smtp_password:
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD 환경변수가 설정되어 있지 않습니다.")

    msg = EmailMessage()
    msg["From"] = smtp_user     # 보내는 사람
    msg["To"] = to_email        # 받는 사람
    msg["Subject"] = subject    # 제목
    msg.set_content(body)       # 본문 내용

    msg.add_attachment(
        file_bytes,                 # 1. 파일의 실제 내용
        maintype="application",     # 2. 대분류(pdf인지 docx인지)
        subtype="octet-stream",     # 3. 소분류(모를경우 아무튼 바이너리 파일이야 라는 뜻)
        filename=filename,          # 4. 파일 이름
    )

    # SMTP주소를 이용해 gmail보냄.
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)


# ============================================================
# 6. LangGraph 그래프 정의
#    - 1단계: /route_action 으로 액션 분류
#    - 2단계: /agent_chat 으로 한국어 답변 + 실제 파일/메일 수행
# ============================================================
def get_graph():
    """
    LangGraph 기반 에이전트.

    - 입력: command(사용자 입력), bhc_di_ko, bhc_di_en
    - 1단계: /route_action 으로 action / file_type / email 분류
    - 2단계: /agent_chat 으로 사람에게 보여 줄 한국어 답변 생성
              + action에 따라 실제 파일 생성 / 이메일 전송 수행
    """

    # 1) 액션 분류 노드
    def classify_node(state: AgentState) -> AgentState:
        # 1. 재료 꺼냄(사용자 명령 + 요약본)
        command = state.get("command", "") or ""
        bhc_di_ko = state.get("bhc_di_ko", "") or ""

        # 2. Qwen3에게 물어봄(사용자의 의도가 무엇인지)
        try:
            # 의도가 뭔지 Qwen이 파악해서 돌려줌ㄴ
            result = _call_route_action(command, bhc_di_ko)
        except Exception as e:
            # 분류 실패 시 → 일반 대화 모드로
            return {
                "action": "none",
                "file_type": "none",
                "email": "",
                "result_message": f"요청 분석 중 오류가 발생했습니다: {e}",
            }

        # 3. 결과
        return {
            "action": result["action"],
            "file_type": result["file_type"],
            "email": result["email"],
        }

    # 2) 답변 생성 + 실제 액션 수행 노드
    def chat_and_act_node(state: AgentState) -> AgentState:
        command = state.get("command", "") or ""
        bhc_di_ko = state.get("bhc_di_ko", "") or ""
        # 1. 계획 확인
        action = state.get("action", "none") or "none"
        file_type = (state.get("file_type") or "none").lower()
        email = state.get("email", "") or ""
        prev_message = state.get("result_message", "") or ""

        # 출력물이 필요한데 요약이 없으면 action 무시
        if action in ["save_file", "send_email"] and not bhc_di_ko.strip():
            reply = (
                "아직 생성된 BHC/DI 출력물이 없습니다.\n"
                "왼쪽 패널에서 먼저 퇴원 요약을 생성하신 후 다시 요청해 주세요."
            )
            return {
                "action": "none",
                "file_type": "none",
                "email": "",
                "file_path": "",
                "result_message": reply,
            }

        # 1) 한국어 답변 생성
        try:
            reply = _call_agent_chat_for_reply(
                command=command,
                bhc_di_ko=bhc_di_ko,
                action=action,
                file_type=file_type,
                email=email,
            )
        except Exception as e:
            reply = f"답변 생성 중 오류가 발생했습니다: {e}"

        result_message = reply
        file_path = ""

        # 2) 실제 파일 생성 / 이메일 전송
        base_dir = os.path.join(os.getcwd(), "generated")
        os.makedirs(base_dir, exist_ok=True)

        # 파일 생성이 action일 경우
        if action == "save_file":
            # 기본값: docx
            if file_type == "pdf":
                file_path = os.path.join(base_dir, "discharge_summary.pdf")
                create_pdf_file(bhc_di_ko, file_path)
            else:
                file_type = "docx"
                file_path = os.path.join(base_dir, "discharge_summary.docx")
                create_docx_file(bhc_di_ko, file_path)

            result_message += f"\n\n(✅ 파일이 생성되었습니다: {os.path.basename(file_path)})"

        # 이메일 보내기가 action일 경우
        elif action == "send_email":
            # 먼저 파일 하나 생성
            if file_type == "pdf":
                file_path = os.path.join(base_dir, "discharge_summary_email.pdf")
                create_pdf_file(bhc_di_ko, file_path)
            else:
                file_type = "docx"
                file_path = os.path.join(base_dir, "discharge_summary_email.docx")
                create_docx_file(bhc_di_ko, file_path)

            if not email:
                result_message += "\n\n(⚠️ 이메일 주소를 인식하지 못해 실제 전송은 하지 못했습니다.)"
            else:
                try:
                    with open(file_path, "rb") as f:
                        file_bytes = f.read()

                    send_email_with_attachment(
                        to_email=email,
                        subject="퇴원 요약 및 지침서",
                        body="첨부된 퇴원 요약/지침서를 확인해 주세요.",
                        filename=os.path.basename(file_path),
                        file_bytes=file_bytes,
                    )
                    result_message += f"\n\n(📧 {email} 주소로 파일을 전송했습니다.)"
                except Exception as e:
                    result_message += f"\n\n(⚠️ 이메일 전송 중 오류가 발생했습니다: {e})"

        # action 이 none이면 그냥 챗봇 답변만 반환
        return {
            "action": action,
            "file_type": file_type,
            "email": email,
            "file_path": file_path,
            "result_message": result_message,
        }

    # 1. 빈 그래프판 준비
    workflow = StateGraph(AgentState)                       
    # 2. 노드 올리기(이름 붙이기)
    workflow.add_node("classify", classify_node)       
    workflow.add_node("chat_and_act", chat_and_act_node)

    # 3. 화살표 긋기(Edge 연결)
    workflow.add_edge(START, "classify")            # 시작 -> 분류
    workflow.add_edge("classify", "chat_and_act")   # 분류 -> 실행
    workflow.add_edge("chat_and_act", END)          # 실행 -> 종료

    # 4. 완성품 포장(Compile)
    app = workflow.compile()
    return app