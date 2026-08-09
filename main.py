import os
import json
import sqlite3
from io import BytesIO
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer
from pypdf import PdfReader
from google import genai
from google.genai import types
from passlib.context import CryptContext
from jose import JWTError, jwt

app = FastAPI()

DB_PATH = "audit_history.db"

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "super_secret_jwt_key_compliance_ai")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filename TEXT,
            standard TEXT,
            language TEXT,
            risk_score INTEGER,
            risk_level TEXT,
            summary TEXT,
            report_markdown TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Forziamo la versione API v1 per garantire la massima compatibilità
client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(api_version="v1")
) if GEMINI_API_KEY else None

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "sk_test_mock")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: Optional[str] = Depends(oauth2_scheme)):
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT id, email FROM users WHERE id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()

    return dict(user) if user else None

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/register")
async def register(email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if not email or not password:
        return JSONResponse(status_code=400, content={"error": "Compila tutti i campi."})

    hashed_pw = get_password_hash(password)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO users (email, hashed_password) VALUES (?, ?)', (email, hashed_pw))
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()

        access_token = create_access_token(data={"sub": user_id, "email": email})
        return JSONResponse(content={"token": access_token, "email": email})
    except sqlite3.IntegrityError:
        return JSONResponse(status_code=400, content={"error": "Indirizzo e-mail già registrato."})

@app.post("/login")
async def login(email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE email = ?', (email,))
    user = cursor.fetchone()
    conn.close()

    if not user or not verify_password(password, user["hashed_password"]):
        return JSONResponse(status_code=400, content={"error": "E-mail o password non corrette."})

    access_token = create_access_token(data={"sub": user["id"], "email": user["email"]})
    return JSONResponse(content={"token": access_token, "email": user["email"]})

@app.post("/analyze")
async def analyze_pdf(
    file: UploadFile = File(...),
    standard: str = Form("gdpr"),
    language: str = Form("it"),
    current_user: Optional[dict] = Depends(get_current_user)
):
    if not file.filename.lower().endswith('.pdf'):
        return JSONResponse(status_code=400, content={"error": "Il file deve essere un PDF."})

    try:
        pdf_bytes = await file.read()
        reader = PdfReader(BytesIO(pdf_bytes))
        extracted_text = ""
        for page in reader.pages:
            text = page.extract_text()
            if text:
                extracted_text += text + "\n"

        if not extracted_text.strip():
            return JSONResponse(status_code=400, content={"error": "Impossibile estrarre testo dal PDF. Il file potrebbe essere una scansione immagine."})

        extracted_text = extracted_text[:12000]

        if not client:
            return JSONResponse(status_code=500, content={"error": "Chiave GEMINI_API_KEY non configurata nelle variabili d'ambiente di Render."})

        lang_map = {"it": "Italiano", "en": "English", "es": "Español", "de": "Deutsch"}
        target_lang = lang_map.get(language, "Italiano")

        std_map = {
            "gdpr": "GDPR & Privacy",
            "iso27001": "ISO 27001",
            "sicurezza": "D.Lgs 81/08"
        }
        target_std = std_map.get(standard, "GDPR")

        prompt = f"""
        Sei un Auditor di Compliance esperto. Analizza questo testo secondo lo standard: {target_std}.
        Rispondi ESCLUSIVAMENTE in lingua: {target_lang}.

        Restituisci la risposta SOLO ed ESCLUSIVAMENTE come oggetto JSON valido con queste chiavi:
        - "risk_score": numero intero da 0 a 100
        - "risk_level": stringa ("Basso", "Medio", "Alto", "Critico")
        - "summary": breve sintesi (max 2 frasi)
        - "markdown_report": analisi dettagliata formattata in Markdown

        Testo:
        {extracted_text}
        """

        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )

        result_data = json.loads(response.text)
        user_id = current_user["id"] if current_user else None

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO audit_reports (user_id, filename, standard, language, risk_score, risk_level, summary, report_markdown)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_id,
            file.filename,
            target_std,
            target_lang,
            result_data.get("risk_score", 70),
            result_data.get("risk_level", "Medio"),
            result_data.get("summary", ""),
            result_data.get("markdown_report", "")
        ))
        report_id = cursor.lastrowid
        conn.commit()
        conn.close()

        result_data["report_id"] = report_id
        return JSONResponse(content=result_data)

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Errore server: {str(e)}"})

@app.get("/history")
async def get_history(current_user: Optional[dict] = Depends(get_current_user)):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if current_user:
            cursor.execute('SELECT id, filename, standard, language, risk_score, risk_level, summary, created_at FROM audit_reports WHERE user_id = ? ORDER BY created_at DESC LIMIT 20', (current_user["id"],))
        else:
            cursor.execute('SELECT id, filename, standard, language, risk_score, risk_level, summary, created_at FROM audit_reports WHERE user_id IS NULL ORDER BY created_at DESC LIMIT 20')
        rows = cursor.fetchall()
        conn.close()
        return JSONResponse(content=[dict(row) for row in rows])
    except Exception as e:
        return JSONResponse(content=[])

@app.get("/get-report/{report_id}")
async def get_report_by_id(report_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM audit_reports WHERE id = ?', (report_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return JSONResponse(status_code=404, content={"error": "Report non trovato"})
    return JSONResponse(content=dict(row))
