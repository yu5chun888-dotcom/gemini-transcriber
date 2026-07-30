# -*- coding: utf-8 -*-
"""錄音檔轉逐字稿與說話者識別 Web App（Gemini API + Streamlit）"""

import io
import os
import re
import shutil
import subprocess
import tempfile
import time

import streamlit as st
from google import genai
from google.genai import types

# ── 常數設定 ──────────────────────────────────────────────
MODELS = {
    "gemini-3.5-flash（穩定・推薦）": "gemini-3.5-flash",
    "gemini-3.6-flash（最新）": "gemini-3.6-flash",
}

# 遇到 429（配額用盡）時，依序改用這些模型重試。
# 背景：2026-07-27 實測 gemini-3.6-flash 的音訊配額會先於 3.5-flash 用盡
#（純文字仍可通、但任何長度的音訊都回 429），故需自動切換而非讓使用者卡住。
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.1-flash-lite"]

MIME_MAP = {
    "mp3": "audio/mp3",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
}

INLINE_LIMIT_MB = 15  # 超過此大小改走 Files API（Gemini 單次請求上限 20MB）

# ── 計費參數 ──────────────────────────────────────────────
# 單價（美元／百萬 token），來源：https://ai.google.dev/gemini-api/docs/pricing
# ⚠️ Google 調價或新增模型時，這張表要一併更新，否則畫面金額會失準。
# 最後查核日期：2026-07-25
PRICING_USD_PER_1M = {
    "gemini-3.6-flash": {"input": 1.50, "output": 7.50},
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00},
}

USD_TO_TWD = 32.0          # 概略匯率，供快速換算參考
AUDIO_TOKENS_PER_SEC = 25  # 實測值：5 秒音訊 = 125 tokens

# 輸出 token 上限。⚠️ 必須明確指定：API 預設僅 8,192，50 分鐘錄音會在約 21 分處被截斷。
# 65536 為 gemini-3.5/3.6-flash 的模型上限（以 client.models.get() 查得）。
MAX_OUTPUT_TOKENS = 65536

# 逐字稿約略耗用的輸出 token／每分鐘音訊（實測 21.8 分鐘 ≈ 8,165 tokens）
OUTPUT_TOKENS_PER_MIN = 375

TRANSCRIBE_PROMPT = """你是專業的逐字稿聽打員。請將這段音訊完整轉錄為逐字稿，並區分不同說話者（Speaker Diarization）。

輸出規則（務必嚴格遵守）：
1. 每個發言段落一行，格式為：[MM:SS] 說話者 A: 發言內容
2. 時間戳記為該段發言的開始時間，格式 [分:秒]，超過一小時用 [HH:MM:SS]。
3. 依出場順序將說話者命名為「說話者 A」「說話者 B」「說話者 C」……同一人全程使用同一代號。
4. 逐字稿使用繁體中文。若說話者使用台語（台灣閩南語），請轉寫為語意對應的繁體中文書面文字，必要時可在括號內附註台語原詞；英文或專有名詞保留原文。
5. 只輸出逐字稿本身，不要加任何前言、說明、標題或總結。
"""


# ── 靜音裁剪（省費用：Gemini 依音訊「秒數」計費，與檔案大小無關）──
def get_ffmpeg_path() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def probe_duration(ffmpeg: str, path: str) -> float | None:
    """從 ffmpeg 輸出解析音訊長度（秒）。"""
    result = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", result.stderr or "")
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def trim_silence(data: bytes, ext: str) -> tuple[bytes, str, float, float] | None:
    """移除超過 1 秒的靜音段並轉單聲道 mp3。

    回傳 (新資料, 新mime, 原長度秒, 新長度秒)；無 ffmpeg 或處理失敗時回傳 None。
    """
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, f"in.{ext}")
            dst = os.path.join(td, "out.mp3")
            with open(src, "wb") as f:
                f.write(data)
            orig = probe_duration(ffmpeg, src)
            result = subprocess.run(
                [ffmpeg, "-y", "-i", src,
                 "-af", "silenceremove=start_periods=1:start_duration=0:start_threshold=-38dB:"
                        "stop_periods=-1:stop_duration=1.0:stop_threshold=-38dB",
                 "-ac", "1", "-b:a", "64k", dst],
                capture_output=True, timeout=1800,
            )
            if result.returncode != 0 or not os.path.exists(dst):
                return None
            new = probe_duration(ffmpeg, dst)
            if orig is None or new is None or new <= 0:
                return None
            with open(dst, "rb") as f:
                out = f.read()
        return out, "audio/mp3", orig, new
    except Exception:
        return None


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


