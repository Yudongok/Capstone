# src/llm.py

import os
import requests
from dataclasses import dataclass

# ✅ .env 에서 COLAB_API_BASE 읽기 (예: "https://xxxx.ngrok-free.dev")
COLAB_API_BASE = os.getenv("COLAB_API_BASE", "").rstrip("/")


@dataclass
class RemoteColabLLM:
    """
    Colab에서 FastAPI + ngrok으로 띄운 Qwen3-8B 에이전트 모델을
    HTTP로 호출하는 간단한 래퍼입니다.
    - LangGraph 에이전트(graph.py)에서 사용합니다.
    - 엔드포인트: POST {COLAB_API_BASE}/agent_chat
    - 입력: {"prompt": "..."}  (system+user 프롬프트를 합친 것)
    - 출력: {"response": "..."}  (모델의 텍스트 응답)
    """
    base_url: str = COLAB_API_BASE
    timeout: int = 120  # 에이전트 응답 여유 있게

    @property
    def endpoint(self) -> str:
        if not self.base_url:
            raise RuntimeError("COLAB_API_BASE 환경변수가 설정되어 있지 않습니다.")
        return self.base_url.rstrip("/") + "/agent_chat"

    def generate(self, prompt: str) -> str:
        """
        Colab 서버로 프롬프트를 보내고, 'response' 필드를 그대로 받아옵니다.
        """
        payload = {"prompt": prompt}
        resp = requests.post(self.endpoint, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        # Colab에서 return {"response": response_text}
        return data.get("response", "").strip()
