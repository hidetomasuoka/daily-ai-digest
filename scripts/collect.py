#!/usr/bin/env python3
"""
Daily AI Digest - 自動情報収集 & レポート生成
GitHub API, RSS, arXiv, Web検索から情報を収集し、
Gemini or Claude APIで日本語要約レポートを生成する。

プロバイダー切り替え: config.yaml の llm.provider を "gemini" or "claude" に設定
"""

import os
import sys
import json
import yaml
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ssl
import re
import time

# ============================================================
# 設定
# ============================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
PROJECT_ROOT = SCRIPT_DIR.parent

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

JST = timezone(timedelta(hours=9))

_ssl_ctx = ssl.create_default_context()


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_provider(config: dict) -> str:
    return config.get("llm", {}).get("provider", "gemini")


def get_api_key(config: dict) -> str:
    provider = get_provider(config)
    if provider == "gemini":
        if not GEMINI_API_KEY:
            print("[ERROR] GEMINI_API_KEY not set", file=sys.stderr)
            sys.exit(1)
        return GEMINI_API_KEY
    else:
        if not ANTHROPIC_API_KEY:
            print("[ERROR] ANTHROPIC_API_KEY not set", file=sys.stderr)
            sys.exit(1)
        return ANTHROPIC_API_KEY


# ============================================================
# HTTP ヘルパー
# ============================================================

def http_get(url: str, headers: dict = None, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"  [WARN] HTTP GET failed: {url} -> {e}", file=sys.stderr)
        return ""


def http_post_json(url: str, body: dict, headers: dict = None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"  [ERROR] HTTP POST failed: {url} -> {e}", file=sys.stderr)
        return {}


# ============================================================
# LLM プロバイダー抽象化
# ============================================================

