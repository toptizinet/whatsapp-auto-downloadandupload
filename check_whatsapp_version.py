#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
监测 Uptodown 上 WhatsApp 的版本更新。
逻辑：
1. 访问搜索页，找到 WhatsApp 的详情页链接（详情页比搜索页结构更稳定）
2. 从详情页提取当前版本号
3. 和上次记录的版本号（存在 last_version.json 里）对比
4. 如果不同，就通过 Server酱 / Telegram 发送通知，并更新记录
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import re
import sys

SEARCH_URL = "https://cn.uptodown.com/android/search?query=whatsapp"
STATE_FILE = "last_version.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def get_whatsapp_detail_url():
    """在搜索结果页中找到 WhatsApp 详情页的链接"""
    resp = requests.get(SEARCH_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    candidates = soup.select("a[href*='uptodown.com']")
    scored = []
    for a in candidates:
        href = a.get("href", "")
        if not href:
            continue
        # 详情页链接通常形如 https://whatsapp.en.uptodown.com/android
        # 或 https://cn.uptodown.com/android/whatsapp
        if re.search(r"whatsapp[\w\-\.]*uptodown\.com", href) or re.search(
            r"/android/whatsapp(?:-messenger)?/?$", href
        ):
            scored.append(href)

    if scored:
        # 去重，取第一个
        return scored[0]

    raise RuntimeError(
        "未能在搜索结果中找到 WhatsApp 的详情页链接，"
        "页面结构可能已发生变化，需要人工检查一次搜索页 HTML。"
    )


def get_version_from_detail(url):
    """从详情页提取版本号"""
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Uptodown 详情页常见结构：<div class="version">2.24.x.x</div>
    version_tag = soup.select_one("div.version") or soup.select_one("span.version")
    if version_tag and version_tag.get_text(strip=True):
        return version_tag.get_text(strip=True), url

    # 兜底：在页面文本里用正则找版本号
    text = soup.get_text(" ", strip=True)
    m = re.search(r"(\d+\.\d+(?:\.\d+){1,3})", text)
    if m:
        return m.group(1), url

    raise RuntimeError(
        "未能在详情页提取版本号，页面结构可能已发生变化，"
        "需要人工检查一次详情页 HTML。"
    )


def load_last_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def save_state(data):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def send_serverchan(title, content):
    key = os.environ.get("SERVERCHAN_KEY")
    if not key:
        return
    url = f"https://sctapi.ftqq.com/{key}.send"
    try:
        r = requests.post(url, data={"title": title, "desp": content}, timeout=15)
        print(f"Server酱推送结果: {r.status_code}")
    except Exception as e:
        print(f"Server酱推送失败: {e}")


def send_telegram(title, content):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = f"*{title}*\n{content}"
    try:
        r = requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
        print(f"Telegram推送结果: {r.status_code}")
    except Exception as e:
        print(f"Telegram推送失败: {e}")


def main():
    try:
        detail_url = get_whatsapp_detail_url()
        version, final_url = get_version_from_detail(detail_url)
    except Exception as e:
        print(f"抓取失败: {e}")
        sys.exit(1)

    state = load_last_state()
    last_version = state.get("version")

    print(f"当前检测到版本: {version}（上次记录: {last_version}）")

    if last_version is None:
        # 第一次运行，只记录，不发通知，避免误报“更新”
        save_state({"version": version, "url": final_url})
        print("首次运行，已记录版本号，不发送通知。")
        return

    if version != last_version:
        title = "WhatsApp 有新版本啦"
        content = (
            f"检测到 WhatsApp 版本更新\n\n"
            f"旧版本：{last_version}\n"
            f"新版本：{version}\n\n"
            f"详情页：{final_url}"
        )
        send_serverchan(title, content)
        send_telegram(title, content)
        save_state({"version": version, "url": final_url})
        print("版本已更新，通知已发送。")
    else:
        print("版本未变化，无需通知。")


if __name__ == "__main__":
    main()
