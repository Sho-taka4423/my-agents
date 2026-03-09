import json
import os
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

BUCKET_NAME = os.environ.get("LT_MEMO_BUCKET")
if not BUCKET_NAME:
    raise ValueError("環境変数 LT_MEMO_BUCKET が設定されていません。")
REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1")

KNOWLEDGE_KEY = "knowledge.json"
EVENTS_KEY = "events.json"
REPORTS_PREFIX = "reports/"
MEMOS_PREFIX = "memos/"
PREVIEW_CACHE_PREFIX = "memos/.cache/"


def _s3():
    return boto3.client("s3", region_name=REGION)


class KnowledgeDB:
    def __init__(self):
        self._ensure_defaults()

    def _ensure_defaults(self):
        s3 = _s3()
        for key, default in [
            (KNOWLEDGE_KEY, {"speakers": {}, "events": {}}),
            (EVENTS_KEY, []),
        ]:
            try:
                s3.head_object(Bucket=BUCKET_NAME, Key=key)
            except ClientError:
                self._save_json(key, default)

    def _load_json(self, key):
        try:
            response = _s3().get_object(Bucket=BUCKET_NAME, Key=key)
            return json.loads(response["Body"].read().decode("utf-8"))
        except ClientError:
            return [] if key == EVENTS_KEY else {"speakers": {}, "events": {}}

    def _save_json(self, key, data):
        _s3().put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

    def save_report(self, event_id: str, content: str) -> str:
        key = f"{REPORTS_PREFIX}{event_id}.md"
        _s3().put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType="text/markdown",
        )
        return f"s3://{BUCKET_NAME}/{key}"

    def read_report(self, s3_uri: str) -> str:
        """s3://bucket/key 形式のURIからレポートを読み込む"""
        key = s3_uri.replace(f"s3://{BUCKET_NAME}/", "")
        response = _s3().get_object(Bucket=BUCKET_NAME, Key=key)
        return response["Body"].read().decode("utf-8")

    def read_memo_file(self, filename: str) -> str:
        """S3の memos/ プレフィックスからメモファイルを読み込む"""
        key = f"{MEMOS_PREFIX}{filename}"
        response = _s3().get_object(Bucket=BUCKET_NAME, Key=key)
        return response["Body"].read().decode("utf-8")

    def list_memo_files(self) -> list:
        """S3の memos/ にあるファイル一覧を返す"""
        response = _s3().list_objects_v2(Bucket=BUCKET_NAME, Prefix=MEMOS_PREFIX)
        return [
            obj["Key"].replace(MEMOS_PREFIX, "")
            for obj in response.get("Contents", [])
            if obj["Key"] != MEMOS_PREFIX
        ]

    def add_event(self, event_id: str, event_name: str, event_date: str, report_path: str, sessions: list = None):
        events = self._load_json(EVENTS_KEY)
        for e in events:
            if e["event_id"] == event_id:
                return  # 既に存在する場合はスキップ
        events.append({
            "event_id": event_id,
            "event_name": event_name,
            "event_date": event_date,
            "report_path": report_path,
            "sessions": sessions or [],
            "created_at": datetime.now().isoformat(),
        })
        self._save_json(EVENTS_KEY, events)

    def add_speaker_knowledge(
        self,
        speaker: str,
        event_id: str,
        event_name: str,
        event_date: str,
        title: str,
        summary: str,
        keywords: list,
        learnings: list,
        tech_stack: list,
    ):
        self.add_bulk_speaker_knowledge([{
            "speaker": speaker,
            "event_id": event_id,
            "event_name": event_name,
            "event_date": event_date,
            "title": title,
            "summary": summary,
            "keywords": keywords,
            "learnings": learnings,
            "tech_stack": tech_stack,
        }])

    def add_bulk_speaker_knowledge(self, entries: list):
        """複数のナレッジエントリを一括追加する（S3 read/write は1回のみ）。"""
        knowledge = self._load_json(KNOWLEDGE_KEY)
        for e in entries:
            speaker = e["speaker"]
            if speaker not in knowledge["speakers"]:
                knowledge["speakers"][speaker] = []
            knowledge["speakers"][speaker].append({
                "event_id": e["event_id"],
                "event_name": e["event_name"],
                "event_date": e["event_date"],
                "title": e["title"],
                "summary": e["summary"],
                "keywords": e["keywords"],
                "learnings": e["learnings"],
                "tech_stack": e["tech_stack"],
            })
        self._save_json(KNOWLEDGE_KEY, knowledge)

    def search(self, keyword: str) -> list:
        knowledge = self._load_json(KNOWLEDGE_KEY)
        results = []
        keyword_lower = keyword.lower()
        for speaker, sessions in knowledge.get("speakers", {}).items():
            for session in sessions:
                text = " ".join([
                    speaker,
                    session.get("title", ""),
                    session.get("summary", ""),
                    " ".join(session.get("keywords", [])),
                    " ".join(session.get("learnings", [])),
                    " ".join(session.get("tech_stack", [])),
                ]).lower()
                if keyword_lower in text:
                    results.append({"speaker": speaker, **session})
        return results

    def get_all_knowledge(self) -> dict:
        return self._load_json(KNOWLEDGE_KEY)

    def get_events(self) -> list:
        return self._load_json(EVENTS_KEY)

    def get_speakers(self) -> dict:
        knowledge = self._load_json(KNOWLEDGE_KEY)
        return knowledge.get("speakers", {})

    def save_preview_cache(self, filename: str, data: dict):
        """プレビュー時に生成したレポート・ナレッジをS3に一時保存する。"""
        key = f"{PREVIEW_CACHE_PREFIX}{filename}.json"
        _s3().put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

    def load_preview_cache(self, filename: str) -> dict | None:
        """キャッシュが存在すれば返す。なければ None。"""
        key = f"{PREVIEW_CACHE_PREFIX}{filename}.json"
        try:
            response = _s3().get_object(Bucket=BUCKET_NAME, Key=key)
            return json.loads(response["Body"].read().decode("utf-8"))
        except ClientError:
            return None

    def delete_preview_cache(self, filename: str):
        """使用済みキャッシュを削除する。"""
        key = f"{PREVIEW_CACHE_PREFIX}{filename}.json"
        try:
            _s3().delete_object(Bucket=BUCKET_NAME, Key=key)
        except ClientError:
            pass
