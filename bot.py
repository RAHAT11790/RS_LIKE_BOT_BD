#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Final Optimized Token Generator
--------------------------------
✅ Handles API retries & rate limits
✅ Skips permanently blocked UIDs
✅ Telegram notifications for summary & blocked
✅ Auto Git commit & push with replacement of token_bd.json
✅ Concurrent (200 limit)
"""

import json, time, asyncio, httpx, subprocess, os, requests
from typing import Dict, Any, List

# === SETTINGS ===
JWT_API_URL = "https://jwt-api-aditya-ffm.vercel.app/token"
MAX_CONCURRENCY = 200
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 13; CPH2095 Build/RKQ1.211119.001)"
RELEASEVERSION = "OB50"
BLOCKED_FILE = "blocked_uids.json"
PROCESSED_FILE = "processed_uids.json"
BRANCH_NAME = "main"
TELEGRAM_TOKEN = "8468503201:AAEkTmfyFwuMM3BkiVR1WQIlJkdljS5KYHs"
TELEGRAM_CHAT_ID = 6621572366

# === TELEGRAM ===
def send_telegram_message(text: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10
        )
    except Exception as e:
        print(f"[⚠️ Telegram] {e}")

# === GIT ===
def run_git(cmd: str):
    try:
        return subprocess.check_output(cmd, shell=True, text=True).strip()
    except subprocess.CalledProcessError as e:
        return e.output.strip()

def git_push_with_replace(filename: str):
    """
    Force replace given token file and push it to GitHub
    """
    run_git('git config user.name "AutoBot"')
    run_git('git config user.email "autobot@example.com"')
    run_git(f"git checkout {BRANCH_NAME}")
    # ensure file is staged (replace old one)
    run_git(f"git add {filename}")
    commit_msg = f"🔄 Auto replace {filename} ({time.strftime('%Y-%m-%d %H:%M:%S')})"
    run_git(f'git commit -m "{commit_msg}" || echo "No changes"')
    push_result = run_git(f"git push origin {BRANCH_NAME} || echo 'Push failed'")
    print(f"🚀 Git Push: {push_result}")
    send_telegram_message(f"✅ *{filename} replaced & pushed to GitHub!*")

# === FILE HELPERS ===
def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path): return {}
    try:
        with open(path) as f: return json.load(f)
    except: return {}

def save_json(path: str, data: Dict[str, Any]):
    with open(path, "w") as f: json.dump(data, f, indent=2)

# === API LOGIC ===
semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

async def fetch_token(client, uid, pwd) -> Dict[str, Any]:
    url = f"{JWT_API_URL}?uid={uid}&password={pwd}"
    for attempt in range(3):
        try:
            r = await client.get(url, headers={"User-Agent": USERAGENT}, timeout=20)
            if r.status_code == 200:
                try:
                    js = r.json()
                    token = js.get("token") or js.get("jwt") or ""
                    if token and len(token) > 50:
                        return {"ok": True, "token": token}
                except: pass
            elif r.status_code in (401, 403):
                return {"ok": False, "blocked": True}
            elif r.status_code == 429:
                print("⏳ Rate limited, waiting 30s...")
                await asyncio.sleep(30)
                continue
        except Exception as e:
            if attempt == 2: return {"ok": False, "error": str(e)}
            await asyncio.sleep(5)
    return {"ok": False}

async def process_one(client, i, uid, pwd, region, blocked):
    if uid in blocked:
        return {"uid": uid, "blocked": True}

    async with semaphore:
        res = await fetch_token(client, uid, pwd)
        if res.get("ok"):
            print(f"✅ {region} #{i+1} {uid}")
            return {"uid": uid, "token": res["token"]}
        elif res.get("blocked"):
            print(f"🔒 Blocked UID {uid}")
            return {"uid": uid, "blocked": True}
        else:
            print(f"❌ Failed UID {uid}")
            return {"uid": uid}

# === REGION LOOP ===
async def generate_region(region: str):
    input_file = f"uid_{region}.json"
    if not os.path.exists(input_file):
        print(f"⚠️ Missing {input_file}")
        return 0

    data = json.load(open(input_file))
    blocked = load_json(BLOCKED_FILE)
    processed = load_json(PROCESSED_FILE)
    results, tokens = [], []

    async with httpx.AsyncClient() as client:
        tasks = [process_one(client, i, x["uid"], x["password"], region, blocked) for i, x in enumerate(data)]
        results = await asyncio.gather(*tasks)

    for r in results:
        uid = r["uid"]
        if r.get("blocked"):
            blocked[uid] = {"time": time.strftime("%Y-%m-%d %H:%M:%S")}
            send_telegram_message(f"🔒 UID {uid} permanently blocked.")
        elif r.get("token"):
            tokens.append({"uid": uid, "token": r["token"]})
            processed[uid] = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "ok": True}

    # BD region ফাইলের জন্য replace logic
    output_file = f"token_{region.lower()}.json"
    save_json(output_file, tokens)
    save_json(BLOCKED_FILE, blocked)
    save_json(PROCESSED_FILE, processed)

    if region == "BD":
        git_push_with_replace(output_file)  # ✅ Auto replace + push for BD file

    send_telegram_message(f"✅ {region} done — {len(tokens)} tokens")
    return len(tokens)

# === ENTRY ===
if __name__ == "__main__":
    regions = ["IND", "BD", "NA"]
    total = 0
    for r in regions:
        try:
            total += asyncio.run(generate_region(r))
        except Exception as e:
            send_telegram_message(f"⚠️ Error in {r}: {e}")
    send_telegram_message(f"🎯 All done! Total: {total}")
