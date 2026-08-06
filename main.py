from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import os
import openai
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
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "pending_verification":
        return JSONResponse(
            status_code=400,
            content={"error": "La chiave API OpenAI non è ancora configurata o verificata."}
        )
    return {"status": "success", "message": f"File '{file.filename}' ricevuto correttamente."}

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
