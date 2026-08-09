from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
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
                content={"error": "Impossibile estrarre testo dal PDF."}
            )

        short_text = extracted_text[:12000]

        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "Sei un auditor esperto di Compliance aziendale e GDPR. Analizza il documento ed evidenzia: 1) Punti critici o non conformità, 2) Rischi legali/privacy, 3) Raccomandazioni operative."
                },
                {
                    "role": "user",
                    "content": f"Ecco il testo:\n\n{short_text}"
                }
            ],
            temperature=0.3
        )

        return {"status": "success", "message": response.choices[0].message.content}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Errore durante l'analisi: {str(e)}"}
        )

@app.post("/create-checkout-session")
async def create_checkout_session(request: Request):
    try:
        # Legge il parametro plan sia se inviato in formato Form che JSON
        form_data = await request.form()
        plan = form_data.get("plan", "pro")

        secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        if not secret_key:
            return JSONResponse(
                status_code=400,
                content={"error": "STRIPE_SECRET_KEY non configurata."}
            )

        stripe.api_key = secret_key
        domain_url = os.getenv("RENDER_EXTERNAL_URL", "https://compliance-ai-qx5a.onrender.com")

        unit_amount = 4900 if plan == 'pro' else 19900

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'eur',
                    'product_data': {'name': f'Piano ComplianceAI: {plan.capitalize()}'},
                    'unit_amount': unit_amount,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{domain_url}/?success=true",
            cancel_url=f"{domain_url}/?canceled=true",
        )
        return {"url": checkout_session.url}
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": f"Errore Stripe: {str(e)}"}
        )
