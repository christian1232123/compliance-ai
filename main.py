import io
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from PyPDF2 import PdfReader
from openai import OpenAI

app = FastAPI(title="AI Compliance Auditor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sostituirai questa stringa con la tua API Key reale di OpenAI
client = OpenAI(api_key="TUA_API_KEY_HERE")

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    pdf_file = io.BytesIO(pdf_bytes)
    reader = PdfReader(pdf_file)
    text = ""
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            text += extracted + "\n"
    return text

@app.post("/analyze-compliance/")
async def analyze_compliance(file: UploadFile = File(...), standard: str = "GDPR"):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Il file deve essere un PDF.")
    
    pdf_bytes = await file.read()
    extracted_text = extract_text_from_pdf(pdf_bytes)
    
    if len(extracted_text.strip()) == 0:
        raise HTTPException(status_code=400, detail="Impossibile estrarre testo dal PDF.")

    truncated_text = extracted_text[:12000]

    system_prompt = (
        f"Sei un auditor ed esperto di compliance legale e normativa ({standard}). "
        "Analizza il seguente testo ed evidenzia:\n"
        "1. Eventuali violazioni o punti di non conformità.\n"
        "2. Livello di rischio (Alto, Medio, Basso).\n"
        "3. Raccomandazioni pratiche per correggere i problemi scoperti.\n"
        "Fornisci una risposta formale, sintetica e strutturata in Markdown."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Ecco il documento da analizzare:\n\n{truncated_text}"}
            ],
            temperature=0.2
        )
        report = response.choices[0].message.content
        return {"filename": file.filename, "standard": standard, "report": report}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore durante l'analisi AI: {str(e)}")