def extract_usage(response, model: str) -> dict:
    """從 API 回應取出 token 用量並換算費用（估算值，非帳單）。"""
    u = getattr(response, "usage_metadata", None)
    prompt = int(getattr(u, "prompt_token_count", 0) or 0)
    candidates = int(getattr(u, "candidates_token_count", 0) or 0)
    thoughts = int(getattr(u, "thoughts_token_count", 0) or 0)
    output = candidates + thoughts  # 思考 token 一樣以輸出計價

    audio = 0
    for detail in (getattr(u, "prompt_tokens_details", None) or []):
        modality = getattr(getattr(detail, "modality", None), "name", "")
        if modality == "AUDIO":
            audio += int(getattr(detail, "token_count", 0) or 0)

    price = PRICING_USD_PER_1M.get(model)
    usd = None
    if price:
        usd = prompt / 1e6 * price["input"] + output / 1e6 * price["output"]

    # 偵測是否因輸出額度用盡而被截斷（逐字稿會停在半句話）
    truncated = False
    try:
        reason = getattr(response.candidates[0], "finish_reason", None)
        truncated = getattr(reason, "name", str(reason)) == "MAX_TOKENS"
    except Exception:
        pass

    return {
        "input_tokens": prompt,
        "output_tokens": output,
        "thoughts_tokens": thoughts,
        "audio_tokens": audio,
        "usd": usd,
        "model": model,
        "truncated": truncated,
    }


def is_quota_error(exc: Exception) -> bool:
    """判斷是否為配額用盡（429）。"""
    return "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)


def transcribe(api_key: str, model: str, data: bytes, mime_type: str,
               on_fallback=None) -> tuple[str, dict]:
    """呼叫 Gemini API 進行轉錄與說話者識別。

    首選 model；若該模型配額用盡（429），自動改用 FALLBACK_MODELS 依序重試。
    on_fallback(failed_model, next_model) 會在每次切換前被呼叫，供 UI 提示。

    回傳 (逐字稿文字, 用量與費用估算)。
    """
    client = genai.Client(api_key=api_key)

    size_mb = len(data) / (1024 * 1024)
    if size_mb <= INLINE_LIMIT_MB:
        audio_part = types.Part.from_bytes(data=data, mime_type=mime_type)
        contents = [TRANSCRIBE_PROMPT, audio_part]
    else:
        # 大檔案：先上傳到 Files API，再引用（只需上傳一次，各模型共用）
        uploaded = client.files.upload(
            file=io.BytesIO(data),
            config={"mime_type": mime_type},
        )
        uploaded = wait_until_active(client, uploaded)
        contents = [TRANSCRIBE_PROMPT, uploaded]

    config = types.GenerateContentConfig(max_output_tokens=MAX_OUTPUT_TOKENS)

    candidates = [model] + [m for m in FALLBACK_MODELS if m != model]
    last_error: Exception | None = None
    for i, current in enumerate(candidates):
        try:
            response = client.models.generate_content(
                model=current, contents=contents, config=config)
            usage = extract_usage(response, current)
            usage["fell_back_from"] = model if current != model else None
            return (response.text or "").strip(), usage
        except Exception as exc:
            last_error = exc
            if not is_quota_error(exc) or i == len(candidates) - 1:
                raise
            if on_fallback:
                on_fallback(current, candidates[i + 1])

    raise last_error  # pragma: no cover（迴圈必定 return 或 raise）


# ── 頁面 ──────────────────────────────────────────────────
st.set_page_config(page_title="錄音轉逐字稿", page_icon="🎙️", layout="wide")

st.title("🎙️ 錄音檔轉逐字稿（含說話者識別）")
st.caption("上傳會議錄音，AI 自動轉錄並標記「[時間] 說話者: 內容」。支援 MP3 / WAV / M4A。")

