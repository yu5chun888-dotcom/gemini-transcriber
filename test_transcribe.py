# -*- coding: utf-8 -*-
"""端到端測試：讀取測試音訊，呼叫 app.transcribe() 驗證 Gemini 轉錄與說話者識別。

用法：python test_transcribe.py <音訊檔路徑>
API Key 來源：環境變數 GEMINI_API_KEY，或 .streamlit/secrets.toml
"""

import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")


def load_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        secrets_path = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")
        if os.path.exists(secrets_path):
            import tomllib
            with open(secrets_path, "rb") as f:
                key = tomllib.load(f).get("GEMINI_API_KEY", "")
    return key


def main():
    if len(sys.argv) < 2:
        print("用法：python test_transcribe.py <音訊檔路徑>")
        sys.exit(1)

    audio_path = sys.argv[1]
    key = load_api_key()
    if not key or "在這裡" in key:
        print("❌ 找不到 API Key：請設定環境變數 GEMINI_API_KEY，或建立 .streamlit/secrets.toml")
        sys.exit(1)

    from app import MIME_MAP, transcribe

    ext = audio_path.rsplit(".", 1)[-1].lower()
    mime = MIME_MAP.get(ext, "audio/wav")
    with open(audio_path, "rb") as f:
        data = f.read()

    print(f"▶ 測試檔案：{audio_path}（{len(data)/1024:.0f} KB, {mime}）")
    print("▶ 呼叫 gemini-3.5-flash 轉錄中……")
    transcript = transcribe(key, "gemini-3.5-flash", data, mime)

    print("\n===== 逐字稿輸出 =====")
    print(transcript)
    print("======================\n")

    # 驗證輸出格式：[時間] 說話者 X: 內容
    lines = [l for l in transcript.splitlines() if l.strip()]
    pattern = re.compile(r"^\[\d{1,2}:\d{2}(:\d{2})?\]\s*說話者\s*\S+\s*[:：]")
    ok = [l for l in lines if pattern.match(l.strip())]
    speakers = {m.group(0).split("]")[1].strip().rstrip(":：").strip() for m in (pattern.match(l.strip()) for l in lines) if m}

    print(f"✔ 總行數 {len(lines)}，符合「[時間] 說話者 X:」格式 {len(ok)} 行")
    print(f"✔ 識別出的說話者：{sorted(speakers)}")
    if len(ok) >= max(1, len(lines) // 2) and len(speakers) >= 2:
        print("✅ 測試通過：格式正確且識別出至少 2 位說話者")
    else:
        print("⚠️ 測試未完全通過：請檢查上方輸出")


if __name__ == "__main__":
    main()