class LLMProvider:
    """Gemini / Claude を切り替えるための抽象レイヤー"""

    def __init__(self, config: dict):
        self.provider = get_provider(config)
        self.config = config
        self.api_key = get_api_key(config)

        if self.provider == "gemini":
            self.model = config.get("llm", {}).get("gemini", {}).get("model", "gemini-2.5-flash")
        else:
            self.model = config.get("llm", {}).get("claude", {}).get("model", "claude-sonnet-4-20250514")

        print(f"  LLM Provider: {self.provider} ({self.model})")

    def web_search(self, query: str) -> list[dict]:
        """Web検索を実行して結果を返す"""
        if self.provider == "gemini":
            return self._gemini_search(query)
        else:
            return self._claude_search(query)

    def generate_text(self, prompt: str, max_tokens: int = 4000) -> str:
        """テキスト生成（Web検索なし）"""
        if self.provider == "gemini":
            return self._gemini_generate(prompt, max_tokens, use_search=False)
        else:
            return self._claude_generate(prompt, max_tokens, use_search=False)

    # ─── Gemini ─────────────────────────────────────────

    def _gemini_search(self, query: str) -> list[dict]:
        """Gemini + Grounding with Google Search"""
        prompt = (
            f"以下のクエリに関する過去24時間以内の新しい記事・投稿を探してください。\n"
            f"クエリ: {query}\n\n"
            f"見つかった各記事について、以下のJSON形式で返してください。\n"
            f"記事が見つからない場合は空の配列を返してください。\n\n"
            f'{{"items": [{{"title": "記事タイトル", "url": "URL", "description": "50文字程度の概要", "source_type": "X/Medium/Blog等"}}]}}\n\n'
            f"JSON以外のテキストは出力しないでください。"
        )

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            f"?key={self.api_key}"
        )
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": 1500},
        }

        resp = http_post_json(url, body, timeout=90)
        return self._parse_gemini_search_items(resp)

    def _gemini_generate(self, prompt: str, max_tokens: int, use_search: bool = False) -> str:
        """Gemini テキスト生成"""
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            f"?key={self.api_key}"
        )
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if use_search:
            body["tools"] = [{"google_search": {}}]

        resp = http_post_json(url, body, timeout=120)
        return self._extract_gemini_text(resp)

    def _parse_gemini_search_items(self, resp: dict) -> list[dict]:
        """Geminiレスポンスから検索アイテムをパース"""
        items = []

        # 1) groundingMetadata からソースを抽出（構造化データ）
        candidates = resp.get("candidates", [])
        for candidate in candidates:
            metadata = candidate.get("groundingMetadata", {})
            for chunk in metadata.get("groundingChunks", []):
                web = chunk.get("web", {})
                if web.get("uri"):
                    items.append({
                        "source": f"Web (Google Search)",
                        "type": "article",
                        "title": web.get("title", ""),
                        "url": web.get("uri", ""),
                        "description": "",
                    })

        # 2) テキスト部分にJSONが含まれていればパース
        text = self._extract_gemini_text(resp)
        if text:
            try:
                text_clean = re.sub(r"```json\s*", "", text)
                text_clean = re.sub(r"```\s*$", "", text_clean)
                parsed = json.loads(text_clean)
                for item in parsed.get("items", []):
                    items.append({
                        "source": f"Web ({item.get('source_type', 'Web')})",
                        "type": "article",
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "description": item.get("description", ""),
                    })
            except (json.JSONDecodeError, AttributeError):
                pass

        return items

    def _extract_gemini_text(self, resp: dict) -> str:
        """Geminiレスポンスからテキストを抽出"""
        for candidate in resp.get("candidates", []):
            content = candidate.get("content", {})
            for part in content.get("parts", []):
                if "text" in part:
                    return part["text"]
        return ""

    # ─── Claude ─────────────────────────────────────────

    def _claude_search(self, query: str) -> list[dict]:
        """Claude API + web_search tool"""
        prompt = (
            f"以下のクエリでWeb検索し、過去24時間以内の新しい記事・投稿を探してください。\n"
            f"クエリ: {query}\n\n"
            f"見つかった各記事について、以下のJSON形式で返してください。\n"
            f"記事が見つからない場合は空の配列を返してください。\n\n"
            f'{{"items": [{{"title": "...", "url": "...", "description": "50文字程度の概要", "source_type": "X/Medium/Blog等"}}]}}\n\n'
            f"JSON以外のテキストは出力しないでください。"
        )

        resp = http_post_json(
            "https://api.anthropic.com/v1/messages",
            body={
                "model": self.model,
                "max_tokens": 1500,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": prompt}],
            },
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=90,
        )

        items = []
        for block in resp.get("content", []):
            if block.get("type") != "text":
                continue
            text = block["text"].strip()
            try:
                text = re.sub(r"```json\s*", "", text)
                text = re.sub(r"```\s*$", "", text)
                parsed = json.loads(text)
                for item in parsed.get("items", []):
                    items.append({
                        "source": f"Web ({item.get('source_type', 'Web')})",
                        "type": "article",
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "description": item.get("description", ""),
                    })
            except (json.JSONDecodeError, AttributeError):
                continue
        return items

    def _claude_generate(self, prompt: str, max_tokens: int, use_search: bool = False) -> str:
        """Claude テキスト生成"""
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if use_search:
            body["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]

        resp = http_post_json(
            "https://api.anthropic.com/v1/messages",
            body=body,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=120,
        )

        for block in resp.get("content", []):
            if block.get("type") == "text":
                return block["text"]
        return ""


# ============================================================
# 1. GitHub 収集
# ============================================================

def collect_github(config: dict) -> list[dict]:
    gh = config.get("github", {})
    days_back = gh.get("days_back", 1)
    min_stars = gh.get("min_stars", 0)
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = []

    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    for query in gh.get("search_queries", []):
        # stars:>=N でAPIレベルでフィルタ
        stars_filter = f" stars:>={min_stars}" if min_stars > 0 else ""
        q = urllib.parse.quote(f"{query} pushed:>{since[:10]}{stars_filter}")
        url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=10"
        body = http_get(url, headers)
        if not body:
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        for repo in data.get("items", [])[:5]:
            star_count = repo.get("stargazers_count", 0)
            # 念のためクライアント側でも再フィルタ
            if star_count < min_stars:
                continue
            items.append({
                "source": "GitHub",
                "type": "repository",
                "title": repo.get("full_name", ""),
                "url": repo.get("html_url", ""),
                "description": repo.get("description", "") or "",
                "stars": star_count,
                "updated": repo.get("updated_at", ""),
                "language": repo.get("language", ""),
                "topics": repo.get("topics", []),
            })

    for repo_name in gh.get("watch_repos", []):
        url = f"https://api.github.com/repos/{repo_name}/releases?per_page=3"
        body = http_get(url, headers)
        if not body:
            continue
        try:
            releases = json.loads(body)
        except json.JSONDecodeError:
            continue
        for rel in releases:
            published = rel.get("published_at", "")
            if published and published >= since:
                items.append({
                    "source": "GitHub",
                    "type": "release",
                    "title": f"{repo_name} - {rel.get('name', rel.get('tag_name', ''))}",
                    "url": rel.get("html_url", ""),
                    "description": (rel.get("body", "") or "")[:500],
                    "updated": published,
                })

    seen = set()
    unique = []
    for item in items:
        key = item.get("url", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    print(f"  GitHub: {len(unique)} items collected (min_stars: {min_stars})")
    return unique


# ============================================================
# 2. RSS 収集
# ============================================================

def parse_rss_date(date_str: str) -> datetime | None:
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def collect_rss(config: dict) -> list[dict]:
    rss_cfg = config.get("rss", {})
    days_back = rss_cfg.get("days_back", 1)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    filter_kws = [kw.lower() for kw in rss_cfg.get("filter_keywords", [])]
    items = []

    for feed_info in rss_cfg.get("feeds", []):
        url = feed_info["url"]
        feed_name = feed_info.get("name", url)
        body = http_get(url)
        if not body:
            continue

        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            print(f"  [WARN] RSS parse failed: {feed_name}", file=sys.stderr)
            continue

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//atom:entry", ns)

        for entry in entries:
            title_el = entry.find("title") or entry.find("atom:title", ns)
            title = title_el.text if title_el is not None and title_el.text else ""

            link_el = entry.find("link") or entry.find("atom:link", ns)
            if link_el is not None:
                link = link_el.get("href") or (link_el.text if link_el.text else "")
            else:
                link = ""

            date_el = (
                entry.find("pubDate")
                or entry.find("atom:published", ns)
                or entry.find("atom:updated", ns)
            )
            pub_date = None
            if date_el is not None and date_el.text:
                pub_date = parse_rss_date(date_el.text)

            if pub_date and pub_date < cutoff:
                continue

            desc_el = entry.find("description") or entry.find("atom:summary", ns)
            desc = ""
            if desc_el is not None and desc_el.text:
                desc = re.sub(r"<[^>]+>", "", desc_el.text)[:300]

            text_lower = (title + " " + desc).lower()
            if filter_kws and not any(kw in text_lower for kw in filter_kws):
                continue

            items.append({
                "source": f"RSS ({feed_name})",
                "type": "article",
                "title": title,
                "url": link.strip(),
                "description": desc,
                "updated": pub_date.isoformat() if pub_date else "",
                "lang": feed_info.get("lang", "en"),
            })

    seen = set()
    unique = []
    for item in items:
        key = item.get("url", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    print(f"  RSS: {len(unique)} items collected")
    return unique


# ============================================================
# 3. arXiv 収集
# ============================================================

def collect_arxiv(config: dict) -> list[dict]:
    arxiv_cfg = config.get("arxiv", {})
    max_per_q = arxiv_cfg.get("max_results_per_query", 5)
    days_back = arxiv_cfg.get("days_back", 3)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    items = []

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for query in arxiv_cfg.get("queries", []):
        q = urllib.parse.quote(f'all:"{query}"')
        url = (
            f"http://export.arxiv.org/api/query?search_query={q}"
            f"&sortBy=submittedDate&sortOrder=descending&max_results={max_per_q}"
        )
        body = http_get(url, timeout=15)
        if not body:
            continue

        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            continue

        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""

            published_el = entry.find("atom:published", ns)
            published = ""
            if published_el is not None and published_el.text:
                pub_dt = parse_rss_date(published_el.text)
                if pub_dt and pub_dt < cutoff:
                    continue
                published = published_el.text

            link = ""
            for link_el in entry.findall("atom:link", ns):
                if link_el.get("type") == "text/html":
                    link = link_el.get("href", "")
                    break
            if not link:
                id_el = entry.find("atom:id", ns)
                link = id_el.text if id_el is not None and id_el.text else ""

            summary_el = entry.find("atom:summary", ns)
            summary = summary_el.text.strip()[:400] if summary_el is not None and summary_el.text else ""

            authors = []
            for author_el in entry.findall("atom:author", ns):
                name_el = author_el.find("atom:name", ns)
                if name_el is not None and name_el.text:
                    authors.append(name_el.text)

            items.append({
                "source": "arXiv",
                "type": "paper",
                "title": title,
                "url": link,
                "description": summary,
                "authors": ", ".join(authors[:3]),
                "updated": published,
            })

    seen = set()
    unique = []
    for item in items:
        key = item.get("url", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    print(f"  arXiv: {len(unique)} items collected")
    return unique


# ============================================================
# 4. Web検索（LLMプロバイダー経由）
# ============================================================

def collect_web_search(config: dict, llm: LLMProvider) -> list[dict]:
    """LLMプロバイダーのWeb検索機能を使って X/Medium/個人ブログ等を収集"""
    web_cfg = config.get("web_search", {})
    queries = web_cfg.get("queries", [])
    items = []

    for i, query in enumerate(queries):
        print(f"    [{i+1}/{len(queries)}] {query[:50]}...")
        results = llm.web_search(query)
        items.extend(results)
        # Rate limit 対策: Gemini Free = 15RPM なので余裕を持つ
        time.sleep(2)

    # 重複除去
    seen = set()
    unique = []
    for item in items:
        key = item.get("url", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    print(f"  Web: {len(unique)} items collected")
    return unique


# ============================================================
# 5. レポート生成
# ============================================================

REPORT_PROMPT_TEMPLATE = """以下は{today}に自動収集されたAIエージェント・ハーネスエンジニアリング関連の情報です。
これを日本語のデイリーダイジェストレポート（Markdown形式）にまとめてください。

## レポートの構成

1. **本日のハイライト** - 最も重要な3〜5件をピックアップし、なぜ重要かを1〜2文で説明
2. **Harness Engineering** - ハーネス設計に関する新着情報
3. **AI Agent アーキテクチャ** - エージェント設計・オーケストレーション関連
4. **MCP × Agent Skills** - Model Context Protocol関連
5. **論文・学術** - arXivの新着論文（あれば）
6. **その他注目** - 上記カテゴリに入らないが注目すべき情報

## ルール
- 英語の記事は日本語で要約すること
- 各項目は タイトル(リンク付き) + 1〜3文の要約 の形式
- 情報がないセクションは「新着なし」と記載
- レポートの冒頭に日付と収集件数のサマリーを入れる
- Markdownのフロントマターは不要

## 収集データ
{items_text}
"""


def generate_report(all_items: list[dict], config: dict, llm: LLMProvider) -> str:
    today = datetime.now(JST).strftime("%Y年%m月%d日")
    items_text = json.dumps(all_items, ensure_ascii=False, indent=2)

    # トークン節約
    if len(items_text) > 30000:
        items_text = items_text[:30000] + "\n... (truncated)"

    prompt = REPORT_PROMPT_TEMPLATE.format(today=today, items_text=items_text)
    result = llm.generate_text(prompt, max_tokens=4000)

    if not result:
        return generate_raw_report(all_items)

    return result


def generate_raw_report(items: list[dict]) -> str:
    """フォールバック（生データ一覧）"""
    today = datetime.now(JST).strftime("%Y年%m月%d日")
    lines = [
        f"# Daily AI Digest - {today}",
        f"\n> 収集件数: {len(items)} 件（LLM API未使用・生データ）\n",
    ]
    for item in items:
        title = item.get("title", "(no title)")
        url = item.get("url", "")
        desc = item.get("description", "")
        source = item.get("source", "")
        lines.append(f"### [{title}]({url})")
        lines.append(f"- **Source:** {source}")
        if desc:
            lines.append(f"- {desc[:200]}")
        lines.append("")
    return "\n".join(lines)


# ============================================================
# メイン
# ============================================================

def main():
    print("=" * 60)
    print(f"Daily AI Digest - {datetime.now(JST).strftime('%Y-%m-%d %H:%M JST')}")
    print("=" * 60)

    config = load_config()

    # LLMプロバイダー初期化
    print("\n[0/5] Initializing LLM provider...")
    llm = LLMProvider(config)

    all_items = []

    # 1. GitHub
    print("\n[1/4] Collecting from GitHub...")
    all_items.extend(collect_github(config))

    # 2. RSS
    print("\n[2/4] Collecting from RSS feeds...")
    all_items.extend(collect_rss(config))

    # 3. arXiv
    print("\n[3/4] Collecting from arXiv...")
    all_items.extend(collect_arxiv(config))

    # 4. Web検索
    print("\n[4/4] Collecting from Web (via LLM)...")
    all_items.extend(collect_web_search(config, llm))

    print(f"\n--- Total: {len(all_items)} items collected ---")

    if not all_items:
        print("No items found. Skipping report generation.")
        sys.exit(0)

    # レポート生成
    print("\nGenerating report...")
    report = generate_report(all_items, config, llm)

    # ファイル出力
    now = datetime.now(JST)
    report_dir = PROJECT_ROOT / config.get("report", {}).get("output_dir", "reports")
    year_month_dir = report_dir / now.strftime("%Y") / now.strftime("%m")
    year_month_dir.mkdir(parents=True, exist_ok=True)

    report_path = year_month_dir / f"{now.strftime('%Y-%m-%d')}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport saved: {report_path}")

    # 生データ保存
    raw_path = year_month_dir / f"{now.strftime('%Y-%m-%d')}-raw.json"
    raw_path.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Raw data saved: {raw_path}")

    print("\nDone!")


if __name__ == "__main__":
    main()
