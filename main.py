from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
import os
import io
from pypdf import PdfReader
from groq import Groq
import stripe

app = FastAPI(title="ComplianceAI SaaS")

# Inizializzazione Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>ComplianceAI is Running!</h1>"

@app.post("/analyze")
async def analyze_compliance(file: UploadFile = File(...)):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return JSONResponse(
            status_code=400,
            content={"error": "La chiave API GROQ_API_KEY non è configurata su Render."}
        )

    try:
        # 1. Estrazione del testo dal file PDF
        pdf_bytes = await file.read()
        pdf_reader = PdfReader(io.BytesIO(pdf_bytes))
        extracted_text = ""
        for page in pdf_reader.pages:
            text = page.extract_text()
            if text:
                extracted_text += text + "\n"

        if not extracted_text.strip():
            return JSONResponse(
                status_code=400,
                content={"error": "Impossibile estrarre testo dal PDF. Assicurati che non sia una scansione di sole immagini."}
            )

        # Tranciamo il testo per rimanere nei limiti
        short_text = extracted_text[:12000]

        # 2. Chiamata all'IA tramite Groq (Llama 3.3)
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "Sei un auditor esperto di Compliance aziendale e GDPR. Analizza il documento fornito ed evidenzia in modo sintetico: 1) Punti critici o non conformità, 2) Rischi legali/privacy, 3) Raccomandazioni operative."
                },
                {
                    "role": "user",
                    "content": f"Ecco il testo del documento da analizzare:\n\n{short_text}"
                }
            ],
            temperature=0.3
        )

        analysis_result = response.choices[0].message.content
        return {"status": "success", "message": analysis_result}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Errore durante l'analisi: {str(e)}"}
        )

@app.post("/create-checkout-session")
async def create_checkout_session(plan: str = Form(...)):
    try:
        domain_url = os.getenv("RENDER_EXTERNAL_URL", "http://localhost:8000")
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {'name': f'Piano ComplianceAI: {plan}'},
                    'unit_amount': 2900 if plan == 'pro' else 9900,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=domain_url + '/?success=true',
            cancel_url=domain_url + '/?canceled=true',
        )
        return {"url": checkout_session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
