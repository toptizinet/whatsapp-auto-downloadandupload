#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
监测 Uptodown 上 WhatsApp 的版本更新。
逻辑：
1. 访问搜索页，找到 WhatsApp 的详情页链接（详情页比搜索页结构更稳定）
2. 从详情页提取当前版本号
3. 和上次记录的版本号（存在 last_version.json 里）对比
4. 如果不同：
   a. 用 Playwright 模拟浏览器，从下载页拿到真实 APK 文件，存为 whatsapp.apk
   b. 上传到 Cloudflare R2 存储桶（会覆盖同名旧文件）
   c. 通过 Server酱 / Telegram 发送通知
   d. 更新记录
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import re
import sys

SEARCH_URL = "https://cn.uptodown.com/android/search?query=whatsapp"
STATE_FILE = "last_version.json"
DOWNLOAD_DIR = "downloads"
APK_FILENAME = "whatsapp.apk"
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
        if re.search(r"whatsapp[\w\-\.]*uptodown\.com", href) or re.search(
            r"/android/whatsapp(?:-messenger)?/?$", href
        ):
            scored.append(href)

    if scored:
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

    version_tag = soup.select_one("div.version") or soup.select_one("span.version")
    if version_tag and version_tag.get_text(strip=True):
        return version_tag.get_text(strip=True), url

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


def download_apk(detail_url):
    """
    用 Playwright 打开下载页，等倒计时结束后点击下载按钮，
    抓到真实 APK 文件并存为 whatsapp.apk。
    Uptodown 下载页地址通常是详情页地址 + /download。
    """
    from playwright.sync_api import sync_playwright

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, APK_FILENAME)
    download_page_url = detail_url.rstrip("/") + "/download"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        page.goto(download_page_url, timeout=60000, wait_until="domcontentloaded")

        # Uptodown 下载按钮常见 id 是 detail-download-button，
        # 倒计时结束前是 disabled 状态，结束后才能点击。
        try:
            page.wait_for_selector(
                "#detail-download-button:not([disabled])", timeout=45000
            )
            button_selector = "#detail-download-button"
        except Exception:
            # 兜底：找页面上文案包含"下载"或 download 的可点击按钮/链接
            button_selector = "a:has-text('下载'), button:has-text('下载'), a:has-text('Download')"
            page.wait_for_selector(button_selector, timeout=15000)

        with page.expect_download(timeout=90000) as download_info:
            page.click(button_selector)
        download = download_info.value
        download.save_as(file_path)
        browser.close()

    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        raise RuntimeError("APK 下载失败或文件为空，下载页结构可能已发生变化。")

    return file_path


def upload_to_r2(file_path):
    """上传到 Cloudflare R2（S3 兼容协议），覆盖同名旧文件"""
    import boto3
    from botocore.config import Config

    account_id = os.environ["R2_ACCOUNT_ID"]
    access_key = os.environ["R2_ACCESS_KEY_ID"]
    secret_key = os.environ["R2_SECRET_ACCESS_KEY"]
    bucket = os.environ["R2_BUCKET_NAME"]

    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )

    s3.upload_file(
        file_path,
        bucket,
        APK_FILENAME,
        ExtraArgs={"ContentType": "application/vnd.android.package-archive"},
    )

    public_base = os.environ.get("R2_PUBLIC_URL")
    if public_base:
        return public_base.rstrip("/") + "/" + APK_FILENAME
    return None


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
        save_state({"version": version, "url": final_url})
        print("首次运行，已记录版本号，不发送通知，不下载。")
        return

    if version == last_version:
        print("版本未变化，无需通知。")
        return

    print("检测到新版本，开始下载 APK ...")
    try:
        apk_path = download_apk(final_url)
        print(f"下载完成: {apk_path}")
    except Exception as e:
        print(f"下载 APK 失败: {e}")
        title = "WhatsApp 有新版本（自动下载失败，需手动处理）"
        content = (
            f"新版本：{version}\n旧版本：{last_version}\n\n"
            f"自动下载出错：{e}\n\n"
            f"详情页：{final_url}"
        )
        send_serverchan(title, content)
        send_telegram(title, content)
        sys.exit(1)

    print("开始上传到 Cloudflare R2 ...")
    try:
        public_url = upload_to_r2(apk_path)
        print(f"上传完成。公开访问地址: {public_url or '(未配置 R2_PUBLIC_URL，仅上传到桶内)'}")
    except Exception as e:
        print(f"上传到 R2 失败: {e}")
        title = "WhatsApp 有新版本（下载成功但上传 R2 失败）"
        content = (
            f"新版本：{version}\n旧版本：{last_version}\n\n"
            f"上传 R2 出错：{e}\n\n"
            f"详情页：{final_url}"
        )
        send_serverchan(title, content)
        send_telegram(title, content)
        sys.exit(1)

    title = "WhatsApp 有新版本啦"
    content = (
        f"检测到 WhatsApp 版本更新\n\n"
        f"旧版本：{last_version}\n"
        f"新版本：{version}\n\n"
        f"详情页：{final_url}\n"
    )
    if public_url:
        content += f"下载地址：{public_url}"

    send_serverchan(title, content)
    send_telegram(title, content)
    save_state({"version": version, "url": final_url})
    print("全部流程完成，通知已发送。")


if __name__ == "__main__":
    main()
