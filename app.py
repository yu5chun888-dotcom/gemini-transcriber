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

# 503（伺服器暫時過載）時，同一模型的重試間隔（秒）。用盡後改試下一個模型。
# 429（配額用盡）不重試——等待無用，直接換模型。
RETRY_DELAYS_SEC = [5, 20]

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

# 思考 token 與逐字稿正文共用上面的輸出額度。實測 48 分鐘錄音在不設限時，
# 思考會吃掉約 62,000 token，導致正文只剩最後 4 分鐘且退化成無意義循環。
# 設上限可把額度留給正文；實測 10 分鐘片段本來就只用約 4,300，故不影響短音訊。
THINKING_BUDGET = 6144

# 逐字稿約略耗用的輸出 token／每分鐘音訊（實測 21.8 分鐘 ≈ 8,165 tokens）
OUTPUT_TOKENS_PER_MIN = 375

# 分段轉錄：實測同一提示詞在 10 分鐘片段可分出 4–5 軌、時間戳正常；
# 但整份 53 分鐘只剩 2 軌、末段 5,605 字、時間戳停在 29:01（長音訊退化）。
# 故超過門檻即切段分別轉錄再合併。
CHUNK_MINUTES = 10
CHUNK_THRESHOLD_MIN = 15   # 短於此長度不切，避免無謂的多次呼叫

# 提示詞規格來源：專案 07「08_逐字稿品質規格與驗收.md」§三
# 每一條都對應該文件的驗收項目，修改前請先確認規格是否同步調整。
PROMPT_TEMPLATE = """你是專業的會議逐字稿聽打員。請將這段錄音完整轉為逐字稿，嚴格遵守以下規格。

【格式】
1. 每段開頭標時間戳 [MM:SS]，超過一小時用 [HH:MM:SS]
2. 依聲紋分軌標示「說話者 A」「說話者 B」「說話者 C」……{speaker_req}
3. 同一個人連續發言為一段；**不同人發言必須換段換標籤**
4. 單段不超過 400 字；超過時依語意或議題斷段，斷段後重新標時間戳與說話者
5. 議題轉換處另起新段，不要把不同議題併在同一段
6. 每段就是**一行純文字**，該行開頭必須是方括號時間戳，範例：
   [12:34] 說話者 A: 發言內容
7. **嚴禁任何格式標記**：不得使用 Markdown 項目符號（* 或 -）、粗體（**）、
   HTML 標籤（如 <b>），也不得縮排。不要加摘要、標題或結論

【台語處理】
本會議台語比例高。**保留台語原詞，並在其後緊接括號國語對照**，例如：
  厝（房子）、條仔（欄杆）、拜六（星期六）、按呢（這樣）、伊（他）、
  頂高（上方）、甩掉（拆除）、無場（沒有位置）、齁（語助詞）
所有台語詞一律加註，不得省略，也不得只寫國語而丟掉台語原音。
英文與專有名詞保留原文。

【禁止事項】
1. 聽不清楚的地方標 [聽不清]，**不准憑空補字**
2. 數字原音保留，不四捨五入、不換算單位（「四十五萬四」不可寫成「45 萬」）
3. 不准把台語意譯後丟掉原音
4. **不准把不同人的發言合併到同一個說話者標籤**
5. 錄音後半段必須維持與前段相同的分段密度與分軌標準，不得因音訊變長而整段傾倒
{term_block}"""

DEFAULT_SPEAKER_REQ = "，至少分出 4 軌"

# 製造業常見易錯詞的填寫範例（僅示範格式，實際詞表由使用者於側欄填入或存於 secrets）
TERM_PLACEHOLDER = "易錯音→正確寫法，例如：\n浩森/豪昇→浩昇\n弘揚→鴻揚\n道具室→刀具室"


