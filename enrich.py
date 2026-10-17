#!/usr/bin/env python
"""enrich.py — structure items with Gemini Flash Lite.
Fetches HF model cards (non-gated) and uses article text, then asks the LLM for a
clean summary + topic tags. Cached per URL in .cache/llm.json so each item is
processed once. Key pool rotation on 429.
"""
import json, os, glob, sys, time, re
from curl_cffi import requests

ROOT = os.path.expanduser("~/second-brain")
NEWS = os.path.join(ROOT, "news")
CACHE = os.path.join(ROOT, ".cache", "llm.json")
PROV = os.path.expanduser("~/.config/keywatch/providers.json")
MODEL = "gemini-3.1-flash-lite"
BUDGET = int(os.environ.get("ENRICH_BUDGET", "60"))  # max LLM calls per run (gentle on free tier)


def load_keys():
    try:
        d = json.load(open(PROV))
        ks = d["providers"]["gemini"]["keys"]
        return [k["key"] if isinstance(k, dict) else k for k in ks]
    except Exception:
        return []


KEYS = load_keys()


def ask(prompt, state):
    """Call Gemini with rotating keys. Returns text or None."""
    n = len(KEYS)
    for _ in range(n):
        k = KEYS[state["i"] % n]
        state["i"] += 1
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={k}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
        try:
            r = requests.post(url, json=body, impersonate="chrome", timeout=60)
        except Exception:
            continue
        if r.status_code == 200:
            try:
                cand = r.json()["candidates"][0]
                return "".join(p.get("text", "") for p in cand["content"]["parts"])
            except Exception:
                return None
        if r.status_code == 429:
            continue
        return None
    return None


def parse_json(txt):
    if not txt:
        return None
    txt = re.sub(r"^```(json)?|```$", "", txt.strip(), flags=re.M).strip()
    try:
        d = json.loads(txt)
        return d if isinstance(d, dict) else None
    except Exception:
        m = re.search(r"\{.*\}", txt, flags=re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def hf_card(repo):
    for _ in range(2):
        try:
            r = requests.get(f"https://huggingface.co/{repo}/raw/main/README.md",
                             impersonate="chrome", timeout=30)
            if r.status_code == 200:
                return r.text[:3500]
            return ""
        except Exception:
            time.sleep(1)
    return ""


def prompt_for_model(repo, meta, card):
    return (
        f"You are cataloguing an AI model for a personal news dashboard.\n"
        f"Repo: {repo}\n"
        f"Task: {meta.get('pipeline','?')} | Library: {meta.get('library','?')} | "
        f"Downloads: {meta.get('downloads',0)} | Likes: {meta.get('likes',0)}\n"
        f"HF tags: {', '.join(meta.get('tags',[]))}\n\n"
        f"Model card excerpt:\n{card or '(no card available)'}\n\n"
        f"Return JSON: {{\"summary\": \"2 plain sentences describing what this model is and does\", "
        f"\"tags\": [\"5 short topic tags\"]}}"
    )


def prompt_for_news(title, content):
    return (
        f"Summarize this article for a personal AI-news dashboard.\n"
        f"Title: {title}\n\nArticle:\n{content[:4000]}\n\n"
        f"Return JSON: {{\"summary\": \"2 plain sentences, no fluff\", "
        f"\"tags\": [\"5 short topic tags\"]}}"
    )


def main():
    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = json.load(open(CACHE))
        except Exception:
            cache = {}
    state = {"i": 0}
    used = 0

    # gather items in the same order publish.py builds them
    seen = set()
    for path in sorted(glob.glob(os.path.join(NEWS, "*.jsonl"))):
        name = os.path.basename(path).rsplit("_", 1)[0]
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if name.startswith("hf"):
                    url = f"https://huggingface.co/{row.get('repo','')}"
                    key = url.lower()
                    if not key or key in cache:
                        continue
                    if used >= BUDGET:
                        continue
                    used += 1
                    card = hf_card(row.get("repo", ""))
                    meta = {}
                    try:
                        meta = requests.get(f"https://huggingface.co/api/models/{row.get('repo','')}",
                                            impersonate="chrome", timeout=25).json()
                    except Exception:
                        pass
                    out = parse_json(ask(prompt_for_model(row.get("repo", ""), meta, card), state))
                    if out:
                        cache[key] = {"summary": out.get("summary", ""), "tags": out.get("tags", [])[:6]}
                        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
                        json.dump(cache, open(CACHE, "w"))
                else:
                    url = row.get("link", "")
                    key = url.lower() or row.get("title", "").lower()
                    if not key or key in cache:
                        continue
                    content = row.get("content", "")
                    if len(content) < 120:
                        continue
                    if used >= BUDGET:
                        continue
                    used += 1
                    out = parse_json(ask(prompt_for_news(row.get("title", ""), content), state))
                    if out:
                        cache[key] = {"summary": out.get("summary", ""), "tags": out.get("tags", [])[:6]}
                        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
                        json.dump(cache, open(CACHE, "w"))

    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(cache, open(CACHE, "w"))
    print(f"enrich: {used} LLM calls, cache={len(cache)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())