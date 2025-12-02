# streamlit.py

import os
import requests
import streamlit as st
from dotenv import load_dotenv

# ------------------------
# 환경변수 / Colab 엔드포인트
# ------------------------
load_dotenv()

from src.graph import get_graph


# Colab 주소 가져오기 (없으면 에러 발생)
COLAB_API_BASE = os.getenv("COLAB_API_BASE", "").rstrip("/")
if not COLAB_API_BASE:
    raise RuntimeError("COLAB_API_BASE가 설정되어 있지 않습니다. .env 파일을 확인하세요.")

GENERATE_KO_ENDPOINT = f"{COLAB_API_BASE}/generate_ko"

# ------------------------
# 세션 상태 초기화
# ------------------------
# BHC/DI 결과물 저장소 (영어/한국어)
if "bhc_di_en" not in st.session_state:
    st.session_state["bhc_di_en"] = ""
if "bhc_di_ko" not in st.session_state:
    st.session_state["bhc_di_ko"] = ""
# LangGraph 에이전트 인스턴스 (매번 새로 만들면 비효율적이라 지정)
if "agent_app" not in st.session_state:
    st.session_state["agent_app"] = get_graph()
# 채팅 기록(사용자 질문 + 에이전트 답변)
if "chat_messages" not in st.session_state:
    # role: "user" or "assistant", content: str
    st.session_state["chat_messages"] = []
# LangGraph에서 생성한 파일(있다면) 저장, 다운로드 버튼용
# {"bytes": ..., "name": ..., "mime": ...}
if "last_file" not in st.session_state:
    st.session_state["last_file"] = None

agent_app = st.session_state["agent_app"]

# ------------------------
# Qwen2에게 줄 BHC/DI 지시문
# ------------------------
BHC_DI_PROMPT = """
You are an expert physician-writer who crafts hospital discharge summaries.

OBJECTIVE
- For this admission, write BOTH:
  1) Brief Hospital Course
  2) Discharge Instructions

EVIDENCE RULES
- Use ONLY facts explicitly present in the input for this admission.
- Do NOT invent diagnoses, tests, medications, or follow-up items that are not stated.
- Preserve all medication names, doses, units, and frequencies exactly as written when you cite them.

STYLE
- English, clinical, crisp, and readable.
- Prefer short paragraphs or bullet points.
- Maintain clear chronology (presentation → key findings → workup → treatments/changes → course → discharge condition).
- Avoid narrative fluff and repetition.

OUTPUT FORMAT
- Your output MUST contain exactly two top-level headings, in this order:

Brief Hospital Course
[Write the hospital course for this admission only.]

Discharge Instructions
[Write patient-facing, concise instructions strictly based on the input orders/medications/instructions.]

- Do NOT add any other top-level headings.
- Do NOT include comments about what you are doing.

Below is the raw chart for this admission. Use it to write the two sections above.
"""

# ------------------------
# UI 설정 + CSS
# ------------------------
st.set_page_config(
    page_title="BHC / Discharge Instructions (Qwen2 → Qwen3)",
    layout="wide",
)

