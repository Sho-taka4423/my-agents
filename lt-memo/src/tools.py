import json
import re
from datetime import datetime

import boto3
from strands import tool

from knowledge_db import KnowledgeDB

db = KnowledgeDB()

MODEL_ID = "apac.amazon.nova-pro-v1:0"
REGION = "ap-northeast-1"


def _invoke_bedrock(prompt: str, max_tokens: int = 1024) -> str:
    client = boto3.client("bedrock-runtime", region_name=REGION)
    response = client.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens},
    )
    return response["output"]["message"]["content"][0]["text"]


def _parse_memo(memo_content: str) -> dict:
    """
    以下のフォーマットのメモを解析してイベント情報とセッション一覧を返す。

    # イベント名
    date: YYYY-MM-DD

    ## セッションタイトル
    speaker: スピーカー名

    メモ内容（自由記述）
    """
    lines = memo_content.strip().splitlines()
    event_name = ""
    event_date = ""
    sessions = []
    current_session = None
    current_memo_lines = []

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("# ") and not stripped.startswith("## ") and not event_name:
            event_name = stripped[2:].strip()

        elif stripped.lower().startswith("date:"):
            event_date = stripped.split(":", 1)[1].strip()

        elif stripped.startswith("## "):
            if current_session is not None:
                current_session["memo"] = "\n".join(current_memo_lines).strip()
                sessions.append(current_session)
            current_session = {"title": stripped[3:].strip(), "speaker": "不明", "memo": ""}
            current_memo_lines = []

        elif stripped.lower().startswith("speaker:") and current_session is not None:
            current_session["speaker"] = stripped.split(":", 1)[1].strip()

        elif current_session is not None and stripped:
            # セッション直下の最初の行がスピーカー名として自動認識:
            # （）が含まれる or 20文字以下の短い行（人名）
            if current_session["speaker"] == "不明" and not current_memo_lines and ("（" in stripped or len(stripped) <= 20):
                current_session["speaker"] = stripped
            else:
                current_memo_lines.append(stripped)

    if current_session is not None:
        current_session["memo"] = "\n".join(current_memo_lines).strip()
        sessions.append(current_session)

    return {"event_name": event_name, "event_date": event_date, "sessions": sessions}


def _generate_knowledge_entry(session: dict) -> dict:
    prompt = f"""以下の勉強会セッションメモから、構造化されたナレッジエントリをJSON形式で生成してください。

スピーカー: {session.get('speaker', '不明')}
タイトル: {session.get('title', '不明')}
メモ:
{session.get('memo', '')}

以下のJSON形式のみ出力してください（他のテキストは不要）:
{{
  "summary": "100文字程度の要約",
  "keywords": ["キーワード1", "キーワード2"],
  "learnings": ["学び1", "学び2"],
  "tech_stack": ["技術1", "技術2"]
}}"""
    text = _invoke_bedrock(prompt)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if match:
            return json.loads(match.group(1))
        return {"summary": text[:200], "keywords": [], "learnings": [], "tech_stack": []}


def _generate_report(event_name: str, event_date: str, sessions: list, overall_comment: str) -> str:
    sessions_text = "\n\n".join([
        f"### {s.get('title', '不明')} （{s.get('speaker', '不明')}）\n{s.get('memo', '')}"
        for s in sessions
    ])
    prompt = f"""以下の勉強会メモから、参加レポートをMarkdown形式で生成してください。

イベント名: {event_name}
日付: {event_date}
全体コメント: {overall_comment}

セッション一覧:
{sessions_text}

以下の構成でレポートを作成してください:
# {event_name} 参加レポート
## 開催概要
## セッション別まとめ
## 全体の感想・学び

簡潔で読みやすいレポートをお願いします。"""
    return _invoke_bedrock(prompt, max_tokens=2048)


