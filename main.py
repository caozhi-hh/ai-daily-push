"""
AI 热点日报 - LangChain Agent 版
使用 LangChain + DuckDuckGo 搜索全球 AI 热点，AI 生成中文摘要，推送到飞书
"""

import os
import sys
import hmac
import hashlib
import base64
import time
import json
import re
from datetime import datetime, timezone, timedelta

import requests
import feedparser
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

# ============ 配置 ============

QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
QWEN_API_BASE = os.environ.get("QWEN_API_BASE", "https://open.bigmodel.cn/api/paas/v4/")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "glm-4-flash")

FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
FEISHU_SECRET = os.environ.get("FEISHU_SECRET", "")

RSS_SOURCES = [
    {"name": "The Verge AI", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/feed/"},
    {"name": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/technology-lab"},
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/"},
]


# ============ Tool 1: DuckDuckGo 搜索 ============

@tool
def search_ai_news(query: str) -> str:
    """Search the web for the latest AI news. Use English queries for best results.
    Examples: "latest AI news today", "OpenAI announcement", "AI breakthrough 2026"

    Args:
        query: Search query string.

    Returns:
        JSON string with search results containing title, url, and snippet.
    """
    try:
        from duckduckgo_search import DDGS
        results = DDGS().text(query, max_results=8)
        items = []
        for r in results:
            items.append({
                "title": r.get("title", ""),
                "link": r.get("href", ""),
                "snippet": r.get("body", ""),
            })
        print(f"[Search] '{query}': {len(items)} results")
        return json.dumps(items, ensure_ascii=False)
    except Exception as e:
        print(f"[Search ERROR] {e}")
        return json.dumps({"error": str(e)})


# ============ Tool 2: RSS 抓取（备用源）============

@tool
def fetch_rss_news() -> str:
    """Fetch AI news from RSS feeds as a supplementary source.
    Returns articles from The Verge, TechCrunch, MIT Tech Review, Ars Technica, VentureBeat.

    Returns:
        JSON string with RSS articles containing title, link, desc, and source.
    """
    ai_keywords = [
        "ai", "artificial intelligence", "machine learning", "deep learning",
        "llm", "gpt", "claude", "gemini", "openai", "anthropic", "copilot",
        "chatbot", "neural", "transformer", "diffusion", "agent",
        "人工智能", "大模型", "机器学习", "智能体", "deepseek", "grok",
    ]

    def is_ai(text):
        t = text.lower()
        return any(kw in t for kw in ai_keywords)

    def clean(text):
        if not text:
            return ""
        return re.sub(r"<[^>]+>", "", re.sub(r"\s+", " ", text)).strip()

    all_items = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(source["url"], request_headers={
                "User-Agent": "Mozilla/5.0 (AI Daily Bot)"
            })
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                summary = clean(entry.get("summary", ""))
                if not is_ai(title + " " + summary):
                    continue
                published = entry.get("published_parsed")
                if published:
                    pub_time = datetime(*published[:6], tzinfo=timezone.utc)
                    if pub_time < cutoff:
                        continue
                all_items.append({
                    "title": title,
                    "link": entry.get("link", ""),
                    "desc": summary[:100],
                    "source": source["name"],
                })
            print(f"[RSS] {source['name']}: {len([i for i in all_items if i['source'] == source['name']])} articles")
        except Exception as e:
            print(f"[RSS WARN] {source['name']}: {e}")

    # Deduplicate
    seen = set()
    unique = []
    for item in all_items:
        key = item["title"][:30].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    print(f"[RSS Total] {len(unique)} unique articles")
    return json.dumps(unique[:15], ensure_ascii=False)


# ============ Tool 3: 飞书推送 ============

@tool
def push_to_feishu(card_json: str) -> str:
    """Push a formatted news card to Feishu group chat via webhook.
    This is the final step - call this only after gathering and organizing all news.

    Args:
        card_json: JSON string with the following structure:
            {
                "title": "AI 热点日报 · 2026-05-10 周六",
                "news_items": [
                    {
                        "title": "中文标题",
                        "summary": "2-3句中文摘要",
                        "link": "https://...",
                        "source": "来源名"
                    }
                ]
            }

    Returns:
        Success or error message string.
    """
    try:
        data = json.loads(card_json)
    except json.JSONDecodeError as e:
        return f"JSON parse error: {e}. Please provide valid JSON."

    news_items = data.get("news_items", [])
    title = data.get("title", "AI 热点日报")

    if not news_items:
        return "No news items provided. Please search for news first."

    # Build Feishu card
    elements = []
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"今日全球 AI 热点速览，共 **{len(news_items)}** 条"}
    })
    elements.append({"tag": "hr"})

    for i, item in enumerate(news_items, 1):
        link_md = f'[🔗]({item.get("link", "")})' if item.get("link") else ""
        source = item.get("source", "")
        source_md = f'  ·  _{source}_' if source else ""
        content = f'**{i}. {item["title"]}** {link_md}\n{item.get("summary", "")}{source_md}'
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": content}
        })

    now_cn = datetime.now(timezone(timedelta(hours=8)))
    date_str = now_cn.strftime("%Y-%m-%d")
    weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_map[now_cn.weekday()]

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "note",
        "elements": [{
            "tag": "plain_text",
            "content": f"AI 热点日报 · {date_str} {weekday} · LangChain Agent 自动推送"
        }]
    })

    # Sign and push
    if not FEISHU_WEBHOOK or not FEISHU_SECRET:
        return "Error: FEISHU_WEBHOOK or FEISHU_SECRET not configured."

    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{FEISHU_SECRET}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    url = f"{FEISHU_WEBHOOK}?timestamp={timestamp}&sign={sign}"

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": elements,
        },
    }

    resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
    result = resp.json()
    print(f"[Feishu] {result}")

    if result.get("StatusCode") == 0:
        return f"Successfully pushed {len(news_items)} news items to Feishu!"
    return f"Feishu push failed: {result}"


