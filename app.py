# -*- coding: utf-8 -*-
"""錄音檔轉逐字稿與說話者識別 Web App（Gemini API + Streamlit）"""

import io
import os
import time

import streamlit as st
from google import genai
from google.genai import types

# ── 常數設定 ──────────────────────────────────────────────
MODELS = {
    "gemini-3.5-flash（快速・推薦）": "gemini-3.5-flash",
    "gemini-pro-latest（高品質・較慢）": "gemini-pro-latest",
}

MIME_MAP = {
    "mp3": "audio/mp3",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
}

INLINE_LIMIT_MB = 15  # 超過此大小改走 Files API（Gemini 單次請求上限 20MB）

TRANSCRIBE_PROMPT = """你是專業的逐字稿聽打員。請將這段音訊完整轉錄為逐字稿，並區分不同說話者（Speaker Diarization）。

輸出規則（務必嚴格遵守）：
1. 每個發言段落一行，格式為：[MM:SS] 說話者 A: 發言內容
2. 時間戳記為該段發言的開始時間，格式 [分:秒]，超過一小時用 [HH:MM:SS]。
3. 依出場順序將說話者命名為「說話者 A」「說話者 B」「說話者 C」……同一人全程使用同一代號。
4. 逐字稿使用繁體中文。若說話者使用台語（台灣閩南語），請轉寫為語意對應的繁體中文書面文字，必要時可在括號內附註台語原詞；英文或專有名詞保留原文。
5. 只輸出逐字稿本身，不要加任何前言、說明、標題或總結。
"""


# ── 工具函式 ──────────────────────────────────────────────
def get_api_key() -> str:
    """依序從環境變數、Streamlit secrets 取得 API Key。"""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        try:
            key = st.secrets["GEMINI_API_KEY"]
        except Exception:
            key = ""
    return key


def wait_until_active(client: genai.Client, file, timeout_sec: int = 300):
    """等待 Files API 上傳的檔案處理完成（狀態變為 ACTIVE）。"""
    start = time.time()
    while file.state and file.state.name == "PROCESSING":
        if time.time() - start > timeout_sec:
            raise TimeoutError("音訊檔處理逾時，請改用較小的檔案或稍後再試。")
        time.sleep(2)
        file = client.files.get(name=file.name)
    if file.state and file.state.name == "FAILED":
        raise RuntimeError("Gemini 無法處理這個音訊檔，請確認檔案未損毀。")
    return file


def transcribe(api_key: str, model: str, data: bytes, mime_type: str) -> str:
    """呼叫 Gemini API 進行轉錄與說話者識別，回傳逐字稿文字。"""
    client = genai.Client(api_key=api_key)

    size_mb = len(data) / (1024 * 1024)
    if size_mb <= INLINE_LIMIT_MB:
        audio_part = types.Part.from_bytes(data=data, mime_type=mime_type)
        contents = [TRANSCRIBE_PROMPT, audio_part]
    else:
        # 大檔案：先上傳到 Files API，再引用
        uploaded = client.files.upload(
            file=io.BytesIO(data),
            config={"mime_type": mime_type},
        )
        uploaded = wait_until_active(client, uploaded)
        contents = [TRANSCRIBE_PROMPT, uploaded]

    response = client.models.generate_content(model=model, contents=contents)
    return (response.text or "").strip()


# ── 頁面 ──────────────────────────────────────────────────
st.set_page_config(page_title="錄音轉逐字稿", page_icon="🎙️", layout="wide")

st.title("🎙️ 錄音檔轉逐字稿（含說話者識別）")
st.caption("上傳會議錄音，AI 自動轉錄並標記「[時間] 說話者: 內容」。支援 MP3 / WAV / M4A。")

with st.sidebar:
    st.header("⚙️ 設定")
    model_label = st.selectbox("選擇模型", list(MODELS.keys()), index=0)
    model = MODELS[model_label]

    api_key = get_api_key()
    if api_key:
        st.success("已從環境設定讀到 GEMINI_API_KEY ✅")
    else:
        api_key = st.text_input(
            "GEMINI_API_KEY",
            type="password",
            help="到 https://aistudio.google.com/apikey 免費申請。部署時建議改用環境變數或 secrets 設定。",
        )

    st.divider()
    st.markdown(
        "**使用說明**\n"
        "1. 上傳音訊檔（建議 < 200MB）\n"
        "2. 點「開始轉錄」\n"
        "3. 完成後可下載逐字稿 TXT"
    )

uploaded_file = st.file_uploader(
    "上傳音訊檔案",
    type=list(MIME_MAP.keys()),
    accept_multiple_files=False,
)

if uploaded_file is not None:
    size_mb = uploaded_file.size / (1024 * 1024)
    st.audio(uploaded_file)
    st.info(f"檔案：**{uploaded_file.name}**（{size_mb:.1f} MB）")

    if st.button("🚀 開始轉錄", type="primary", use_container_width=True):
        if not api_key:
            st.error("請先在左側欄輸入 GEMINI_API_KEY，或於部署環境設定該環境變數。")
            st.stop()

        ext = uploaded_file.name.rsplit(".", 1)[-1].lower()
        mime_type = MIME_MAP.get(ext, "audio/mp3")

        with st.spinner("AI 轉錄中……音訊越長耗時越久（10 分鐘錄音約需 1–3 分鐘），請勿關閉頁面。"):
            try:
                transcript = transcribe(api_key, model, uploaded_file.getvalue(), mime_type)
            except Exception as e:
                st.error(f"轉錄失敗：{e}")
                st.stop()

        if not transcript:
            st.warning("模型未回傳內容，請換一個模型或稍後再試。")
            st.stop()

        st.session_state["transcript"] = transcript
        st.session_state["transcript_name"] = uploaded_file.name

# 顯示結果（存在 session_state，避免下載按鈕觸發重跑後結果消失）
if "transcript" in st.session_state:
    st.divider()
    st.subheader(f"📝 逐字稿：{st.session_state['transcript_name']}")

    base_name = st.session_state["transcript_name"].rsplit(".", 1)[0]
    st.download_button(
        "⬇️ 下載逐字稿（.txt）",
        data=st.session_state["transcript"].encode("utf-8"),
        file_name=f"{base_name}_逐字稿.txt",
        mime="text/plain",
    )

    # 依說話者上色顯示
    palette = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b"]
    speaker_colors: dict[str, str] = {}
    for line in st.session_state["transcript"].splitlines():
        line = line.strip()
        if not line:
            continue
        speaker = ""
        if "]" in line and ":" in line:
            after_ts = line.split("]", 1)[1]
            if ":" in after_ts:
                speaker = after_ts.split(":", 1)[0].strip()
        if speaker and speaker not in speaker_colors:
            speaker_colors[speaker] = palette[len(speaker_colors) % len(palette)]
        color = speaker_colors.get(speaker, "#555555")
        st.markdown(
            f'<div style="margin:4px 0; line-height:1.7;">'
            f'<span style="color:{color}; font-weight:600;">{line}</span></div>'
            if speaker
            else f'<div style="margin:4px 0;">{line}</div>',
            unsafe_allow_html=True,
        )
