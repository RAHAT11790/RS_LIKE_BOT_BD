#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto Token Generator (BD Push Fixed + Batch 200 Limit)
"""

import os, json, time, asyncio, httpx, subprocess, requests
from typing import Dict, Any

JWT_API_URL = "https://jwt-api-aditya-ffm.vercel.app/token"
USERAGENT = "Dalvik/2.1.0 (Linux; Android 13; CPH2095 Build/RKQ1.211119.001)"
TELEGRAM_TOKEN = "8468503201:AAEkTmfyFwuMM3BkiVR1WQIlJkdljS5KYHs"
TELEGRAM_CHAT_ID = 6621572366
BATCH_SIZE = 200
BRANCH_NAME = "main"

BLOCKED_FILE = "blocked_uids.json"
PROCESSED_FILE = "processed_uids.json"


def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
    except:
        pass


def git_push_replace(filename):
    os.system('git config user.name "AutoBot"')
    os.system('git config user.email "autobot@example.com"')
    os.system(f"git checkout {BRANCH_NAME}")
    os.system(f"git add {filename}")
    os.system(f'git commit -m "Replace {filename}" || echo "no change"')
    os.system(f"git push origin {BRANCH_NAME} || echo 'Push failed'")
    send_telegram(f"✅ GitHub updated: {filename}")


def load_json(file):
    return json.load(open(file)) if os.path.exists(file) else {}


def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)


async def get_token(client, uid, pwd):
    url = f"{JWT_API_URL}?uid={uid}&password={pwd}"
    try:
        r = await client.get(url, headers={"User-Agent": USERAGENT}, timeout=25)
        if r.status_code == 200:
            j = r.json()
            token = j.get("token") or j.get("jwt")
            if token:
                return {"ok": True, "token": token}
        elif r.status_code in (401, 403):
            return {"ok": False, "blocked": True}
    except:
        pass
    return {"ok": False}


async def handle_one(client, acc, blocked):
    uid, pwd = acc["uid"], acc["password"]
    if uid in blocked:
        return {"uid": uid, "blocked": True}

    res = await get_token(client, uid, pwd)
    if res.get("ok"):
        print(f"✅ Token OK: {uid}")
        return {"uid": uid, "token": res["token"]}
    elif res.get("blocked"):
        print(f"🚫 Blocked UID: {uid}")
        return {"uid": uid, "blocked": True}
    else:
        print(f"❌ Failed UID: {uid}")
        return {"uid": uid}


async def generate_region(region: str):
    input_file = f"uid_{region}.json"
    if not os.path.exists(input_file):
        send_telegram(f"⚠️ Missing file: {input_file}")
        return 0

    data = json.load(open(input_file))
    blocked = load_json(BLOCKED_FILE)
    processed = load_json(PROCESSED_FILE)
    tokens = []
    total = len(data)

    async with httpx.AsyncClient() as client:
        for i in range(0, total, BATCH_SIZE):
            batch = data[i : i + BATCH_SIZE]
            print(f"\n⚙️ {region}: Batch {i//BATCH_SIZE+1} ({len(batch)} UIDs)")
            res = await asyncio.gather(*[handle_one(client, a, blocked) for a in batch])

            new_tokens = []
            for r in res:
                uid = r["uid"]
                if r.get("blocked"):
                    blocked[uid] = True
                    send_telegram(f"🚫 UID {uid} blocked permanently.")
                elif r.get("token"):
                    new_tokens.append({"uid": uid, "token": r["token"]})
                    processed[uid] = True

            tokens.extend(new_tokens)
            save_json(f"token_{region.lower()}.json", tokens)
            save_json(BLOCKED_FILE, blocked)
            save_json(PROCESSED_FILE, processed)

            send_telegram(f"✅ {region} batch done ({len(new_tokens)} tokens).")

            if region == "BD":
                git_push_replace(f"token_{region.lower()}.json")

            await asyncio.sleep(10)

    send_telegram(f"🎯 {region} finished. Total: {len(tokens)} tokens.")
    return len(tokens)


if __name__ == "__main__":
    regions = ["IND", "BD", "NA"]
    total = 0
    for r in regions:
        total += asyncio.run(generate_region(r))
    send_telegram(f"🏁 All Done. Total Tokens: {total}")