def build_prompt(speaker_count: int = 0, term_table: str = "",
                 context_tail: str = "") -> str:
    """依使用者設定組出轉錄提示詞。

    context_tail：分段轉錄時傳入前一段的結尾，讓說話者代號盡量延續。
    """
    if speaker_count >= 2:
        speaker_req = f"。本場會議約有 {speaker_count} 位發言者，請至少分出 {speaker_count} 軌"
    else:
        speaker_req = DEFAULT_SPEAKER_REQ

    term_block = ""
    if term_table.strip():
        term_block = ("\n\n【專有名詞校正】\n依以下對照表校正（左為易錯音，右為正確寫法）：\n"
                      + term_table.strip())

    if context_tail.strip():
        term_block += ("\n\n【承接前段】\n這是同一場會議的後續片段。前一段結尾如下，"
                       "請延續相同的說話者代號，並從 [00:00] 重新計時：\n"
                       + context_tail.strip())

    return PROMPT_TEMPLATE.format(speaker_req=speaker_req, term_block=term_block)


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


# ── 分段轉錄（長音訊退化的對策）──────────────────────────
def split_audio(data: bytes, ext: str, chunk_sec: int) -> list[tuple[bytes, float]] | None:
    """把音訊切成固定長度的段落。回傳 [(段落資料, 起始秒數), ...]。"""
    ffmpeg = get_ffmpeg_path()
    if not ffmpeg:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, f"in.{ext}")
            with open(src, "wb") as f:
                f.write(data)
            total = probe_duration(ffmpeg, src)
            if not total:
                return None

            chunks = []
            start = 0.0
            while start < total:
                dst = os.path.join(td, f"chunk_{int(start)}.mp3")
                result = subprocess.run(
                    [ffmpeg, "-y", "-ss", str(start), "-t", str(chunk_sec), "-i", src,
                     "-ac", "1", "-b:a", "64k", dst],
                    capture_output=True, timeout=1800,
                )
                if result.returncode != 0 or not os.path.exists(dst):
                    return None
                with open(dst, "rb") as f:
                    chunks.append((f.read(), start))
                start += chunk_sec
        return chunks
    except Exception:
        return None


def shift_timestamps(text: str, offset_sec: float) -> str:
    """把逐字稿內的時間戳整體平移，讓分段結果能接回原始音訊時間軸。"""
    def repl(m):
        parts = [int(p) for p in m.group(1).split(":")]
        sec = parts[0] * 60 + parts[1] if len(parts) == 2 else \
            parts[0] * 3600 + parts[1] * 60 + parts[2]
        sec += int(offset_sec)
        h, rem = divmod(sec, 3600)
        mnt, s = divmod(rem, 60)
        return f"[{h}:{mnt:02d}:{s:02d}]" if h else f"[{mnt:02d}:{s:02d}]"

    return re.sub(r"\[(\d{1,3}:\d{2}(?::\d{2})?)\]", repl, text)


# ── 工具函式 ──────────────────────────────────────────────
def get_secret(name: str, default: str = "") -> str:
    """依序從環境變數、Streamlit secrets 取值；都沒有則回傳預設值。"""
    value = os.environ.get(name, "")
    if not value:
        try:
            value = st.secrets[name]
        except Exception:
            value = ""
    return value or default


def get_api_key() -> str:
    """取得 Gemini API Key。"""
    return get_secret("GEMINI_API_KEY")


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
    """配額用盡（429）：等待無效，須改用其他模型。"""
    return "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)


def is_overload_error(exc: Exception) -> bool:
    """伺服器暫時過載或異常（503／500／504）：稍候重試通常就會好。"""
    s = str(exc)
    return any(k in s for k in ("503", "UNAVAILABLE", "500 INTERNAL", "504", "DEADLINE_EXCEEDED"))


