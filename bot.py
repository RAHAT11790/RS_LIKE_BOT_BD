#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Auto Token Generator with:
- concurrency limit (max 200 concurrent requests)
- blocked UID handling (persisted to blocked_uids.json)
- telegram notifications for blocked/dead tokens
- region-wise token output files
- git push support
"""

import json
import time
import asyncio
import httpx
import subprocess
import os
import requests
from typing import Dict, Optional, List, Any

# --- SETTINGS ---
RELEASEVERSION = "OB50"
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 13; CPH2095 Build/RKQ1.211119.001)"
TELEGRAM_TOKEN = "8468503201:AAEkTmfyFwuMM3BkiVR1WQIlJkdljS5KYHs"
TELEGRAM_CHAT_ID = 6621572366
BRANCH_NAME = "main"
JWT_API_URL = "https://jwt-api-aditya-ffm.vercel.app/token"

# Concurrency limit (as requested: 200 at a time)
MAX_CONCURRENCY = 200

# Files
BLOCKED_FILE = "blocked_uids.json"   # permanently blocked UIDs (never try again)
PROCESSED_FILE = "processed_uids.json"  # optional: record of processed uids (success/fail)

# --- TELEGRAM ---
def send_telegram_message(message: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        # don't crash if Telegram is unreachable
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram Error: {e}")

# --- GIT HELPERS ---
def run_git_command(cmd: str) -> str:
    try:
        result = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
        return result.strip()
    except subprocess.CalledProcessError as e:
        return e.output.strip()

def detect_git_conflict() -> bool:
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

def get_repo_and_filename(region: str) -> str:
    if region == "IND":
        return "token_ind.json"
    elif region in {"BR", "US", "SAC", "NA"}:
        return "token_br.json"
    else:
        return "token_bd.json"

# --- BLOCKED UID STORAGE ---
def load_blocked_uids() -> Dict[str, Any]:
    if not os.path.exists(BLOCKED_FILE):
        return {}
    try:
        with open(BLOCKED_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_blocked_uids(blocked: Dict[str, Any]):
    try:
        with open(BLOCKED_FILE, "w") as f:
            json.dump(blocked, f, indent=2)
    except Exception as e:
        print(f"Error saving blocked file: {e}")

def mark_uid_blocked(uid: str, reason: str):
    blocked = load_blocked_uids()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    blocked[uid] = {"blocked_at": ts, "reason": reason}
    save_blocked_uids(blocked)
    # notify immediately
    send_telegram_message(f"🔒 *UID BLOCKED*\nUID: `{uid}`\nReason: {reason}\nTime: {ts}")

# Optional: processed record to avoid duplicates or for audit
def load_processed() -> Dict[str, Any]:
    if not os.path.exists(PROCESSED_FILE):
        return {}
    try:
        with open(PROCESSED_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_processed(processed: Dict[str, Any]):
    try:
        with open(PROCESSED_FILE, "w") as f:
            json.dump(processed, f, indent=2)
    except Exception as e:
        print(f"Error saving processed file: {e}")

# --- TOKEN GENERATION ---
# We will inspect response status and text to detect blocked/dead accounts.
async def generate_jwt_token(client: httpx.AsyncClient, uid: str, password: str) -> Dict[str, Any]:
    url = f"{JWT_API_URL}?uid={uid}&password={password}"
    headers = {'User-Agent': USERAGENT, 'Accept': 'application/json'}
    try:
        resp = await client.get(url, headers=headers, timeout=30)
    except Exception as e:
        return {"ok": False, "error": f"request_exception: {e}", "blocked": False, "status": None}

    status = resp.status_code
    text = ""
    try:
        text = resp.text or ""
    except Exception:
        text = ""

    # quick blocked heuristics
    lowered = text.lower()
    if status in (401, 403):
        return {"ok": False, "error": f"HTTP {status}", "blocked": True, "status": status, "body": text}
    if status == 429:
        # rate-limited — not necessarily blocked; mark not blocked but failed
        return {"ok": False, "error": f"HTTP {status} - rate limit", "blocked": False, "status": status, "body": text}
    if "block" in lowered or "blocked" in lowered or "banned" in lowered:
        return {"ok": False, "error": "detected_block_in_body", "blocked": True, "status": status, "body": text}

    # success path: try parse json
    if status == 200:
        try:
            data = resp.json()
            # If API returns something telling account is invalid, treat accordingly
            if not data:
                return {"ok": False, "error": "empty_json", "blocked": False, "status": status, "body": text}
            # if data includes token
            return {"ok": True, "data": data, "blocked": False, "status": status}
        except Exception as e:
            return {"ok": False, "error": f"json_parse_error: {e}", "blocked": False, "status": status, "body": text}

    # fallback: treat other statuses as failed but not necessarily blocked
    return {"ok": False, "error": f"HTTP {status}", "blocked": False, "status": status, "body": text}

# semaphore will control concurrency
semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

async def process_account(client: httpx.AsyncClient, index: int, uid: str, password: str, region: str, processed: Dict[str, Any], blocked_cache: Dict[str, Any]) -> Dict[str, Any]:
    # Skip if already marked blocked in memory
    if uid in blocked_cache:
        return {"serial": index + 1, "uid": uid, "token": None, "blocked": True, "reason": "already_blocked"}

    # use semaphore to limit concurrent requests
    async with semaphore:
        # small retries for transient network errors
        max_retries = 2
        for attempt in range(1, max_retries + 1):
            res = await generate_jwt_token(client, uid, password)
            if res.get("ok"):
                data = res["data"]
                # check token presence and region match
                token = data.get("token") or data.get("access_token") or data.get("jwt")
                noti_region = data.get("notiRegion", "") or data.get("region", "") or data.get("noti_region", "")
                # if token found and region matches (or noti_region empty -> accept)
                if token and (noti_region == region or noti_region == "" or noti_region is None):
                    # store processed
                    processed[uid] = {"serial": index + 1, "status": "success", "time": time.strftime("%Y-%m-%d %H:%M:%S")}
                    return {"serial": index + 1, "uid": uid, "token": token, "notiRegion": noti_region, "blocked": False}
                else:
                    # treat as fail (maybe wrong region)
                    processed[uid] = {"serial": index + 1, "status": "no_token_or_region_mismatch", "time": time.strftime("%Y-%m-%d %H:%M:%S"), "raw": data}
                    # If there's no token, but api returned something, we'll not mark blocked immediately
                    return {"serial": index + 1, "uid": uid, "token": None, "blocked": False, "error": "no_token_or_region_mismatch", "raw": data}
            else:
                # not ok
                reason = res.get("error", "unknown_error")
                is_blocked = res.get("blocked", False)
                processed[uid] = {"serial": index + 1, "status": "failed", "time": time.strftime("%Y-%m-%d %H:%M:%S"), "error": reason, "status_code": res.get("status")}
                # if blocked -> mark and return
                if is_blocked:
                    return {"serial": index + 1, "uid": uid, "token": None, "blocked": True, "reason": reason, "raw": res.get("body", "")}
                # otherwise, retry a bit (if attempts left)
                if attempt < max_retries:
                    await asyncio.sleep(5)
                    continue
                else:
                    return {"serial": index + 1, "uid": uid, "token": None, "blocked": False, "reason": reason, "raw": res.get("body", "")}

# --- MAIN REGION PROCESSOR ---
async def generate_tokens_for_region(region: str) -> int:
    start_time = time.time()
    input_file = f"uid_{region}.json"

    if not os.path.exists(input_file):
        msg = f"⚠️ {input_file} not found. Skipping {region}..."
        print(msg)
        send_telegram_message(msg)
        return 0

    # load accounts
    with open(input_file, "r") as f:
        try:
            accounts = json.load(f)
        except Exception as e:
            send_telegram_message(f"⚠️ Failed to parse {input_file}: {e}")
            return 0

    total_accounts = len(accounts)
    print(f"🚀 Starting Token Generation for {region} ({total_accounts} accounts)...")

    # load blocked and processed caches
    blocked_cache = load_blocked_uids()  # dict keyed by uid
    processed = load_processed()

    region_tokens: List[Dict[str, Any]] = []
    failed_serials: List[int] = []
    failed_values: List[str] = []

    async with httpx.AsyncClient() as client:
        tasks = []
        for index, account in enumerate(accounts):
            uid = account.get("uid")
            pwd = account.get("password") or account.get("pwd") or account.get("pass", "")
            if not uid:
                continue
            # skip if already blocked
            if uid in blocked_cache:
                print(f"⏭️ Skipping blocked UID #{index+1} {uid}")
                continue
            # create tasks
            tasks.append(process_account(client, index, uid, pwd, region, processed, blocked_cache))

        # run tasks (concurrency limited by semaphore inside)
        if tasks:
            results = await asyncio.gather(*tasks)
        else:
            results = []

        # handle results
        for result in results:
            serial = result.get("serial")
            uid = result.get("uid")
            token = result.get("token")
            if result.get("blocked"):
                reason = result.get("reason", "blocked_by_api")
                # persist to blocked file
                mark_uid_blocked(uid, reason)
                failed_serials.append(serial)
                failed_values.append(uid)
                print(f"🔒 UID #{serial} {uid} marked blocked ({reason})")
            elif token:
                region_tokens.append({"uid": uid, "token": token})
                print(f"✅ UID #{serial} {uid} - token saved")
            else:
                # failed but not blocked
                failed_serials.append(serial)
                failed_values.append(uid)
                print(f"❌ UID #{serial} {uid} - token generation failed ({result.get('reason')})")

    # write tokens to file
    output_file = get_repo_and_filename(region)
    try:
        with open(output_file, "w") as f:
            json.dump(region_tokens, f, indent=2)
    except Exception as e:
        print(f"Error writing {output_file}: {e}")

    # save processed log
    save_processed(processed)

    total_time = time.time() - start_time
    minutes = int(total_time // 60)
    seconds = int(total_time % 60)

    summary = (
        f"✅ *{region} Token Generation Complete*\n\n"
        f"🔹 *Total Tokens Saved:* {len(region_tokens)}\n"
        f"🔢 *Accounts Checked:* {total_accounts}\n"
        f"❌ *Failed (non-block):* {len(failed_serials)}\n"
        f"🔸 *Failed Serials:* {', '.join(map(str, failed_serials)) or 'None'}\n"
        f"🔸 *Failed UIDs:* {', '.join(map(str, failed_values)) or 'None'}\n"
        f"⏱️ *Time Taken:* {minutes}m {seconds}s\n"
        f"🔒 *Blocked UID File:* `{BLOCKED_FILE}`"
    )
    send_telegram_message(summary)
    print(summary)

    return len(region_tokens)

# --- ENTRY POINT ---
if __name__ == "__main__":
    regions = ["IND", "BD", "NA"]  # adjust as needed
    total_tokens = 0

    # run region by region (each region itself will limit concurrency to MAX_CONCURRENCY)
    for region in regions:
        send_telegram_message(f"🤖 {region} Token Generation Started... (Concurrency: {MAX_CONCURRENCY})")
        try:
            tokens_generated = asyncio.run(generate_tokens_for_region(region))
        except Exception as e:
            tokens_generated = 0
            send_telegram_message(f"⚠️ Exception while processing {region}: {e}")
        total_tokens += tokens_generated

    send_telegram_message(f"🤖 All Regions Completed!\nTotal Tokens Generated: {total_tokens}")

    # git conflict handling & push
    if detect_git_conflict():
        resolve_git_conflict()

    try:
        push_to_git()
    except Exception as e:
        print(f"Git push error: {e}")
        send_telegram_message(f"⚠️ Git push error: {e}")
