import os
import json
import sqlite3
import hashlib
from io import BytesIO
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pypdf import PdfReader
from groq import Groq

app = FastAPI()

DB_PATH = "audit_history.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            is_pro INTEGER DEFAULT 0
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("GEMINI_API_KEY")
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "sk_test_mock")

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode('utf-8')).hexdigest()

def get_email_from_token(token: Optional[str]) -> Optional[str]:
    if token and token.startswith("user_"):
        return token.replace("user_", "")
    return None

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.post("/register")
async def register(email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if not email or not password:
        return JSONResponse(status_code=400, content={"error": "Compila tutti i campi."})

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('INSERT INTO users (email, hashed_password) VALUES (?, ?)', (email, hash_pw(password)))
        conn.commit()
        conn.close()

        return JSONResponse(content={"token": f"user_{email}", "email": email, "is_pro": 0})
    except sqlite3.IntegrityError:
        return JSONResponse(status_code=400, content={"error": "E-mail già registrata."})

@app.post("/login")
async def login(email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE email = ? AND hashed_password = ?', (email, hash_pw(password)))
    user = cursor.fetchone()
    conn.close()

    if not user:
        return JSONResponse(status_code=400, content={"error": "Credenziali non valide."})

    return JSONResponse(content={"token": f"user_{email}", "email": user["email"], "is_pro": user["is_pro"]})

@app.get("/user-status")
async def user_status(authorization: Optional[str] = Header(None)):
    email = get_email_from_token(authorization)
    if not email:
        return JSONResponse(content={"is_pro": 0})

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT is_pro FROM users WHERE email = ?', (email,))
    user = cursor.fetchone()
    conn.close()

    is_pro = user["is_pro"] if user else 0
    return JSONResponse(content={"is_pro": is_pro})

@app.post("/confirm-payment")
async def confirm_payment(authorization: Optional[str] = Header(None)):
    email = get_email_from_token(authorization)
    if email:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET is_pro = 1 WHERE email = ?', (email,))
        conn.commit()
        conn.close()
        return JSONResponse(content={"status": "success", "is_pro": 1})
    return JSONResponse(status_code=400, content={"error": "Utente non identificato"})

@app.post("/analyze")
async def analyze_pdf(
    file: UploadFile = File(...),
    standard: str = Form("gdpr"),
    language: str = Form("it")
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
            return JSONResponse(status_code=400, content={"error": "Impossibile estrarre testo dal PDF."})

        extracted_text = extracted_text[:12000]

        if not client:
            return JSONResponse(status_code=500, content={"error": "Chiave GROQ_API_KEY non trovata su Render."})

        lang_map = {
            "it": "Italian",
            "en": "English",
            "es": "Spanish",
            "de": "German"
        }
        target_lang = lang_map.get(language, "Italian")

        std_map = {
            "gdpr": "GDPR & Privacy Policy",
            "iso27001": "ISO 27001 Standard",
            "sicurezza": "D.Lgs 81/08 Safety Regulation"
        }
        target_std = std_map.get(standard, "GDPR")

        prompt = f"""
        You are an expert Compliance Auditor. Analyze the provided document text against the following compliance standard: {target_std}.

        CRITICAL INSTRUCTION: You MUST write ALL values, summaries, headers, and detailed reports ENTIRELY in {target_lang}. Do NOT use any other language.

        Return ONLY a valid JSON object matching this structure:
        {{
            "risk_score": <integer between 0 and 100>,
            "risk_level": "<string: Low / Medium / High / Critical, translated into {target_lang}>",
            "summary": "<short 2-sentence summary fully written in {target_lang}>",
            "markdown_report": "<detailed markdown analysis including strengths, critical issues, and actionable recommendations fully written in {target_lang}>"
        }}

        Document text to analyze:
        {extracted_text}
        """

        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": f"You are a professional compliance auditor. You must respond strictly in JSON format and strictly in {target_lang}."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )

        response_content = completion.choices[0].message.content
        result_data = json.loads(response_content)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO audit_reports (filename, standard, language, risk_score, risk_level, summary, report_markdown)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
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
async def get_history():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT id, filename, standard, language, risk_score, risk_level, summary, created_at FROM audit_reports ORDER BY created_at DESC LIMIT 20')
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

@app.post("/create-checkout-session")
async def create_checkout_session(plan: str = Form(...)):
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    price = 4900 if plan == "pro" else 19900
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {'name': f'Piano ComplianceAI {plan.capitalize()}'},
                    'unit_amount': price,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url='https://compliance-ai-qx5a.onrender.com/?success=true',
            cancel_url='https://compliance-ai-qx5a.onrender.com/?canceled=true',
        )
        return {"url": session.url}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
