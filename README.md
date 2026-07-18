# 🎙️ 錄音檔轉逐字稿 Web App（含說話者識別）

上傳會議錄音（MP3 / WAV / M4A），透過 **Google Gemini API** 的原生語音理解能力，
自動產出「`[時間戳記] 說話者 A: 內容`」格式的逐字稿，並可下載 TXT。

## 功能特色

- 🎵 支援 MP3、WAV、M4A，上傳上限 200MB（>15MB 自動改走 Gemini Files API）
- 🗣️ 說話者識別（Speaker Diarization）：依出場順序標記說話者 A / B / C…，並以顏色區分
- ⏱️ 每段發言附開始時間戳記 `[MM:SS]`
- 🔄 可切換模型：`gemini-3.5-flash`（推薦）/ `gemini-flash-lite-latest`（更省・品質略降）
- ✂️ 自動靜音裁剪（預設開啟）：Gemini 依音訊秒數計費，剪掉超過 1 秒的靜音段可省 2 成以上費用；需精準對回原始錄音時間時可於側欄關閉
- 🗣️ 支援國語、台語、英語混講（台語自動轉寫為繁體中文書面文字）
- ⬇️ 一鍵下載逐字稿 `.txt`

## 事前準備：申請 Gemini API Key（免費）

1. 前往 <https://aistudio.google.com/apikey>
2. 用 Google 帳號登入 → 點「Create API Key」
3. 複製產生的金鑰（格式類似 `AIzaSy...`），下面會用到

## 本地執行（Windows）

**方法一：雙擊 `run.bat`**（會自動安裝套件並啟動）

**方法二：手動指令**

```bat
cd gemini-transcriber
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

啟動後瀏覽器開啟 <http://localhost:8501>。

**API Key 設定方式（三選一）：**

| 方式 | 做法 | 適合情境 |
|---|---|---|
| 網頁輸入 | 直接在左側欄貼上 Key | 快速試用 |
| secrets 檔 | 複製 `.streamlit/secrets.toml.example` 為 `secrets.toml` 並填入 Key | 本地常用 |
| 環境變數 | `set GEMINI_API_KEY=你的Key` 後再啟動 | 腳本/伺服器 |

## ☁️ 一鍵部署到 Streamlit Cloud（免費，分享給朋友）

1. 把 `gemini-transcriber` 資料夾推上 GitHub（**確認 `.streamlit/secrets.toml` 沒有被上傳**——本專案的 `.gitignore` 已自動排除）：
   ```bash
   git init
   git add .
   git commit -m "init"
   git remote add origin https://github.com/<你的帳號>/<repo名稱>.git
   git push -u origin main
   ```
2. 前往 <https://share.streamlit.io> → 用 GitHub 帳號登入 → **New app**
3. 選擇剛才的 repo，Main file path 填 `app.py`
4. 進入 App 的 **Settings → Secrets**，貼上：
   ```toml
   GEMINI_API_KEY = "你的APIKey"
   ```
5. 按 Deploy。完成後會得到一個 `https://xxxx.streamlit.app` 網址，直接把網址傳給朋友即可使用 🎉

> 💡 部署後設定了 Secrets，使用者打開網頁就**不需要**再輸入 API Key（費用會算在你的 Key 上，請留意用量）。
> 若想讓每個使用者用自己的 Key，就**不要**設定 Secrets，網頁會顯示 Key 輸入欄。

## 專案結構

```
gemini-transcriber/
├── app.py                          # 主程式（前端 UI + Gemini API 串接）
├── requirements.txt                # 相依套件（部署時 Streamlit Cloud 會自動安裝）
├── run.bat                         # Windows 一鍵啟動腳本
├── README.md
├── .gitignore                      # 排除 secrets.toml 等機密檔
└── .streamlit/
    ├── config.toml                 # 上傳上限 200MB 等伺服器設定
    └── secrets.toml.example        # API Key 設定範例（複製為 secrets.toml 使用）
```

## 常見問題

- **轉錄要多久？** 10 分鐘錄音約 1–3 分鐘（flash 模型）；音訊越長越久。
- **時間戳記準嗎？** Gemini 對音訊時間的估計大致準確，但長音訊可能有數秒偏移，重要場合請抽查核對。
- **說話者會認錯嗎？** 聲音相近或重疊發言時可能混淆，建議轉完後人工快速校對說話者代號。
- **音訊長度上限？** Gemini 單一請求最長支援約 9.5 小時音訊；實務上建議 2 小時內、並注意 API 免費額度。
- **費用？** Gemini 依音訊「秒數」計費（與檔案大小、音質無關），flash 級約 NT$4–6／小時音訊。省錢三招：開啟靜音裁剪（預設開）、長檔改選 flash-lite、避免不必要的重轉。建議在 Google Cloud Console 設預算警示。詳見 [官方定價](https://ai.google.dev/pricing)。
- **模型清單會變動嗎？** 會。若遇到「model no longer available」錯誤，執行 `python -c "from google import genai; [print(m.name) for m in genai.Client(api_key='你的Key').models.list()]"` 查詢可用模型，並更新 `app.py` 開頭的 `MODELS` 字典。
