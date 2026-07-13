@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/2] 安裝相依套件……
python -m pip install -r requirements.txt -q
echo [2/2] 啟動 Streamlit（3 秒後自動開啟瀏覽器 http://localhost:8501）……
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start "" http://localhost:8501"
python -m streamlit run app.py --server.port 8501
