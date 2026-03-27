# Daily AI Digest

AIエージェント・ハーネスエンジニアリング関連の情報を毎日自動収集し、日本語レポートを生成するワークフロー。

## LLMプロバイダー

| プロバイダー | モデル | コスト | Web検索 |
|-------------|--------|--------|---------|
| **Gemini (デフォルト)** | 2.5 Flash | **無料** (1000 RPD) | Grounding with Google Search |
| Claude | Sonnet 4.6 | ~$4/月 | web_search tool |

`scripts/config.yaml` の `llm.provider` で切り替え:

```yaml
llm:
  provider: "gemini"   # ← "claude" に変えるだけ
```

## 収集テーマ

- **Harness Engineering** — エージェントハーネス設計、コンテキストエンジニアリング
- **AI Agent アーキテクチャ** — 自律コーディングエージェント、マルチエージェント
- **MCP × Agent Skills** — Model Context Protocol、Agent Skills設計

## 収集ソース

| ソース | 方法 | 対象 |
|--------|------|------|
| GitHub | GitHub API | リポジトリ検索、トピック、リリース |
| 技術ブログ | RSS | Zenn, Qiita, Anthropic, OpenAI, Martin Fowler 等 |
| arXiv | arXiv API | AI agent / harness 関連論文 |
| Web全般 | LLM経由 (Google Search / web_search) | X, Medium, 個人ブログ等 |

## セットアップ

### 1. リポジトリを作成

```bash
gh repo create daily-ai-digest --private --clone
cd daily-ai-digest
# ダウンロードしたファイルをここにコピー
git add -A && git commit -m "初期セットアップ" && git push
```

### 2. Gemini API キーを取得（無料）

1. https://aistudio.google.com にアクセス
2. 「Get API key」→ APIキーを作成
3. クレジットカード不要、無料枠で十分

### 3. Secrets を設定

GitHub リポジトリの Settings → Secrets → Actions:

| Secret | 必須 | 説明 |
|--------|------|------|
| `GEMINI_API_KEY` | ✅ (Gemini使用時) | Google AI Studio の API キー |
| `ANTHROPIC_API_KEY` | ❌ (Claude使用時のみ) | Claude API キー |

`GITHUB_TOKEN` は自動で提供されます。

### 4. 動作確認

```bash
# ローカルで実行
export GEMINI_API_KEY="AIza..."
python scripts/collect.py

# GitHub Actions で手動実行
gh workflow run daily-collect.yml
```

## 出力

```
reports/
  2026/
    03/
      2026-03-27.md       # 日本語レポート
      2026-03-27-raw.json # 生データ
```

## カスタマイズ

`scripts/config.yaml` を編集:

- **プロバイダー切替**: `llm.provider` を `"gemini"` or `"claude"` に
- **テーマ追加**: `themes` にキーワードを追加
- **RSS追加**: `rss.feeds` にフィードURLを追加
- **監視リポジトリ追加**: `github.watch_repos` にリポジトリを追加
- **Web検索クエリ追加**: `web_search.queries` にクエリを追加

## コスト

| プロバイダー | 日次API使用量 | 月額 |
|-------------|--------------|------|
| Gemini 2.5 Flash (無料枠) | ~10リクエスト/日 | **¥0** |
| Gemini 2.5 Flash (有料) | ~24K tokens/日 | ~¥60 |
| Claude Sonnet 4.6 | ~24K tokens/日 | ~¥500 |

## ライセンス

MIT
