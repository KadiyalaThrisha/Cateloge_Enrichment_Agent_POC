import os
from pathlib import Path
from groq import Groq


def _get_groq_api_key() -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if api_key:
        return api_key

    # Fallback: read project .env without extra dependencies.
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "GROQ_API_KEY" and value.strip():
                return value.strip().strip("\"'")

    raise ValueError("GROQ_API_KEY is missing. Set it in environment or .env file.")


client = Groq(api_key=_get_groq_api_key())

def call_groq_llm(prompt, image_url=None, *, max_completion_tokens: int = 50):

    content = [{"type": "text", "text": prompt}]

    if image_url:
        content.append({
            "type": "image_url",
            "image_url": {"url": image_url}
        })

    completion = client.chat.completions.create(
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        messages=[{"role": "user", "content": content}],
        temperature=0,
        max_completion_tokens=max_completion_tokens,
    )

    return completion.choices[0].message.content