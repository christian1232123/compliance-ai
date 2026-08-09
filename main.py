import os
import json
import sqlite3
from io import BytesIO
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pypdf import PdfReader
from google import genai
from google.genai import types

app = FastAPI()

DB_PATH = "audit_history.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "sk_test_mock")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

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
            return JSONResponse(status_code=400, content={"error": "Impossibile estrarre testo dal PDF. Potrebbe essere una scansione immagine."})

        extracted_text = extracted_text[:12000]

        if not client:
            return JSONResponse(status_code=500, content={"error": "Chiave GEMINI_API_KEY non trovata su Render."})

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
        - "markdown_report": analisi dettagliata con punti di forza, criticità e raccomandazioni formattata in Markdown

        Testo da analizzare:
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
