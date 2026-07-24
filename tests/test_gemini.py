"""Quick sanity check — run this after adding your GOOGLE_API_KEY to .env.

    .venv/bin/python test_gemini.py
"""
from src.config.settings import settings
from src.llm.factory import get_llm, get_embeddings
from langchain_core.messages import HumanMessage

# Low-cost models only (cheapest first)
CHAT_MODELS_TO_TRY = [
    "gemini-3.1-flash-lite",
]

def test_chat():
    print("\n[1] Chat:")
    for model in CHAT_MODELS_TO_TRY:
        try:
            llm = get_llm(model)
            response = llm.invoke([HumanMessage(content="Say hello in one sentence.")])
            from src.utils import extract_text
            print(f"    PASSED  {model}")
            print(f"    Response: {extract_text(response.content).strip()}")
            return model
        except Exception as e:
            msg = str(e)
            if "limit: 0" in msg:
                print(f"    QUOTA   {model} — still limit:0, wait a few minutes after enabling billing")
            else:
                print(f"    FAILED  {model} — {msg[:120]}")
    return None


def test_embeddings():
    print(f"\n[2] Embeddings — model: {settings.embedding_model}")
    try:
        vector = get_embeddings().embed_query("travel planning test")
        print(f"    Vector length: {len(vector)}  —  PASSED")
    except Exception as e:
        print(f"    FAILED — {e}")


if __name__ == "__main__":
    print(f"Provider : {settings.llm_provider}")
    print(f"API key  : {settings.google_api_key[:8]}{'*' * 10}")

    working_model = test_chat()
    test_embeddings()

    if working_model:
        print(f"""
Set in your .env:
  COORDINATOR_MODEL=gemini-2.0-flash
  FLIGHT_AGENT_MODEL={working_model}
  ATTRACTIONS_AGENT_MODEL={working_model}
  HOTEL_AGENT_MODEL={working_model}
  TRANSPORT_AGENT_MODEL={working_model}
""")
    print("Done.")
