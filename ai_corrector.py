import requests
import openai
import os
from dotenv import load_dotenv

load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")

# Correct using OpenAI

def correct_with_openai(text):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Correct grammar and fix OCR errors in the given text."},
                {"role": "user", "content": text}
            ],
            temperature=0.3,
            max_tokens=2048
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"[ERROR] OpenAI failed: {e}")
        raise RuntimeError("OpenAI failed.")

# Correct using Ollama

def correct_with_ollama(text):
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3",
                "prompt": f"Correct grammar and spelling:\n\n{text}",
                "stream": False
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["response"].strip()
    except Exception as e:
        print(f"[ERROR] Ollama failed: {e}")
        raise RuntimeError("Ollama failed.")

# Entry point for text chunk correction

def correct_text_chunk(text, engine='ollama'):
    if engine == 'ollama':
        return correct_with_ollama(text)
    elif engine == 'openai':
        return correct_with_openai(text)
    else:
        raise ValueError(f"Unknown engine: {engine}")

# Correct a list of (id, html) chunks

def correct_chunks(chunks, engine='ollama', max_length=2000):
    corrected = []
    for cid, text in chunks:
        if len(text) > max_length:
            print(f"[WARN] Chunk {cid} is too long ({len(text)} chars), splitting…")
            subchunks = [text[i:i+max_length] for i in range(0, len(text), max_length)]
            fixed_parts = []
            for i, part in enumerate(subchunks):
                try:
                    fixed = correct_text_chunk(part, engine)
                    fixed_parts.append(fixed)
                except Exception as e:
                    print(f"[ERROR] Subchunk {i} of {cid}: {e}")
                    fixed_parts.append(part)
            corrected_text = "\n".join(fixed_parts)
        else:
            try:
                corrected_text = correct_text_chunk(text, engine)
            except Exception as e:
                print(f"[ERROR] Failed to correct chunk {cid}: {e}")
                corrected_text = text
        corrected.append((cid, corrected_text))
    print(f"[INFO] Corrected {len(corrected)} chunks, approx {sum(len(t[1]) for t in corrected):,} characters")
    return corrected
