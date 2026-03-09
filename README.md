# lt-memo-agent

勉強会・LT会のメモを記録・検索・レポート化する Claude サブエージェント。

## 概要

勉強会やLT会に参加したあと、セッション内容をナレッジとして蓄積し、あとから自然言語で検索・参照できるようにします。
AWS Bedrock AgentCore Runtime 上で動作し、Claude Code から自然言語で呼び出せます。

---

## アーキテクチャ

```
Claude Code（ユーザー）
    ↓ 自然言語
lt-memo-agent（サブエージェント: .claude/agents/lt-memo-agent.md）
    ↓ agentcore invoke
AWS Bedrock AgentCore Runtime（ap-northeast-1）
    ↓ Strands Agent + Amazon Nova Pro
各種ツール（add_memo / search_knowledge / ask_knowledge ...）
    ↓ S3 read/write
S3バケット: $LT_MEMO_BUCKET
├── events.json        # イベント一覧
├── knowledge.json     # スピーカー・セッション別ナレッジ
├── memos/             # アップロードしたメモファイル（入力）
└── reports/           # 生成した参加レポート（Markdown）
```

**LLMモデル**: Amazon Nova Pro (`apac.amazon.nova-pro-v1:0`)
**フレームワーク**: [Strands Agents](https://github.com/strands-agents/sdk-python)

---

## 主な機能と使い方

Claude Code 上で日本語で話しかけるだけで動作します。

### 1. メモの追加

メモをその場でテキストとして渡す方法と、シェルスクリプト（`add_memo.sh`）を使う方法の2通りがあります。

#### 方法A：テキストを直接渡す

```
「以下のメモを追加して」

# 〇〇勉強会
date: 2025-06-01

## セッションタイトルA
speaker: 山田さん

- 発表内容のポイント1
- 発表内容のポイント2

## セッションタイトルB
speaker: 田中さん

- 発表内容のポイント1
- 発表内容のポイント2
```

#### 方法B:add_memo.sh を使う（推奨）

`lt-memo/add_memo.sh` を使うと、S3アップロード・プレビュー確認・保存を対話的に一括で行えます。

```bash
cd lt-memo
source .venv/bin/activate

# メモファイルを memos/ に置いてから実行
./add_memo.sh 20250601_study.md
```

実行すると以下の流れで処理されます：

1. S3 にメモファイルをアップロード
2. レポートとナレッジのプレビューを表示
3. 確認後 `y` を入力すると保存、`N` でキャンセル
4. 保存後、レポートを `lt-memo/reports/` にローカルダウンロード

手動で行う場合は以下の手順でも可能です：

```bash
# 1. S3にメモファイルをアップロード
aws s3 cp 20250601_study.md s3://$LT_MEMO_BUCKET/memos/20250601_study.md

# 2. Claude Code でプレビュー確認（保存はしない）
「20250601_study.md をプレビューして」

# 3. 問題なければ保存
「20250601_study.md を処理して保存して」
```

#### メモのフォーマット

```markdown
# イベント名
date: YYYY-MM-DD

## セッションタイトル
speaker: スピーカー名

メモ内容（自由記述）
```

##### 各フィールドの詳細

| フィールド | 書き方 | 必須 | 備考 |
|---|---|---|---|
| イベント名 | `# ` で始まる行 | 必須 | ファイル先頭のH1のみ認識 |
| 開催日 | `date: YYYY-MM-DD` | 省略可 | 省略時は当日の日付 |
| セッション | `## ` で始まる行 | 1件以上必須 | H2ごとに1セッションとして分割 |
| スピーカー名 | `speaker: 名前` | 省略可 | 省略時は自動検出（後述） |
| メモ内容 | それ以外の行 | - | 自由記述。箇条書きも可 |

##### スピーカー名の自動検出ルール

`speaker:` を書かない場合、`## セッションタイトル` 直後の最初の行が以下の条件を満たすと**スピーカー名として自動認識**されます。

- 20文字以下の短い行
- または `（` を含む行（例：`田中さん（株式会社○○）`）

条件を満たさない場合はメモ本文として扱われ、スピーカーは「不明」になります。

##### 効果的なメモの書き方

**精度が上がる書き方**（推奨）

```markdown
# 〇〇勉強会
date: 2025-06-01

## セッションタイトルA
speaker: 山田さん

- 発表の要点を箇条書きで
- キーワードや技術名を具体的に書くと検索精度が上がる
- 印象に残ったポイントや疑問点も記録する

## セッションタイトルB
speaker: 田中さん（株式会社○○）

- 発表の要点を箇条書きで
- 参考リンクやデモの内容も書いておくと後で役立つ
```

**注意点**

- `# イベント名` は1行目付近に1つだけ書く（2つ目以降は無視される）
- `date:` と `speaker:` は `## セッション` より前に書く
- メモ内容の `##` はセッション区切りとして解釈されるので、小見出しに `##` は使わない（`###` 以降は本文として扱われる）
- セッション情報（`## `）が1件もないとエラーになる

#### 追加時に自動生成されるもの

- 参加レポート（Markdown）→ S3 `reports/` に保存
- セッションごとのナレッジエントリ（要約・キーワード・学び・技術スタック）→ `knowledge.json` に追加

---

### 2. ナレッジの検索

#### キーワード検索

`knowledge.json` 全体を対象に全文検索します（タイトル・要約・キーワード・学び・技術スタックを横断）。

```
「○○について検索して」
「Kubernetesが出てきた発表を教えて」
```

#### 自然言語で質問（RAG的な使い方）

蓄積されたナレッジ全体をコンテキストとして LLM に渡し、自由な質問に回答します。

```
「○○の実装方法について教えて」
「最近の勉強会でよく出てきた技術は何？」
「○○さんはどんな発表をしてきた？」
```

---

### 3. その他の操作

| やりたいこと | 呼びかけ例 |
|---|---|
| イベント一覧を確認 | 「登録済みのイベントを一覧で見せて」 |
| 参加レポートを取得 | 「〇〇勉強会のレポートを見せて」 |
| スピーカー一覧を確認 | 「これまで発表したスピーカーを一覧で見せて」 |
| スピーカー検索 | 「山田さんの発表は？」 |

---

## データ構造

### knowledge.json

```json
{
  "speakers": {
    "山田さん": [
      {
        "event_id": "2025-06-01_〇〇勉強会",
        "event_name": "〇〇勉強会",
        "event_date": "2025-06-01",
        "title": "セッションタイトルA",
        "summary": "LLMが自動生成する100文字程度の要約",
        "keywords": ["キーワード1", "キーワード2"],
        "learnings": ["学び1", "学び2"],
        "tech_stack": ["技術1", "技術2"]
      }
    ]
  }
}
```

### events.json

```json
[
  {
    "event_id": "2025-06-01_〇〇勉強会",
    "event_name": "〇〇勉強会",
    "event_date": "2025-06-01",
    "report_path": "s3://YOUR_BUCKET/reports/...",
    "sessions": ["セッションタイトルA", "セッションタイトルB"]
  }
]
```

---

## 動作環境

| 項目 | 値 |
|---|---|
| Runtime | AWS Bedrock AgentCore (ap-northeast-1) |
| LLM | Amazon Nova Pro (`apac.amazon.nova-pro-v1:0`) |
| フレームワーク | Strands Agents |
| データストア | S3（環境変数 `LT_MEMO_BUCKET` で指定） |
| デプロイ | Docker コンテナ (linux/arm64) / ECR |
| 応答言語 | 日本語 |

---

## ディレクトリ構成

```
my-agents/
├── CLAUDE.md
└── lt-memo/
    ├── .bedrock_agentcore.yaml   # AgentCore デプロイ設定
    ├── src/
    │   ├── agentcore_app.py      # エントリーポイント（BedrockAgentCoreApp）
    │   ├── agent.py              # Strands Agent 定義
    │   ├── tools.py              # ツール実装（add_memo, search_knowledge など）
    │   └── knowledge_db.py       # S3 読み書きラッパー（KnowledgeDB）
    └── .bedrock_agentcore/
        └── lt_memo/Dockerfile
```

---

## デプロイ

```bash
cd lt-memo
source .venv/bin/activate

# ローカルテスト
python src/agent.py "イベント一覧を表示して"

# AgentCore にデプロイ
agentcore deploy

# デプロイ済みエージェントを呼び出し
agentcore invoke '{"prompt": "登録済みのイベントを一覧で見せて"}'
```