# ============ Agent 构建 ============

SYSTEM_PROMPT = """你是一个 AI 新闻助手，负责每日搜索全球 AI 热点并推送到飞书。

你的工作流程：
1. 使用 search_ai_news 搜索今天全球最新的 AI 新闻（建议搜索 2-3 个不同关键词）
   - 推荐搜索词："latest AI news today 2026"、"AI breakthrough"、"artificial intelligence update"
2. 使用 fetch_rss_news 获取 RSS 源的新闻作为补充
3. 综合两个来源，挑选最重要的 8 条新闻（去重、按重要性排序）
4. 为每条新闻：
   - 标题翻译成中文（保留英文关键术语如 GPT、Claude、OpenAI 等）
   - 写一段 2-3 句话的中文摘要，突出关键信息
5. 使用 push_to_feishu 推送，参数格式：
   {"title": "AI 热点日报 · 日期 星期", "news_items": [{"title": "中文标题", "summary": "中文摘要", "link": "url", "source": "来源"}]}

要求：
- 必须实际调用搜索工具获取新闻，不要编造
- 中英文来源都要覆盖
- 按重要性和热度排序
- 摘要简洁明了，突出关键信息
- 推送是最后一步，确保新闻整理好后再调用
"""


def main():
    print("=" * 50)
    print(f"AI Daily News Agent - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    if not QWEN_API_KEY:
        print("[ERROR] QWEN_API_KEY not set")
        return
    if not FEISHU_WEBHOOK or not FEISHU_SECRET:
        print("[ERROR] FEISHU_WEBHOOK or FEISHU_SECRET not set")
        return

    llm = ChatOpenAI(
        model=QWEN_MODEL,
        api_key=QWEN_API_KEY,
        base_url=QWEN_API_BASE,
        temperature=0,
    )

    tools = [search_ai_news, fetch_rss_news, push_to_feishu]

    agent = create_react_agent(model=llm, tools=tools, prompt=SYSTEM_PROMPT)

    print("\n[Agent] Starting search and push...")
    result = agent.invoke({
        "messages": [
            {"role": "user", "content": "请搜索今天全球 AI 领域的最新热点新闻，整理成中文摘要，然后推送到飞书。"}
        ]
    })

    last_message = result["messages"][-1]
    print(f"\n[Agent Response] {last_message.content}")
    print("[DONE]")


if __name__ == "__main__":
    main()