def transcribe(api_key: str, model: str, data: bytes, mime_type: str,
               on_fallback=None, on_retry=None, prompt: str = "") -> tuple[str, dict]:
    """呼叫 Gemini API 進行轉錄與說話者識別。

    首選 model。遇 503（暫時過載）先就地重試；遇 429（配額用盡）或重試無效時，
    自動改用 FALLBACK_MODELS 依序嘗試。

    on_fallback(failed_model, next_model)：切換模型前呼叫，供 UI 提示。
    on_retry(model, wait_sec)：等待重試前呼叫，供 UI 提示。

    回傳 (逐字稿文字, 用量與費用估算)。
    """
    client = genai.Client(api_key=api_key)
    prompt = prompt or build_prompt()

    size_mb = len(data) / (1024 * 1024)
    if size_mb <= INLINE_LIMIT_MB:
        audio_part = types.Part.from_bytes(data=data, mime_type=mime_type)
        contents = [prompt, audio_part]
    else:
        # 大檔案：先上傳到 Files API，再引用（只需上傳一次，各模型共用）
        uploaded = client.files.upload(
            file=io.BytesIO(data),
            config={"mime_type": mime_type},
        )
        uploaded = wait_until_active(client, uploaded)
        contents = [prompt, uploaded]

    config = types.GenerateContentConfig(
        max_output_tokens=MAX_OUTPUT_TOKENS,
        thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
    )

    candidates = [model] + [m for m in FALLBACK_MODELS if m != model]
    last_error: Exception | None = None

    for i, current in enumerate(candidates):
        # 同一模型內：503 過載時就地重試
        for attempt in range(len(RETRY_DELAYS_SEC) + 1):
            try:
                response = client.models.generate_content(
                    model=current, contents=contents, config=config)
                usage = extract_usage(response, current)
                usage["fell_back_from"] = model if current != model else None
                return (response.text or "").strip(), usage
            except Exception as exc:
                last_error = exc
                if is_overload_error(exc) and attempt < len(RETRY_DELAYS_SEC):
                    wait = RETRY_DELAYS_SEC[attempt]
                    if on_retry:
                        on_retry(current, wait)
                    time.sleep(wait)
                    continue
                break  # 非暫時性錯誤，或重試已用盡 → 跳出改試下一個模型

        # 這個模型不行了：只有配額／過載問題才值得換模型
        if not (is_quota_error(last_error) or is_overload_error(last_error)):
            raise last_error
        if i == len(candidates) - 1:
            raise last_error
        if on_fallback:
            on_fallback(current, candidates[i + 1])

    raise last_error  # pragma: no cover（迴圈必定 return 或 raise）


