"""
AI 热点日报 - 飞书自动推送
从 RSS 源抓取最新 AI 新闻，推送到飞书群机器人
"""

import hmac
import hashlib
import base64
import time
import json
import os
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import feedparser
import requests

# ============ 配置 ============

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
FEISHU_SECRET = os.environ.get("FEISHU_SECRET", "")
MAX_NEWS = 8

# RSS 源配置（中文 + 英文混合，覆盖面广）
RSS_SOURCES = [
    {
        "name": "The Verge AI",
        "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "lang": "en",
    },
    {
        "name": "TechCrunch AI",
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "lang": "en",
    },
    {
        "name": "MIT Tech Review",
        "url": "https://www.technologyreview.com/feed/",
        "lang": "en",
    },
    {
        "name": "Ars Technica AI",
        "url": "https://feeds.arstechnica.com/arstechnica/technology-lab",
        "lang": "en",
    },
    {
        "name": "VentureBeat AI",
        "url": "https://venturebeat.com/category/ai/feed/",
        "lang": "en",
    },
]


# ============ 工具函数 ============

def ai_keyword_filter(title, summary=""):
    """过滤 AI 相关的新闻"""
    text = (title + " " + summary).lower()
    keywords = [
        "ai", "artificial intelligence", "machine learning", "deep learning",
        "llm", "gpt", "claude", "gemini", "openai", "anthropic", "copilot",
        "chatbot", "neural", "transformer", "diffusion", "agent",
        "人工智能", "大模型", "机器学习", "深度学习", "智能体",
        "deepseek", "grok", "midjourney", "stable diffusion",
    ]
    return any(kw in text for kw in keywords)


def clean_html(text):
    """去除 HTML 标签"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate(text, max_len=80):
    """截断文本"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# ============ RSS 抓取 ============

def fetch_rss(source, hours=24):
    """抓取单个 RSS 源，返回最近的新闻列表"""
    items = []
    try:
        feed = feedparser.parse(source["url"], request_headers={
            "User-Agent": "Mozilla/5.0 (AI Daily Bot)"
        })
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=hours)

        for entry in feed.entries:
            title = entry.get("title", "").strip()
            if not title:
                continue

            # 过滤 AI 相关
            summary = clean_html(entry.get("summary", ""))
            if not ai_keyword_filter(title, summary):
                continue

            # 时间过滤
            published = entry.get("published_parsed")
            if published:
                pub_time = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_time < cutoff:
                    continue

            link = entry.get("link", "")
            desc = truncate(clean_html(summary), 100)

            items.append({
                "title": title,
                "link": link,
                "desc": desc,
                "source": source["name"],
            })
    except Exception as e:
        print(f"[WARN] Failed to fetch {source['name']}: {e}")

    return items


def fetch_all_news(hours=24):
    """抓取所有 RSS 源，合并去重，按源交替排列"""
    all_items = []

    for source in RSS_SOURCES:
        items = fetch_rss(source, hours)
        all_items.extend(items)
        print(f"[OK] {source['name']}: {len(items)} AI articles")

    # 去重（按标题相似度）
    seen = set()
    unique = []
    for item in all_items:
        key = item["title"][:30].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    print(f"\n[Total] {len(unique)} unique AI articles")
    return unique[:MAX_NEWS]


# ============ 飞书推送 ============

def gen_sign(secret):
    """生成飞书签名"""
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    return timestamp, sign


def build_card(news_list):
    """构建飞书卡片消息"""
    now_cn = datetime.now(timezone(timedelta(hours=8)))
    date_str = now_cn.strftime("%Y-%m-%d")
    weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_map[now_cn.weekday()]

    elements = []

    # 开头说明
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"今日全球 AI 热点速览，共 **{len(news_list)}** 条"
        }
    })
    elements.append({"tag": "hr"})

    # 新闻列表
    for i, item in enumerate(news_list, 1):
        link_md = f'[🔗]({item["link"]})' if item["link"] else ""
        content = f'**{i}. {item["title"]}** {link_md}\n{item["desc"]}  ·  _{item["source"]}_'
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": content}
        })

    # 底部
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [
            {
                "tag": "plain_text",
                "content": f"🤖 AI 热点日报 · {date_str} {weekday} · GitHub Actions 自动推送"
            }
        ]
    })

    card = {
        "title": f"🤖 AI 热点日报 · {date_str} {weekday}",
        "color": "blue",
        "elements": elements,
    }
    return card


def push_to_feishu(card_data):
    """推送到飞书"""
    if not FEISHU_WEBHOOK or not FEISHU_SECRET:
        print("[ERROR] FEISHU_WEBHOOK or FEISHU_SECRET not set")
        return False

    timestamp, sign = gen_sign(FEISHU_SECRET)
    url = f"{FEISHU_WEBHOOK}?timestamp={timestamp}&sign={sign}"

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": card_data["title"]},
                "template": card_data["color"],
            },
            "elements": card_data["elements"],
        },
    }

    resp = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    result = resp.json()
    print(f"[Feishu] {result}")
    return result.get("StatusCode") == 0


def push_fallback(msg):
    """推送错误信息"""
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": msg}},
        {"tag": "hr"},
        {"tag": "note", "elements": [
            {"tag": "plain_text", "content": "🤖 AI 热点日报 · 推送异常通知"}
        ]},
    ]
    card = {"title": "⚠️ AI 日报推送异常", "color": "red", "elements": elements}
    push_to_feishu(card)


# ============ 主流程 ============

def main():
    print("=" * 50)
    print("AI Daily News Push - Starting")
    print("=" * 50)

    news = fetch_all_news(hours=24)

    if not news:
        print("[WARN] No AI news found, pushing fallback message")
        push_fallback("今日暂未抓取到 AI 热点新闻，可能 RSS 源暂时不可用。明天会自动重试。")
        return

    card = build_card(news)
    success = push_to_feishu(card)

    if success:
        print("[DONE] Push successful!")
    else:
        print("[ERROR] Push failed")


if __name__ == "__main__":
    main()
