#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto Token Generator & GitHub Updater
Every 7 hours it runs from GitHub Actions and sends Telegram update.
"""

import json
import time
import asyncio
import httpx
import subprocess
import os
import requests
from typing import Dict, Optional

# --- SETTINGS ---
RELEASEVERSION = "OB50"
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 13; CPH2095 Build/RKQ1.211119.001)"
TELEGRAM_TOKEN = "8088160544:AAGs9dkjiKLwT_ZvBm2v4u-NDL4pnpBB1Ag"
TELEGRAM_CHAT_ID = 6621572366
BRANCH_NAME = "main"
JWT_API_URL = "https://jwt-api-aditya-ffm.vercel.app/token"

# --- TELEGRAM ---
def send_telegram_message(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram Error: {e}")

# --- GIT HELPERS ---
def run_git_command(cmd):
    try:
        result = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
        return result.strip()
    except subprocess.CalledProcessError as e:
        return e.output.strip()

def detect_git_conflict():
    status = run_git_command("git status")
    return "both modified" in status or "Unmerged paths" in status

def resolve_git_conflict():
    print("\n⚠️ Git Conflict Detected. Resolve manually then press Enter.")
    input("➡️ Press Enter after resolving conflicts... ")
    run_git_command("git add .")
    run_git_command("git rebase --continue")
    print("✅ Rebase continued.")

def push_to_git():
    run_git_command(f"git checkout {BRANCH_NAME}")
    run_git_command("git add .")
    run_git_command(f'git commit -m "Auto token update at {time.strftime("%Y-%m-%d %H:%M:%S")}" || echo "No changes"')
    run_git_command(f"git push origin {BRANCH_NAME}")
    print(f"🚀 Changes pushed to {BRANCH_NAME} branch.")

def get_repo_and_filename(region):
    if region == "IND":
        return "token_ind.json"
    elif region in {"BR", "US", "SAC", "NA"}:
        return "token_br.json"
    else:
        return "token_bd.json"

# --- TOKEN GENERATION ---
async def generate_jwt_token(client, uid: str, password: str) -> Optional[Dict]:
    try:
        url = f"{JWT_API_URL}?uid={uid}&password={password}"
        headers = {'User-Agent': USERAGENT, 'Accept': 'application/json'}
        resp = await client.get(url, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        print(f"⚠️ Error generating token for {uid}: {e}")
        return None

async def process_account_with_retry(client, index, uid, password, max_retries=2):
    for attempt in range(max_retries):
        token_data = await generate_jwt_token(client, uid, password)
        if token_data and "token" in token_data:
            return {
                "serial": index + 1,
                "uid": uid,
                "password": password,
                "token": token_data["token"],
                "notiRegion": token_data.get("notiRegion", "")
            }
        if attempt < max_retries - 1:
            print(f"⏳ UID #{index + 1} {uid} - Retry after 1 minute...")
            await asyncio.sleep(60)
    return {"serial": index + 1, "uid": uid, "password": password, "token": None, "notiRegion": ""}

async def generate_tokens_for_region(region):
    start_time = time.time()
    input_file = f"uid_{region}.json"

    if not os.path.exists(input_file):
        msg = f"⚠️ {input_file} not found. Skipping {region}..."
        print(msg)
        send_telegram_message(msg)
        return 0

    with open(input_file, "r") as f:
        accounts = json.load(f)

    total_accounts = len(accounts)
    print(f"🚀 Starting Token Generation for {region} ({total_accounts} accounts)...\n")

    region_tokens = []
    failed_serials, failed_values = [], []

    async with httpx.AsyncClient() as client:
        tasks = [process_account_with_retry(client, i, acc["uid"], acc["password"]) for i, acc in enumerate(accounts)]
        results = await asyncio.gather(*tasks)

        for result in results:
            serial, uid, token, token_region = result["serial"], result["uid"], result["token"], result.get("notiRegion", "")
            if token and token_region == region:
                region_tokens.append({"uid": uid, "token": token})
                print(f"✅ UID #{serial} {uid} - Token OK [{region}]")
            else:
                failed_serials.append(serial)
                failed_values.append(uid)
                print(f"❌ UID #{serial} {uid} - Failed [{region}]")

    output_file = get_repo_and_filename(region)
    with open(output_file, "w") as f:
        json.dump(region_tokens, f, indent=2)

    total_time = time.time() - start_time
    summary = (
        f"✅ *{region} Token Generation Complete*\n\n"
        f"🔹 *Total Tokens:* {len(region_tokens)}\n"
        f"🔢 *Total Accounts:* {total_accounts}\n"
        f"❌ *Failed UIDs:* {len(failed_serials)}\n"
        f"🔸 *Failed Serials:* {', '.join(map(str, failed_serials)) or 'None'}\n"
        f"🔸 *Failed UIDs:* {', '.join(map(str, failed_values)) or 'None'}\n"
        f"⏱️ *Time Taken:* {int(total_time // 60)}m {int(total_time % 60)}s"
    )

    send_telegram_message(summary)
    print(summary)
    return len(region_tokens)

# --- MAIN ---
if __name__ == "__main__":
    regions = ["IND", "BD", "NA"]
    total_tokens = 0

    for region in regions:
        send_telegram_message(f"🤖 {region} Token Generation Started...⚙️")
        tokens_generated = asyncio.run(generate_tokens_for_region(region))
        total_tokens += tokens_generated

    send_telegram_message(f"🤖 All Regions Completed!\nTotal Tokens Generated: {total_tokens}")

    if detect_git_conflict():
        resolve_git_conflict()

    push_to_git()
