"""
Optional LLM providers for Chhanni (story-from-concept, smarter mapping).
All are plain HTTPS calls - no SDKs.
  none    : rule-based only (default, free, offline)
  ollama  : local & free  (http://127.0.0.1:11434, e.g. qwen3:8b, gemma3:12b, llama3.1:8b)
  gemini  : Google AI Studio API key (free tier available)  e.g. gemini-flash-latest
  claude  : Anthropic API key  e.g. claude-haiku-5-5 / claude-sonnet-5-5
  openai  : any OpenAI-compatible server (LM Studio, Groq, OpenRouter...) - base_url + key + model
Settings live in %APPDATA%\\Chhanni\\settings.json on this PC only.
"""
import json
import os
import re
import urllib.request

DEFAULT_MODELS = {"ollama": "qwen3:8b", "gemini": "gemini-flash-latest", "claude": "claude-haiku-5-5",
                  "openai": "gpt-4o-mini"}


def settings_path():
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Chhanni")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "settings.json")


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(new):
    s = load_settings()
    s.update({k: v for k, v in new.items() if v is not None})
    with open(settings_path(), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    return s


def public_settings():
    s = load_settings()
    s = dict(s)
    if s.get("api_key"):
        k = s["api_key"]
        s["api_key"] = (k[:4] + "…" + k[-4:]) if len(k) > 10 else "set"
        s["has_key"] = True
    return s


def _post(url, body, headers, timeout=180):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=dict({"Content-Type": "application/json"}, **headers))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def chat(system, user, want_json=True, settings=None, timeout=240):
    s = settings or load_settings()
    prov = s.get("llm_provider", "none")
    model = s.get("llm_model") or (s.get("vlm_model") if prov == "ollama" else None) or DEFAULT_MODELS.get(prov, "")
    key = s.get("api_key", "")
    if prov == "none":
        raise RuntimeError("LLM provider set nahi hai (AI settings).")
    if prov == "ollama":
        base = s.get("base_url") or "http://127.0.0.1:11434"
        body = {"model": model, "stream": False, "messages": [{"role": "system", "content": system},
                                                               {"role": "user", "content": user}],
                "options": {"temperature": 0.3, "num_ctx": int(s.get("num_ctx") or 16384)}, "think": False,
                "keep_alive": "15m"}
        if want_json:
            body["format"] = "json"
        r = _post(base.rstrip("/") + "/api/chat", body, {}, timeout)
        text = r["message"]["content"]
    elif prov == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0.3}}
        if want_json:
            body["generationConfig"]["responseMimeType"] = "application/json"
        r = _post(url, body, {"x-goog-api-key": key}, timeout)
        text = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
    elif prov == "claude":
        body = {"model": model, "max_tokens": 8000, "system": system, "temperature": 0.3,
                "messages": [{"role": "user", "content": user}]}
        r = _post("https://api.anthropic.com/v1/messages", body,
                  {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
        text = "".join(b.get("text", "") for b in r["content"] if b.get("type") == "text")
    elif prov == "openai":
        base = s.get("base_url") or "https://api.openai.com/v1"
        body = {"model": model, "temperature": 0.3, "messages": [{"role": "system", "content": system},
                                                                 {"role": "user", "content": user}]}
        if want_json:
            body["response_format"] = {"type": "json_object"}
        r = _post(base.rstrip("/") + "/chat/completions", body, {"Authorization": f"Bearer {key}"}, timeout)
        text = r["choices"][0]["message"]["content"]
    else:
        raise RuntimeError(f"unknown provider {prov}")
    if not want_json:
        return text
    return parse_json(text)


def parse_json(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except Exception:
        # models sometimes emit two JSON objects or trailing text ("Extra data"): take the first complete object
        dec = json.JSONDecoder()
        for ch in "{[":
            i = text.find(ch)
            while i >= 0:
                try:
                    return dec.raw_decode(text[i:])[0]
                except Exception:
                    i = text.find(ch, i + 1)
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            return json.loads(text[a:b + 1])
        a, b = text.find("["), text.rfind("]")
        if a >= 0 and b > a:
            return json.loads(text[a:b + 1])
        raise RuntimeError("LLM ne valid JSON nahi diya: " + text[:200])


def test(settings=None):
    r = chat("Reply with JSON only.", 'Return {"ok": true, "lang": "hinglish"}', True, settings, timeout=60)
    return bool(r.get("ok"))