def transcribe_chunked(api_key: str, model: str, data: bytes, mime_type: str,
                       speaker_count: int, term_table: str, duration_sec: float,
                       on_progress=None, **kw) -> tuple[str, dict]:
    """長音訊分段轉錄後合併。短音訊則直接單次轉錄。"""
    chunk_sec = CHUNK_MINUTES * 60
    chunks = None
    if duration_sec and duration_sec > CHUNK_THRESHOLD_MIN * 60:
        ext = {"audio/mp3": "mp3", "audio/wav": "wav", "audio/mp4": "m4a"}.get(mime_type, "mp3")
        chunks = split_audio(data, ext, chunk_sec)

    if not chunks:
        prompt = build_prompt(speaker_count, term_table)
        return transcribe(api_key, model, data, mime_type, prompt=prompt, **kw)

    parts, tail = [], ""
    total = {"input_tokens": 0, "output_tokens": 0, "thoughts_tokens": 0,
             "audio_tokens": 0, "usd": 0.0, "model": model,
             "truncated": False, "fell_back_from": None, "chunks": len(chunks)}

    for i, (chunk_data, offset) in enumerate(chunks):
        if on_progress:
            on_progress(i + 1, len(chunks))
        prompt = build_prompt(speaker_count, term_table, context_tail=tail)
        text, usage = transcribe(api_key, model, chunk_data, "audio/mp3",
                                 prompt=prompt, **kw)
        parts.append(shift_timestamps(text, offset))

        # 取本段結尾數行，作為下一段的說話者延續線索
        lines = [l for l in text.splitlines() if l.strip()]
        tail = "\n".join(lines[-3:])

        for k in ("input_tokens", "output_tokens", "thoughts_tokens", "audio_tokens"):
            total[k] += usage.get(k, 0)
        if usage.get("usd"):
            total["usd"] += usage["usd"]
        total["truncated"] = total["truncated"] or usage.get("truncated", False)
        total["model"] = usage.get("model", model)
        if usage.get("fell_back_from"):
            total["fell_back_from"] = usage["fell_back_from"]

    return "\n".join(parts), total


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

    speaker_count = st.number_input(
        "與會人數（0 = 自動）",
        min_value=0, max_value=12, value=0, step=1,
        help="填入實際發言人數可大幅改善說話者分軌。留 0 則要求模型至少分出 4 軌。",
    )

    term_table = st.text_area(
        "專有名詞校正表（選填）",
        value=get_secret("TERM_TABLE"),
        height=120,
        placeholder=TERM_PLACEHOLDER,
        help="每行一組「易錯音→正確寫法」。可消除廠商名、部門名、製程術語的誤植。"
             "常用詞表可存到 Streamlit Secrets 的 TERM_TABLE，即會自動帶入。",
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

        if duration_sec is None:
            # 未裁剪或裁剪失敗時仍需知道長度，才能判斷是否要分段轉錄
            ffmpeg = get_ffmpeg_path()
            if ffmpeg:
                with tempfile.TemporaryDirectory() as td:
                    probe_path = os.path.join(td, f"probe.{ext}")
                    with open(probe_path, "wb") as f:
                        f.write(audio_bytes)
                    duration_sec = probe_duration(ffmpeg, probe_path)

        # 事前預警：逐字稿長度可能撞到單次輸出上限
        if duration_sec and duration_sec / 60 * OUTPUT_TOKENS_PER_MIN > MAX_OUTPUT_TOKENS * 0.8:
            st.warning(
                f"⚠️ 這段錄音長達 {duration_sec / 60:.0f} 分鐘，逐字稿可能超過模型單次輸出上限"
                "而被截斷。建議先切成兩段再分別轉錄。"
            )

        status_note = st.empty()

        def notify_fallback(failed: str, nxt: str):
            status_note.warning(f"⚠️ `{failed}` 目前不可用，自動改用 `{nxt}` 繼續……")

        def notify_retry(current: str, wait: int):
            status_note.info(f"⏳ `{current}` 伺服器忙碌中，{wait} 秒後自動重試……")

        def notify_progress(i: int, n: int):
            status_note.info(f"🎧 分段轉錄中……第 {i}/{n} 段（長錄音切段可避免說話者分軌退化）")

        with st.spinner("AI 轉錄中……音訊越長耗時越久（10 分鐘錄音約需 1–3 分鐘），請勿關閉頁面。"):
            try:
                transcript, usage = transcribe_chunked(
                    api_key, model, audio_bytes, mime_type,
                    speaker_count=int(speaker_count), term_table=term_table,
                    duration_sec=duration_sec or 0,
                    on_progress=notify_progress,
                    on_fallback=notify_fallback, on_retry=notify_retry)
            except Exception as e:
                if is_overload_error(e):
                    st.error(
                        "❌ **各模型伺服器都在忙**（503）。已自動重試並輪替所有備援模型仍未成功。\n\n"
                        "這是 Google 端的暫時性壅塞，通常幾分鐘內恢復——請稍候再按一次「開始轉錄」。"
                    )
                elif is_quota_error(e):
                    st.error(
                        "❌ **所有可用模型的配額都已用盡**（429）。\n\n"
                        "音訊轉錄配額通常隔日重置，請明天再試；"
                        "若急需，可到 [Google AI Studio](https://aistudio.google.com/) "
                        "查看帳號配額或提升方案等級。"
                    )
                else:
                    st.error(f"轉錄失敗：{e}")
                st.stop()
        status_note.empty()

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
