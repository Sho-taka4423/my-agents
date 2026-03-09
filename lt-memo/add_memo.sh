#!/bin/bash
# 使い方: ./add_memo.sh 20260601_勉強会.md
set -e

FILE="$1"
BUCKET="${LT_MEMO_BUCKET:?環境変数 LT_MEMO_BUCKET が設定されていません}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -z "$FILE" ]; then
  echo "使い方: ./add_memo.sh <ファイル名>"
  echo "例:     ./add_memo.sh 20260601_勉強会.md"
  exit 1
fi

LOCAL_PATH="$SCRIPT_DIR/memos/$FILE"

if [ ! -f "$LOCAL_PATH" ]; then
  echo "エラー: $LOCAL_PATH が見つかりません"
  exit 1
fi

echo "S3 にアップロード中..."
aws s3 cp "$LOCAL_PATH" "s3://$BUCKET/memos/$FILE"

cd "$SCRIPT_DIR"

echo ""
echo "レポートとナレッジをプレビュー生成中..."
.venv/bin/agentcore invoke "{\"prompt\": \"${FILE}のプレビューを見せて\"}" < /dev/null

echo ""
read -p "このレポートとナレッジで保存しますか？ (y/N): " CONFIRM </dev/tty
if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
  echo "キャンセルしました。S3 のメモファイルはそのまま残っています。"
  exit 0
fi

echo ""
echo "AgentCore でメモを保存中..."
.venv/bin/agentcore invoke "{\"prompt\": \"${FILE}を処理して保存して\"}" < /dev/null

echo ""
echo "レポートをローカルに保存中..."
mkdir -p "$SCRIPT_DIR/reports"
aws s3 sync "s3://$BUCKET/reports/" "$SCRIPT_DIR/reports/" --quiet
echo "reports/ に保存しました"