@tool
def add_memo(memo_content: str) -> str:
    """
    勉強会・LT会のメモを保存し、参加レポートとナレッジエントリを自動生成する。
    memo_contentには以下のフォーマットでメモを渡す:

    # イベント名
    date: YYYY-MM-DD

    ## セッションタイトル
    speaker: スピーカー名

    メモ内容（自由記述）

    dateとspeakerは省略可能だが、あると精度が上がる。
    """
    parsed = _parse_memo(memo_content)
    event_name = parsed["event_name"] or "不明イベント"
    event_date = parsed["event_date"] or datetime.now().strftime("%Y-%m-%d")
    sessions = parsed["sessions"]

    if not sessions:
        return "エラー: セッション情報が見つかりませんでした。## セッションタイトル の形式で記述してください。"

    event_id = f"{event_date}_{event_name}"

    # 重複チェック
    existing_events = db.get_events()
    if any(e["event_id"] == event_id for e in existing_events):
        return f"イベント「{event_name}（{event_date}）」は既に登録済みです。（ID: {event_id}）"

    # レポート生成・保存
    report_content = _generate_report(event_name, event_date, sessions, "")
    report_path = db.save_report(event_id, report_content)

    # イベント保存
    db.add_event(
        event_id=event_id,
        event_name=event_name,
        event_date=event_date,
        report_path=report_path,
        sessions=[s.get("title", "不明") for s in sessions],
    )

    # セッションごとにナレッジエントリ生成（S3への保存は最後に一括）
    processed = []
    bulk_entries = []
    for session in sessions:
        speaker = session.get("speaker", "不明")
        title = session.get("title", "不明")
        entry = _generate_knowledge_entry(session)
        bulk_entries.append({
            "speaker": speaker,
            "event_id": event_id,
            "event_name": event_name,
            "event_date": event_date,
            "title": title,
            "summary": entry.get("summary", ""),
            "keywords": entry.get("keywords", []),
            "learnings": entry.get("learnings", []),
            "tech_stack": entry.get("tech_stack", []),
        })
        processed.append(f"  - {speaker}: {title}")
    db.add_bulk_speaker_knowledge(bulk_entries)

    return (
        f"メモを保存しました。\n"
        f"イベント: {event_name}（{event_date}）\n"
        f"レポート: {report_path}\n"
        f"処理したセッション:\n" + "\n".join(processed)
    )


@tool
def preview_memo_file(file_path: str) -> str:
    """
    S3の memos/ にあるメモファイルを読み込み、生成されるレポートとナレッジエントリをプレビューする。
    保存は行わない。内容を確認してから process_memo_file で保存する想定。
    file_pathにはファイル名（例: 20260601_勉強会.md）を指定する。
    """
    try:
        memo_content = db.read_memo_file(file_path)
    except Exception as e:
        available = db.list_memo_files()
        return (
            f"ファイルが見つかりません: {file_path}\n"
            f"S3 memos/ にあるファイル: {', '.join(available) if available else 'なし'}\n"
            f"エラー: {e}"
        )

    parsed = _parse_memo(memo_content)
    event_name = parsed["event_name"] or "不明イベント"
    event_date = parsed["event_date"] or datetime.now().strftime("%Y-%m-%d")
    sessions = parsed["sessions"]

    if not sessions:
        return "エラー: セッション情報が見つかりませんでした。## セッションタイトル の形式で記述してください。"

    report_content = _generate_report(event_name, event_date, sessions, "")

    knowledge_lines = []
    bulk_entries = []
    for session in sessions:
        speaker = session.get("speaker", "不明")
        title = session.get("title", "不明")
        entry = _generate_knowledge_entry(session)
        bulk_entries.append({
            "speaker": speaker,
            "title": title,
            "entry": entry,
        })
        knowledge_lines.append(f"### {speaker}：{title}")
        knowledge_lines.append(f"- 要約: {entry.get('summary', '')}")
        knowledge_lines.append(f"- キーワード: {', '.join(entry.get('keywords', []))}")
        knowledge_lines.append(f"- 学び: {', '.join(entry.get('learnings', []))}")
        knowledge_lines.append(f"- 技術スタック: {', '.join(entry.get('tech_stack', []))}")
        knowledge_lines.append("")

    db.save_preview_cache(file_path, {
        "event_name": event_name,
        "event_date": event_date,
        "sessions": sessions,
        "report": report_content,
        "entries": bulk_entries,
    })

    return (
        f"## プレビュー: {event_name}（{event_date}）\n\n"
        f"---\n### 生成レポート\n\n{report_content}\n\n"
        f"---\n### ナレッジエントリ（{len(sessions)}件）\n\n"
        + "\n".join(knowledge_lines)
        + "※ 保存する場合は process_memo_file を使ってください。"
    )


@tool
def process_memo_file(file_path: str) -> str:
    """
    S3の memos/ に置いたメモファイル（Markdown）を読み込んでレポートとナレッジエントリを生成・保存する。
    file_pathにはファイル名（例: 20260601_勉強会.md）を指定する。
    事前に aws s3 cp でファイルをS3にアップロードしておく必要がある。
    preview_memo_file を先に呼んでいる場合はキャッシュを再利用し、Bedrock呼び出しをスキップする。
    """
    cache = db.load_preview_cache(file_path)
    if cache:
        event_name = cache["event_name"]
        event_date = cache["event_date"]
        sessions = cache["sessions"]
        report_content = cache["report"]
        entries = cache["entries"]

        event_id = f"{event_date}_{event_name}"
        existing_events = db.get_events()
        if any(e["event_id"] == event_id for e in existing_events):
            db.delete_preview_cache(file_path)
            return f"イベント「{event_name}（{event_date}）」は既に登録済みです。（ID: {event_id}）"

        report_path = db.save_report(event_id, report_content)
        db.add_event(
            event_id=event_id,
            event_name=event_name,
            event_date=event_date,
            report_path=report_path,
            sessions=[s.get("title", "不明") for s in sessions],
        )

        bulk_entries = []
        processed = []
        for e in entries:
            bulk_entries.append({
                "speaker": e["speaker"],
                "event_id": event_id,
                "event_name": event_name,
                "event_date": event_date,
                "title": e["title"],
                "summary": e["entry"].get("summary", ""),
                "keywords": e["entry"].get("keywords", []),
                "learnings": e["entry"].get("learnings", []),
                "tech_stack": e["entry"].get("tech_stack", []),
            })
            processed.append(f"  - {e['speaker']}: {e['title']}")
        db.add_bulk_speaker_knowledge(bulk_entries)
        db.delete_preview_cache(file_path)

        return (
            f"メモを保存しました（キャッシュ利用）。\n"
            f"イベント: {event_name}（{event_date}）\n"
            f"レポート: {report_path}\n"
            f"処理したセッション:\n" + "\n".join(processed)
        )

    try:
        memo_content = db.read_memo_file(file_path)
        return add_memo(memo_content)
    except Exception as e:
        available = db.list_memo_files()
        return (
            f"ファイルが見つかりません: {file_path}\n"
            f"S3 memos/ にあるファイル: {', '.join(available) if available else 'なし'}\n"
            f"エラー: {e}"
        )