with st.sidebar:
    st.header("⚙️ 設定")
    model_label = st.selectbox("選擇模型", list(MODELS.keys()), index=0)
    model = MODELS[model_label]

    trim_enabled = st.checkbox(
        "上傳前自動裁剪靜音（省費用）",
        value=True,
        help="Gemini 依音訊秒數計費，與檔案大小無關。自動剪掉超過 1 秒的靜音段，"
             "長錄音通常可省 2 成以上費用。注意：裁剪後時間戳記是裁剪版音訊的時間，"
             "與原始錄音會有偏移；需要精準對回原檔時請關閉此選項。",
    )

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
        audio_bytes = uploaded_file.getvalue()

        duration_sec = None

        if trim_enabled:
            with st.spinner("靜音裁剪中……"):
                trimmed = trim_silence(audio_bytes, ext)
            if trimmed:
                audio_bytes, mime_type, orig_sec, new_sec = trimmed
                duration_sec = new_sec
                saved_pct = max(0.0, (1 - new_sec / orig_sec) * 100) if orig_sec else 0.0
                msg = (f"✂️ 已裁剪靜音：{orig_sec / 60:.1f} 分 → {new_sec / 60:.1f} 分"
                       f"（省 {saved_pct:.0f}%）")
                price = PRICING_USD_PER_1M.get(model)
                if price:
                    saved_usd = (max(0.0, orig_sec - new_sec) * AUDIO_TOKENS_PER_SEC
                                 / 1e6 * price["input"])
                    msg += f"，約省 NT${saved_usd * USD_TO_TWD:.2f}"
                st.info(msg)
            else:
                st.caption("（未進行靜音裁剪：ffmpeg 不可用或處理失敗，改用原始音訊）")

        # 事前預警：逐字稿長度可能撞到單次輸出上限
        if duration_sec and duration_sec / 60 * OUTPUT_TOKENS_PER_MIN > MAX_OUTPUT_TOKENS * 0.8:
            st.warning(
                f"⚠️ 這段錄音長達 {duration_sec / 60:.0f} 分鐘，逐字稿可能超過模型單次輸出上限"
                "而被截斷。建議先切成兩段再分別轉錄。"
            )

        fallback_note = st.empty()

        def notify_fallback(failed: str, nxt: str):
            fallback_note.warning(
                f"⚠️ `{failed}` 今日配額已用盡，自動改用 `{nxt}` 繼續……"
            )

        with st.spinner("AI 轉錄中……音訊越長耗時越久（10 分鐘錄音約需 1–3 分鐘），請勿關閉頁面。"):
            try:
                transcript, usage = transcribe(api_key, model, audio_bytes, mime_type,
                                               on_fallback=notify_fallback)
            except Exception as e:
                if is_quota_error(e):
                    st.error(
                        "❌ **所有可用模型的配額都已用盡**（429）。\n\n"
                        "音訊轉錄配額通常隔日重置，請明天再試；"
                        "若急需，可到 [Google AI Studio](https://aistudio.google.com/) "
                        "查看帳號配額或提升方案等級。"
                    )
                else:
                    st.error(f"轉錄失敗：{e}")
                st.stop()

        if not transcript:
            st.warning("模型未回傳內容，請換一個模型或稍後再試。")
            st.stop()

        if usage.get("truncated"):
            st.error(
                "⚠️ **逐字稿不完整**：內容長度已達模型單次輸出上限，後半段未產出"
                "（最後一行會停在半句話）。請將錄音切成兩段後分別轉錄，再自行合併。"
            )

        st.session_state["transcript"] = transcript
        st.session_state["transcript_name"] = uploaded_file.name
        st.session_state["usage"] = usage
        if usage.get("usd") is not None:
            st.session_state["session_usd"] = (
                st.session_state.get("session_usd", 0.0) + usage["usd"]
            )
            st.session_state["session_runs"] = st.session_state.get("session_runs", 0) + 1

# 顯示結果（存在 session_state，避免下載按鈕觸發重跑後結果消失）
if "transcript" in st.session_state:
    st.divider()
    st.subheader(f"📝 逐字稿：{st.session_state['transcript_name']}")

    usage = st.session_state.get("usage")
    if usage:
        if usage.get("truncated"):
            st.error("⚠️ 這份逐字稿因達到輸出上限而不完整，請將錄音切段後重新轉錄。")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("音訊長度", f"{usage['audio_tokens'] / AUDIO_TOKENS_PER_SEC / 60:.1f} 分")
        c2.metric("輸入 tokens", f"{usage['input_tokens']:,}")
        c3.metric("輸出 tokens", f"{usage['output_tokens']:,}",
                  help=f"含思考 token {usage['thoughts_tokens']:,}（一樣以輸出計價）")
        if usage.get("fell_back_from"):
            st.info(
                f"ℹ️ 原選模型 `{usage['fell_back_from']}` 配額用盡，"
                f"本次實際由 `{usage['model']}` 完成轉錄。"
            )
        if usage.get("usd") is not None:
            c4.metric("本次費用", f"NT${usage['usd'] * USD_TO_TWD:.2f}",
                      help=f"US${usage['usd']:.4f}｜模型 {usage['model']}")
        else:
            c4.metric("本次費用", "—", help=f"{usage['model']} 尚未列入程式內的價目表")

        note = (f"💡 費用為估算值（依官方單價換算，匯率以 1 美元 = {USD_TO_TWD:.0f} 元計），"
                "與 Google 實際帳單可能有小幅落差；正式對帳請看 Google Cloud Console。")
        if st.session_state.get("session_runs", 0) > 1:
            note += (f" 本次開啟頁面已轉錄 {st.session_state['session_runs']} 次，"
                     f"累計約 NT${st.session_state['session_usd'] * USD_TO_TWD:.2f}"
                     "（重新整理頁面即歸零）。")
        st.caption(note)

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
