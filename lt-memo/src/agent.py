import sys

from strands import Agent
from strands.models.bedrock import BedrockModel

from tools import add_memo, ask_knowledge, get_report, list_events, preview_memo_file, process_memo_file, search_knowledge, show_speakers

model = BedrockModel(
    model_id="apac.amazon.nova-pro-v1:0",
    region_name="ap-northeast-1",
)

agent = Agent(
    model=model,
    tools=[add_memo, preview_memo_file, process_memo_file, get_report, search_knowledge, ask_knowledge, list_events, show_speakers],
    system_prompt="""あなたはエンジニアの勉強会・LT会ナレッジ管理エージェントです。
ユーザーのメモを参加レポートとナレッジDBに変換・蓄積し、
過去の学びをいつでも引き出せるように助けます。

## できること
- 勉強会メモの追加とレポート自動生成
- スピーカー別ナレッジの蓄積と検索
- 過去の学びへの自然言語での質問応答

常に日本語で回答してください。
""",
)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
        response = agent(prompt)
        print(response)
    else:
        print("使い方: python agent.py <プロンプト>")
        print("例: python agent.py イベント一覧を表示して")