@tool
def get_report(event_id: str) -> str:
    """
    保存済みの参加レポートを取得して返す。
    event_idはlist_eventsで確認できるIDを指定する。
    event_idが不明な場合はlist_eventsを先に呼ぶこと。
    """
    events = db.get_events()
    # 完全一致 → 部分一致の順で検索
    matched = next((e for e in events if e["event_id"] == event_id), None)
    if matched is None:
        matched = next((e for e in events if event_id.lower() in e["event_id"].lower()), None)
    if matched is None:
        return f"イベントID「{event_id}」が見つかりません。list_eventsで登録済みIDを確認してください。"
    report_path = matched.get("report_path", "")
    if not report_path:
        return f"イベント「{matched['event_id']}」のレポートパスが登録されていません。"
    try:
        return db.read_report(report_path)
    except Exception as e:
        return f"レポートの読み込みに失敗しました: {e}"


@tool
def search_knowledge(keyword: str) -> str:
    """キーワードでナレッジDBを全文検索して結果を返す。"""
    results = db.search(keyword)
    if not results:
        return f"「{keyword}」に関するナレッジは見つかりませんでした。"

    lines = [f"「{keyword}」の検索結果（{len(results)}件）:\n"]
    for r in results:
        lines.append(f"## {r['title']} — {r['speaker']}")
        lines.append(f"イベント: {r['event_name']}（{r['event_date']}）")
        lines.append(f"要約: {r.get('summary', '')}")
        if r.get("keywords"):
            lines.append(f"キーワード: {', '.join(r['keywords'])}")
        lines.append("")
    return "\n".join(lines)


@tool
def ask_knowledge(question: str) -> str:
    """蓄積されたナレッジDB全体をもとに、自然言語の質問に回答する。"""
    all_knowledge = db.get_all_knowledge()
    if not all_knowledge.get("speakers"):
        return "まだナレッジが蓄積されていません。勉強会メモを追加してください。"

    context_parts = []
    for speaker, sessions in all_knowledge["speakers"].items():
        for session in sessions:
            context_parts.append(
                f"スピーカー: {speaker}\n"
                f"タイトル: {session.get('title')}\n"
                f"イベント: {session.get('event_name')}（{session.get('event_date')}）\n"
                f"要約: {session.get('summary')}\n"
                f"学び: {', '.join(session.get('learnings', []))}"
            )

    context = "\n\n---\n\n".join(context_parts[:20])  # コンテキスト上限

    prompt = f"""以下は勉強会・LT会で得たナレッジです。このナレッジをもとに質問に答えてください。

## ナレッジ
{context}

## 質問
{question}

日本語で回答してください。"""
    return _invoke_bedrock(prompt)


@tool
def list_events() -> str:
    """登録済みイベントの一覧を返す。"""
    events = db.get_events()
    if not events:
        return "まだイベントが登録されていません。"

    lines = [f"登録済みイベント（{len(events)}件）:\n"]
    for e in events:
        lines.append(f"- {e['event_name']}（{e['event_date']}）")
        lines.append(f"  ID: {e['event_id']}")
        lines.append(f"  レポート: {e.get('report_path', 'N/A')}")
    return "\n".join(lines)


@tool
def show_speakers() -> str:
    """スピーカー別のナレッジ一覧を返す。"""
    speakers = db.get_speakers()
    if not speakers:
        return "まだスピーカーのナレッジが登録されていません。"

    lines = [f"スピーカー一覧（{len(speakers)}名）:\n"]
    for speaker, sessions in speakers.items():
        lines.append(f"## {speaker}（{len(sessions)}件の発表）")
        for s in sessions:
            lines.append(f"  - {s['title']}（{s['event_name']}, {s['event_date']}）")
        lines.append("")
    return "\n".join(lines)
