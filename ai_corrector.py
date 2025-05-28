# ai_corrector.py
import os
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def call_ollama(prompt):
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": "mistral", "prompt": prompt, "stream": False},
            timeout=60
        )
        return response.json().get("response", "").strip()
    except Exception as e:
        print(f"[ERROR] Ollama failed: {e}")
        raise RuntimeError("Ollama failed.")

def call_openai(prompt, engine):
    try:
        response = openai_client.chat.completions.create(
            model=engine,
            messages=[
                {"role": "system", "content": "You correct OCR errors."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] OpenAI failed: {e}")
        raise RuntimeError("OpenAI failed.")

def correct_text_chunk(text, engine="ollama"):
    prompt = (
        "Fix spelling errors, OCR mistakes, and formatting problems in the following text. "
        "Preserve all structural elements such as paragraphs, italics, or titles.\n\n"
        f"{text}"
    )
    return call_ollama(prompt) if engine == "ollama" else call_openai(prompt, engine)

def correct_chunks(chunks, engine="ollama"):
    corrected = []
    for cid, text in chunks:
        try:
            corrected_text = correct_text_chunk(text, engine=engine)
        except Exception as e:
            print(f"[ERROR] Chunk {cid} failed with {engine}: {e}")
            corrected_text = text
        corrected.append((cid, corrected_text))
    print(f"[INFO] Corrected {len(corrected)} chunks")
    return corrected
