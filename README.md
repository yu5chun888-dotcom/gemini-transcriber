# 🎙️ 錄音檔轉逐字稿 Web App（含說話者識別）

上傳會議錄音（MP3 / WAV / M4A），透過 **Google Gemini API** 的原生語音理解能力，
自動產出「`[時間戳記] 說話者 A: 內容`」格式的逐字稿，並可下載 TXT。

## 功能特色

- 🎵 支援 MP3、WAV、M4A，上傳上限 200MB（>15MB 自動改走 Gemini Files API）
- 🗣️ 說話者識別（Speaker Diarization）：依出場順序標記說話者 A / B / C…，並以顏色區分
- ⏱️ 每段發言附開始時間戳記 `[MM:SS]`
- 🤖 可切換模型：`gemini-3.5-flash`（穩定・預設）/ `gemini-3.6-flash`（最新），兩者皆經實測，說話者識別與中英台混講品質良好
- 🔁 失敗自動處理：503（伺服器忙碌）自動等待重試，429（配額用盡）自動改用備援模型，都不必手動重按
- ✂️ 自動靜音裁剪（預設開啟）：Gemini 依音訊秒數計費，剪掉超過 1 秒的靜音段可省 2 成以上費用；需精準對回原始錄音時間時可於側欄關閉
- 💰 用量與費用顯示：每次轉錄後顯示輸入／輸出 token 數與估算費用（NT$），並累計本次開啟頁面的總花費
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
- **音訊長度上限？** 真正的限制在「輸出」而非輸入：逐字稿長度受單次輸出上限 65,536 tokens 約束，換算**約 175 分鐘（近 3 小時）音訊**。超過時畫面會出現截斷警示，請將錄音切段後分別轉錄。
  > ⚠️ 2026-07-27 修正：先前未指定 `max_output_tokens`，API 套用預設值 8,192，導致超過約 22 分鐘的錄音會被無聲截斷。現已明確指定為模型上限，並加上截斷偵測與事前預警。
- **費用？** Gemini 依音訊「秒數」計費（與檔案大小、音質無關，實測約每秒 25 tokens），**一小時錄音約 NT$8–10**（含輸出與思考 token）。省錢方式：保持靜音裁剪開啟、避免重複轉同一個檔。建議在 Google Cloud Console 設預算警示。詳見 [官方定價](https://ai.google.dev/gemini-api/docs/pricing)。
- **畫面顯示的費用準嗎？** 是依官方單價即時換算的**估算值**（匯率固定為 1 美元 = 32 元），與 Google 實際帳單通常有小幅落差（免費額度、計價捨入等因素）。正式對帳請看 Google Cloud Console。
- **Google 調價了怎麼辦？** 修改 `app.py` 開頭的 `PRICING_USD_PER_1M` 與 `USD_TO_TWD` 即可，該處有註記價格來源與最後查核日期。
- **模型清單會變動嗎？** 會。若遇到「model no longer available」錯誤，執行 `python -c "from google import genai; [print(m.name) for m in genai.Client(api_key='你的Key').models.list()]"` 查詢可用模型，並更新 `app.py` 開頭的 `MODELS` 字典。
- **出現 503 UNAVAILABLE？** Google 端暫時壅塞，與您的帳號無關。程式會自動等待重試（5 秒、20 秒）並輪替備援模型；若仍失敗，等幾分鐘再按一次「開始轉錄」即可。
- **出現 429 RESOURCE_EXHAUSTED？** 表示該模型的**音訊配額**用盡（實測特徵：純文字請求仍可通、但任何長度的音訊都失敗，與檔案大小無關）。程式會自動改用備援模型；若所有模型都用盡，配額通常隔日重置，或到 [Google AI Studio](https://aistudio.google.com/) 查看方案等級。
