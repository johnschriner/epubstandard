import os
import re
import logging
import time
from dotenv import load_dotenv
from datetime import datetime

# Define timestamp for logging
def timestamp():
    return datetime.now().strftime("%H:%M:%S")

load_dotenv()

USE_OLLAMA = os.getenv("USE_OLLAMA", "false").lower() == "true"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ENGINE = "ollama" if USE_OLLAMA else "gpt-3.5-turbo"

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")

# --- Tokenizer ---
def num_tokens_from_string(string):
    # Approximate count: 1 token ≈ 4 characters
    return max(1, len(string) // 4)

# --- Chunking ---
def chunk_html_sections(sections, max_tokens=2000):
    logging.info("📚 Chunking HTML sections...")
    chunks = []
    current_chunk = ""
    current_id = 0

    for section_id, html in sections:
        estimated_tokens = num_tokens_from_string(current_chunk + html)
        if estimated_tokens > max_tokens and current_chunk:
            chunks.append((f"id{current_id}", current_chunk.strip()))
            current_id += 1
            current_chunk = html
        else:
            current_chunk += "\n\n" + html

    if current_chunk.strip():
        chunks.append((f"id{current_id}", current_chunk.strip()))

    logging.info(f"✅ Created {len(chunks)} chunks")
    return chunks

# --- Correction Driver ---
def correct_chunks(chunks, engine='ollama'):
    print(f"[{timestamp()}] 🛠️ Starting chunk correction...")
    corrected = []

    for i, (chunk_id, chunk_text) in enumerate(chunks):
        print(f"[{timestamp()}] 🧩 Processing chunk {i + 1}/{len(chunks)} (id: {chunk_id})")
        try:
            if engine == 'ollama':
                print(f"[{timestamp()}] Correcting with engine: ollama")
                corrected_text = correct_with_ollama(chunk_text)
            else:
                print(f"[{timestamp()}] Correcting with engine: openai")
                corrected_text = correct_with_openai(chunk_text)
            corrected.append((chunk_id, corrected_text))
        except Exception as e:
            print(f"[{timestamp()}] [ERROR] Chunk {chunk_id} failed with {engine}: {e}")
            corrected.append((chunk_id, chunk_text))  # fallback to original

    print(f"[{timestamp()}] ✅ Correction complete")
    print(f"[{timestamp()}] 📦 Total chunks: {len(corrected)}, total characters: {sum(len(c[1]) for c in corrected)}")
    return corrected


# --- Ollama Correction ---
def correct_with_ollama(chunk, model="mistral", base_url=None):
    import requests
    import json

    if not base_url:
        base_url = OLLAMA_BASE_URL

    logging.info(f"Correcting with engine: ollama")
    try:
        prompt = (
            "You are a text cleanup assistant. Fix OCR errors, spelling mistakes, and formatting problems in the following HTML content. "
            "Do not change the structure unless necessary. Return valid HTML with corrected content."
        )

        url = f"{base_url.rstrip('/')}/api/generate"
        headers = {
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true",  # 👈 bypasses splash page
        }

        data = {
            "model": model,
            "prompt": prompt + "\n\n" + chunk,
            "stream": False,
        }

        response = requests.post(url, headers=headers, json=data, timeout=60)
        response.raise_for_status()
        result = response.json()
        corrected_text = result.get("response", "")
        return corrected_text.strip()

    except Exception as e:
        logging.error(f"Ollama failed: {e}")
        raise RuntimeError("Ollama failed.") from e

# --- OpenAI Correction ---
def correct_with_openai(chunk):
    import openai

    openai.api_key = OPENAI_API_KEY
    prompt = (
        "You are a text cleanup assistant. Fix OCR errors, spelling mistakes, and formatting problems in the following HTML content. "
        "Do not change the structure unless necessary. Return valid HTML with corrected content."
    )

    try:
        response = openai.ChatCompletion.create(
            model=ENGINE,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": chunk},
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"OpenAI failed: {e}")
        raise RuntimeError("OpenAI failed.") from e
