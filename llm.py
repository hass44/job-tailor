"""Talk to Claude, OpenAI or Gemini with the user's own key, or to a logged-in Claude Code."""
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

PROVIDERS = {
    "anthropic": {"label": "Claude", "env": "ANTHROPIC_API_KEY"},
    "openai": {"label": "OpenAI", "env": "OPENAI_API_KEY"},
    "gemini": {"label": "Gemini", "env": "GEMINI_API_KEY"},
}
CLAUDE_CODE = "claude-code"
MAX_TOKENS = 16000
OPENAI_SKIP = re.compile(r"audio|realtime|tts|whisper|transcribe|image|dall-e|embedding|moderation|search|"
                         r"instruct|codex|davinci|babbage|sora", re.I)
GEMINI_SKIP = re.compile(r"embedding|image|tts|aqa|live|vision|learnlm|robotics|computer-use", re.I)


class LLMError(Exception):
    pass


def request_json(url, headers, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            raise LLMError(f"The provider rejected this API key ({e.code}). Check it on the Settings tab.") from e
        if e.code == 429:
            raise LLMError(f"Rate limit or out of credit (429): {detail}") from e
        raise LLMError(f"API error {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"Could not reach the provider: {e.reason}") from e


def headers_for(provider, key):
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if provider == "openai":
        return {"authorization": f"Bearer {key}"}
    return {"x-goog-api-key": key}  # a header, so the key never appears in a URL or an error message


def list_models(provider, key):
    """Models this key can use, best default first. Asked live because model names change often."""
    headers = headers_for(provider, key)
    if provider == "anthropic":
        ids = [m["id"] for m in request_json("https://api.anthropic.com/v1/models?limit=100", headers)["data"]]
        return sorted(ids, key=lambda m: "sonnet" not in m)  # stable: keeps newest first within each group
    if provider == "openai":
        models = request_json("https://api.openai.com/v1/models", headers)["data"]
        models = [m for m in models if re.match(r"(gpt-|o\d)", m["id"]) and not OPENAI_SKIP.search(m["id"])]
        models.sort(key=lambda m: m.get("created", 0), reverse=True)
        ids = [m["id"] for m in models]
        return sorted(ids, key=lambda m: bool(re.search(r"mini|nano|preview", m)))
    models = request_json("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers)["models"]
    ids = [m["name"].removeprefix("models/") for m in models
           if "generateContent" in m.get("supportedGenerationMethods", []) and not GEMINI_SKIP.search(m["name"])]
    ids.sort(reverse=True)  # version numbers: newest first
    return sorted(ids, key=lambda m: ("preview" in m or "exp" in m, "flash" not in m))


def complete(provider, key, model, prompt, pdf=None):
    """One prompt, optionally with a PDF attached. Returns the reply text."""
    b64 = base64.b64encode(pdf).decode() if pdf else None
    headers = headers_for(provider, key)
    if provider == "anthropic":
        content = [{"type": "text", "text": prompt}]
        if pdf:
            content.insert(0, {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}})
        reply = request_json("https://api.anthropic.com/v1/messages", headers,
                             {"model": model, "max_tokens": MAX_TOKENS, "messages": [{"role": "user", "content": content}]})
        return "".join(b.get("text", "") for b in reply.get("content", []))
    if provider == "openai":
        content = [{"type": "text", "text": prompt}]
        if pdf:
            content.insert(0, {"type": "file", "file": {"filename": "cv.pdf", "file_data": f"data:application/pdf;base64,{b64}"}})
        reply = request_json("https://api.openai.com/v1/chat/completions", headers,
                             {"model": model, "max_completion_tokens": MAX_TOKENS,
                              "messages": [{"role": "user", "content": content}]})
        return reply["choices"][0]["message"].get("content") or ""
    parts = [{"text": prompt}]
    if pdf:
        parts.insert(0, {"inline_data": {"mime_type": "application/pdf", "data": b64}})
    reply = request_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", headers,
                         {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"maxOutputTokens": MAX_TOKENS}})
    candidates = reply.get("candidates") or []
    if not candidates:
        raise LLMError(f"Gemini returned nothing: {str(reply.get('promptFeedback', ''))[:200]}")
    return "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []))


def claude_cli():
    found = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if found:
        return found
    local = glob.glob(os.path.join(os.path.expanduser("~"), ".local", "bin", "claude*"))
    return local[0] if local else None


def call_cli(exe, prompt):
    # No tools and an empty working directory: the posting text is untrusted and must not be able to do anything
    with tempfile.TemporaryDirectory() as cwd:
        try:
            done = subprocess.run([exe, "-p", "--output-format", "text", "--tools", ""], input=prompt, cwd=cwd,
                                  capture_output=True, text=True, encoding="utf-8", timeout=420)
        except subprocess.TimeoutExpired as e:
            raise LLMError("Claude Code timed out.") from e
    if done.returncode != 0:
        msg = (done.stderr or done.stdout).strip()[:300]
        hint = " Run `claude` once in a terminal and use /login." if "log" in msg.lower() else ""
        raise LLMError(f"Claude Code failed: {msg}.{hint}")
    return done.stdout


def key_for(settings, provider):
    return os.environ.get(PROVIDERS[provider]["env"]) or settings.get("keys", {}).get(provider, "")


def active(settings):
    """The provider to use: the chosen one if usable, else the first that has a key, else Claude Code."""
    chosen = settings.get("provider")
    if chosen in PROVIDERS and key_for(settings, chosen):
        return chosen
    if chosen == CLAUDE_CODE and claude_cli():
        return chosen
    for provider in PROVIDERS:
        if key_for(settings, provider):
            return provider
    return CLAUDE_CODE if claude_cli() else None


def model_for(settings, provider):
    model = settings.get("models", {}).get(provider)
    if model:
        return model
    models = list_models(provider, key_for(settings, provider))
    if not models:
        raise LLMError("This key has no usable models.")
    return models[0]


def ask(settings, prompt, pdf=None):
    provider = active(settings)
    if provider is None:
        raise LLMError("No AI connected yet. Add a Claude, OpenAI or Gemini key on the Settings tab.")
    if provider == CLAUDE_CODE:
        if pdf:
            raise LLMError("Reading a PDF needs an API key. Otherwise upload a DOCX or paste the CV text.")
        return call_cli(claude_cli(), prompt)
    return complete(provider, key_for(settings, provider), model_for(settings, provider), prompt, pdf)