# 말풍선 + 스크롤 영역 스타일
st.markdown(
    """
    <style>
    .chat-container {
        height: 70vh;                 /* 고정 높이: 내부 스크롤 */
        overflow-y: auto;
        padding: 8px;
        padding-right: 12px;
        border: 1px solid #E0E0E0;
        border-radius: 8px;
        background-color: #F9F9F9;
    }
    .bubble-user {
        background-color: #DCF8C6;
        color: #000000;
        padding: 8px 12px;
        border-radius: 12px;
        margin: 4px 0;
        margin-left: 40px;
        text-align: left;
        word-break: break-word;
        white-space: pre-wrap;
    }
    .bubble-assistant {
        background-color: #FFFFFF;
        color: #000000;
        padding: 8px 12px;
        border-radius: 12px;
        margin: 4px 0;
        margin-right: 40px;
        border: 1px solid #E0E0E0;
        word-break: break-word;
        white-space: pre-wrap;
    }
    .bubble-role-user {
        text-align: right;
        font-size: 0.8rem;
        color: #222222;
        margin-top: 8px;
    }
    .bubble-role-assistant {
        text-align: left;
        font-size: 0.8rem;
        color: #222222;
        margin-top: 8px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🏥 BHC & Discharge Instructions Generator")

st.markdown(
    """
파인튜닝된 **Qwen2** 모델이 영어 BHC/DI를 생성하고,  
**Qwen3** 모델이 이를 한국어로 번역한 결과를 보여줍니다.

> ⚠️ 실제 의무기록으로 사용하기 전에는 반드시 담당 의사가 최종 검토해야 합니다.
"""
)

# ------------------------
# 레이아웃: 왼쪽 = 생성기, 오른쪽 = 챗봇
# ------------------------
left_col, right_col = st.columns([2, 1])    # 화면을 2:1 비율로 나눔

# ============================
#  왼쪽: BHC/DI 생성기
# ============================
with left_col:
    st.subheader("📄 BHC / Discharge Instructions 생성")

    # 텍스트 입력창
    patient_info = st.text_area(
        "환자 정보 입력 (EMR 요약, 진행 기록, 수술/처치, 약물/지시사항 등)",
        height=400,
        placeholder="EMR에서 복사한 텍스트를 그대로 붙여 넣으세요.",
    )

    run_button = st.button("BHC + DI 생성", type="primary")

    def call_generate_ko(raw_emr: str) -> dict:
        """Colab 서버의 /generate_ko 엔드포인트 호출"""
        # 프롬프트 조정
        full_prompt = BHC_DI_PROMPT.strip() + "\n\n===== RAW ADMISSION NOTES =====\n\n" + raw_emr
        # 데이터 포장(JSON)
        payload = {"prompt": full_prompt}
        # POST요청, timeout=300: AI 서버가 느리니까 5분으로 설정해줌
        resp = requests.post(GENERATE_KO_ENDPOINT, json=payload, timeout=300)
        # 받은 값 확인(에러 체크)
        resp.raise_for_status()
        # 받은 값 꺼내기
        return resp.json()

    # 버튼 클릭시 동작
    if run_button:
        # 값이 비어있는지 체크(방어 로직)
        if not patient_info.strip():
            st.warning("먼저 환자 정보를 입력해 주세요.")
        else:
            # 2. 대기 표시(UX)
            with st.spinner("Qwen2로 영어 BHC/DI 생성 후 Qwen3로 한국어 번역 중입니다..."):
                try:
                    # 3. 실제 Colab API와 통신
                    data = call_generate_ko(patient_info)
                except Exception as e:
                    # 4. 실패시 에러 표시
                    st.error(f"Colab 서버 호출 중 오류가 발생했습니다: {e}")
                # else문을 try밖으로 따로 뺀 이유는 만약 같이 넣게 되면 개발자 실수로 에러가 날 때 실제론 내 코드 오류인데 사용자에게는 Colab서버 오류라고 거짓말하게 됨.
                else:
                    # 성공시 데이터 저장(Session State)
                    st.session_state["bhc_di_en"] = (data.get("response_en") or "").strip()
                    st.session_state["bhc_di_ko"] = (data.get("response_ko") or "").strip()
                    # 새 출력물을 생성했으니, 이전 파일은 초기화
                    st.session_state["last_file"] = None

    # 결과 표시
    if st.session_state["bhc_di_ko"]:
        st.subheader("📝 한국어 입원 경과 요약 + 퇴원 지침")
        st.write(st.session_state["bhc_di_ko"])

    if st.session_state["bhc_di_en"]:
        with st.expander("🔍 Qwen2가 생성한 영어 BHC / DI 보기"):
            st.write(st.session_state["bhc_di_en"])

# ============================
#  오른쪽: 에이전트 "채팅" UI
# ============================
with right_col:
    st.subheader("🤖 에이전트 챗봇 (파일 생성 / 이메일 전송 / 일반 대화)")

    # 1) 채팅 말풍선 렌더링 (고정 높이 + 내부 스크롤), HTML로 말풍선 조립
    chat_html = ['<div class="chat-container">']
    for msg in st.session_state["chat_messages"]:   # st.session은 대화 기록이 저자오딘 리스트. 이걸 하나씩 꺼내서 HTML태그<div>로 감쌈
        if msg["role"] == "user":
            # 사용자 말풍선 (오른쪽, 초록색)
            chat_html.append('<div class="bubble-role-user">👤 사용자</div>')
            chat_html.append(f'<div class="bubble-user">{msg["content"]}</div>')
        else:
            # 에이전트 말풍선 (왼쪽, 흰색)
            chat_html.append('<div class="bubble-role-assistant">🤖 에이전트</div>')
            chat_html.append(f'<div class="bubble-assistant">{msg["content"]}</div>')
    chat_html.append("</div>")
    # 화면에 실제 표시
    st.markdown("".join(chat_html), unsafe_allow_html=True) # usafe_allow_html은 기본적으로 Streamlit이 보안때문에 HTML코드를 막아두는데 이 말풍선에 CSS를 적용하려면 이 옵션을 켜서 허락해야함.

    # 1-1) LangGraph에서 생성된 파일이 있으면, 항상 다운로드 버튼 제공
    # Streamlit은 버튼을 누를 때마다 화면이 새로고침된다. 파일을 생성한 직/후에는 버튼이 보이지만 채팅을 한번 더 치면 사라질 수 있음.
    # 그래서 st.sesstion_state["last_file"]에 파일 데이터를 박제해두고, 저장된 파일이 있으면 무조건 버튼을 그리라는 명령어임.
    last_file = st.session_state.get("last_file")
    if last_file is not None:
        st.download_button(
            label="생성된 파일 다운로드",
            data=last_file["bytes"],
            file_name=last_file["name"],
            mime=last_file["mime"],
            key=f"download_{last_file['name']}",
        )

    # 2) 에이전트 처리 함수 (엔터 / 버튼 공용)
    def handle_agent_message():
        # 1. 입력값 가져오기
        user_cmd = st.session_state.get("agent_command_input", "").strip()
        if not user_cmd:    # 빈칸이면 무시
            return

        bhc_di_en = st.session_state["bhc_di_en"]
        bhc_di_ko = st.session_state["bhc_di_ko"]

        # 2. 문맥 검사
        # needs_output으로 특정 단어가 들어가 있는지 확인
        needs_output = any(
            kw in user_cmd
            for kw in ["파일", "pdf", "PDF", "docx", "워드", "문서", "이메일", "메일", "메일로", "@"]
        )
        # 사용자가 파일이나 메일은 언급했는데 아직 요약문(bhc_di_ko)가 없을 경우
        if needs_output and not bhc_di_ko.strip():
            # 아직 요약문이 없다며 종료 시킴
            st.session_state["chat_messages"].append(
                {"role": "user", "content": user_cmd}
            )
            assistant_text = (
                "아직 생성된 BHC/DI 출력물이 없습니다.\n"
                "왼쪽 패널에서 먼저 퇴원 요약을 생성하신 다음에 다시 시도해 주세요."
            )
            st.session_state["chat_messages"].append(
                {"role": "assistant", "content": assistant_text}
            )
            st.session_state["agent_command_input"] = ""
            return

        # 3. 사용자 메시지 기록
        st.session_state["chat_messages"].append(
            {"role": "user", "content": user_cmd}
        )

        # 4. LangGraph 에이전트 호출
        with st.spinner("LangGraph 에이전트가 요청을 처리하는 중입니다..."):
            try:
                final_state = agent_app.invoke(
                    {
                        "command": user_cmd,        # ex) "pdf만들어줘"
                        "bhc_di_ko": bhc_di_ko,     # 한국어 요약 내용
                        "bhc_di_en": bhc_di_en,     # 영어 요약 내용
                    }
                )
            except Exception as e:
                assistant_text = f"에이전트 실행 중 오류가 발생했습니다: {e}"
                st.error(assistant_text)
                st.session_state["chat_messages"].append(
                    {"role": "assistant", "content": assistant_text}
                )
            else:
                # 5. 성공 시 결과 처리
                # 에이전트가 action, file_type, email, file_path, result_message를 json형식으로 줌
                action = final_state.get("action", "none")
                file_type = final_state.get("file_type", "docx")
                email = (final_state.get("email") or "").strip()
                file_path = final_state.get("file_path", "")
                result_message = final_state.get("result_message", "")

                # 자연어 답변
                assistant_text = result_message or f"- action: {action}\n- file_type: {file_type}\n- email: {email or '(없음)'}"
                st.session_state["chat_messages"].append(
                    {"role": "assistant", "content": assistant_text}
                )

                # LangGraph가 실제로 파일을 생성했다면, 읽어서 세션에 저장 → 다운로드 버튼 표시
                if file_path and os.path.exists(file_path):
                    # 파일을 읽어서 메모리(RAM)에 올림 -> 다운로드 버튼용
                    with open(file_path, "rb") as f:
                        file_bytes = f.read()

                    if file_path.lower().endswith(".pdf"):
                        mime = "application/pdf"    # pdf일 경우 크롬 브라우저에서 열어달라는 의미
                    else:
                        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"    # .docx라면 워드 아이콘 보여달라는 뜻

                    # 세션에 저장
                    st.session_state["last_file"] = {
                        "bytes": file_bytes,
                        "name": os.path.basename(file_path),
                        "mime": mime,
                    }

        # 입력창 비우기
        st.session_state["agent_command_input"] = ""

    # 3) 입력창 + 버튼
    user_cmd = st.text_input(
        "에이전트에게 지시를 입력해 주세요.",
        placeholder="pdf만들기 / 이메일 보내기 / 질문",
        key="agent_command_input",
        on_change=handle_agent_message,  # ⏎ 엔터로 전송
    )

    st.button("보내기", on_click=handle_agent_message, key="agent_send_button")
