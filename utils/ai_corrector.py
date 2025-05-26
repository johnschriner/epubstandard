import os
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

def correct_text(ocr_text):
    prompt = (
        "This is a passage extracted from an OCR scan of an ebook. "
        "Correct any obvious letter errors, misspellings, or bad line breaks while preserving paragraph structure. "
        "Return the cleaned version of the text.\n\n"
        f"{ocr_text[:5000]}"  # Truncate if too long
    )

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )

    return response.choices[0].message['content']
