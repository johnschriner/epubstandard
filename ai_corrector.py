import os
import re
import time
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL")
if not OLLAMA_BASE_URL:
    raise RuntimeError("OLLAMA_BASE_URL is not set. Set it in your Render environment variables.")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')


def get_engine_base_url(engine):
    if engine == "ollama":
        return OLLAMA_BASE_URL
    elif engine == "openai":
        return "https://api.openai.com/v1"
    else:
        raise ValueError(f"Unknown engine: {engine}")


def correct_with_ollama(text):
    logging.info("Correcting with engine: ollama")
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": f"Fix the OCR and spelling errors in this legal text:\n\n{text}\n\nCorrected version:",
                "stream": False,
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["response"]
    except Exception as e:
        logging.error(f"Ollama failed: {e}")
        return "Ollama failed."


def correct_with_openai(text):
    logging.info("Correcting with engine: openai")
    try:
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": "You are an assistant that fixes OCR and spelling errors in legal text."},
                {"role": "user", "content": text}
            ]
        }
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logging.error(f"OpenAI failed: {e}")
        return "OpenAI failed."


def chunk_text(text, max_chars=5000):
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 < max_chars:
            current += line + "\n"
        else:
            chunks.append(current.strip())
            current = line + "\n"
    if current:
        chunks.append(current.strip())
    return chunks


def correct_chunks(chunks, engine="ollama"):
    corrected_chunks = []
    total_chunks = len(chunks)
    logging.info(f"🛠️ Starting chunk correction...")
    for i, chunk in enumerate(chunks):
        chunk_id = f"id{i}"
        logging.info(f"🧩 Processing chunk {i + 1}/{total_chunks} (id: {chunk_id})")
        if len(chunk) > 6000:
            logging.info(f"🔪 Chunk {chunk_id} is too long ({len(chunk)} chars), splitting…")
            subchunks = chunk_text(chunk, 2000)
            corrected_subs = []
            for j, sub in enumerate(subchunks):
                logging.info(f"⏳ Subchunk {j + 1}/{len(subchunks)} of chunk {chunk_id}")
                if engine == "ollama":
                    corrected = correct_with_ollama(sub)
                elif engine == "openai":
                    corrected = correct_with_openai(sub)
                else:
                    corrected = f"Unknown engine: {engine}"
                if "failed" in corrected.lower():
                    logging.error(f"[ERROR] Subchunk {j + 1} of {chunk_id} failed: {corrected}")
                corrected_subs.append(corrected)
                time.sleep(0.3)
            corrected_chunks.append((chunk_id, " ".join(corrected_subs)))
        else:
            if engine == "ollama":
                corrected = correct_with_ollama(chunk)
            elif engine == "openai":
                corrected = correct_with_openai(chunk)
            else:
                corrected = f"Unknown engine: {engine}"
            if "failed" in corrected.lower():
                logging.error(f"[ERROR] Chunk {chunk_id} failed with {engine}: {corrected}")
            corrected_chunks.append((chunk_id, corrected))
        time.sleep(0.5)
    logging.info(f"✅ Correction complete")
    logging.info(f"📦 Total chunks: {total_chunks}, total characters: {sum(len(c) for c in chunks)}")
    return corrected_chunks
