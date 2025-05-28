# ai_corrector.py

import os
import time
import requests
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def correct_text_chunk(text, engine="ollama", model="llama3"):
    log(f"Correcting with engine: {engine}")
    if engine == "openai":
        return correct_with_openai(text)
    elif engine == "ollama":
        return correct_with_ollama(text, model)
    else:
        raise ValueError(f"Unknown engine: {engine}")

def correct_with_openai(text):
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You're an expert copyeditor. Fix OCR mistakes in the provided text."},
                {"role": "user", "content": text},
            ],
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        log(f"[ERROR] OpenAI correction failed: {e}")
        raise RuntimeError("OpenAI failed.")

def correct_with_ollama(text, model):
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": f"You're an expert copyeditor. Fix OCR mistakes in the following text:\n\n{text}",
                "stream": False,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except Exception as e:
        log(f"[ERROR] Ollama failed: {e}")
        raise RuntimeError("Ollama failed.")

def correct_chunks(chunks, max_length=2000, engine="ollama"):
    corrected = []
    total_chunks = len(chunks)
    total_subchunks = 0

    log("🛠️ Starting chunk correction...")
    for i, (cid, text) in enumerate(chunks):
        log(f"🧩 Processing chunk {i + 1}/{total_chunks} (id: {cid})")

        if len(text) > max_length:
            log(f"🔪 Chunk {cid} is too long ({len(text)} chars), splitting…")
            subchunks = [text[j:j + max_length] for j in range(0, len(text), max_length)]
            fixed_parts = []
            for k, part in enumerate(subchunks):
                try:
                    log(f"⏳ Subchunk {k + 1}/{len(subchunks)} of chunk {cid}")
                    fixed = correct_text_chunk(part, engine=engine)
                    fixed_parts.append(fixed)
                except Exception as e:
                    log(f"[ERROR] Subchunk {k + 1} of {cid} failed: {e}")
                    fixed_parts.append(part)
            corrected_text = "\n".join(fixed_parts)
            total_subchunks += len(subchunks)
        else:
            try:
                corrected_text = correct_text_chunk(text, engine=engine)
            except Exception as e:
                log(f"[ERROR] Chunk {cid} failed: {e}")
                corrected_text = text

        corrected.append((cid, corrected_text))

    total_chars = sum(len(t[1]) for t in corrected)
    log("✅ Correction complete")
    log(f"📦 Total chunks: {total_chunks}, subchunks: {total_subchunks}, total characters: {total_chars}")
    return corrected
