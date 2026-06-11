"""
Optional machine-translation helpers (stdlib urllib only).

Providers:
  deepl  — https://api-free.deepl.com / https://api.deepl.com (key in config)
  google — Cloud Translation v2 (API key in config)
  libre  — any LibreTranslate-compatible endpoint (URL in config)
"""

import json
import urllib.request
import urllib.parse

TIMEOUT = 30


def translate(project, texts: list) -> list:
    cfg = project.config
    provider = cfg.get("mtProvider", "none")
    if provider == "none":
        raise RuntimeError("No machine-translation provider configured "
                           "(Project → Translation settings).")
    if not texts:
        return []
    source = cfg.get("mtSource", "JA")
    target = cfg.get("mtTarget", "EN")
    if provider == "deepl":
        return _deepl(cfg.get("mtApiKey", ""), texts, source, target)
    if provider == "google":
        return _google(cfg.get("mtApiKey", ""), texts, source, target)
    if provider == "libre":
        return _libre(cfg.get("mtUrl", ""), cfg.get("mtApiKey", ""),
                      texts, source, target)
    raise RuntimeError(f"unknown MT provider: {provider}")


def _post(url, data: bytes, headers: dict) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"MT request failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MT request failed: {exc.reason}") from exc


def _deepl(api_key, texts, source, target):
    if not api_key:
        raise RuntimeError("DeepL API key is not set.")
    host = "api-free.deepl.com" if api_key.endswith(":fx") else "api.deepl.com"
    params = [("text", t) for t in texts]
    params += [("source_lang", source.upper()), ("target_lang", target.upper())]
    body = urllib.parse.urlencode(params).encode("utf-8")
    data = _post(f"https://{host}/v2/translate", body, {
        "Authorization": f"DeepL-Auth-Key {api_key}",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    return [t["text"] for t in data.get("translations", [])]


def _google(api_key, texts, source, target):
    if not api_key:
        raise RuntimeError("Google API key is not set.")
    body = json.dumps({
        "q": texts,
        "source": source.lower(),
        "target": target.lower(),
        "format": "text",
    }).encode("utf-8")
    url = ("https://translation.googleapis.com/language/translate/v2?key="
           + urllib.parse.quote(api_key))
    data = _post(url, body, {"Content-Type": "application/json"})
    return [t["translatedText"]
            for t in data.get("data", {}).get("translations", [])]


def _libre(url, api_key, texts, source, target):
    if not url:
        raise RuntimeError("LibreTranslate URL is not set.")
    results = []
    for text in texts:
        payload = {"q": text, "source": source.lower(),
                   "target": target.lower(), "format": "text"}
        if api_key:
            payload["api_key"] = api_key
        data = _post(url.rstrip("/") + "/translate",
                     json.dumps(payload).encode("utf-8"),
                     {"Content-Type": "application/json"})
        results.append(data.get("translatedText", ""))
    return results
