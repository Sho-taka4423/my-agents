from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent import agent

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context):
    prompt = payload.get("prompt", "")
    if not prompt:
        return {"error": "promptフィールドが必要です"}
    response = agent(prompt)
    return {"response": str(response)}


if __name__ == "__main__":
    app.run()
