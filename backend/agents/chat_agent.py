"""ChatAgent — agentic, tool-using, streaming conversational assistant (Feature 1).

Extends the single-shot QueryAgent into a multi-turn agent loop:

  load history -> [system, ...history, user] -> stream an LLM turn -> if it asked for
  tools, run them (read-only), append results, loop -> else stream the final answer.

Memory: prior user/assistant turns are loaded from / saved to Neo4j (db/queries/chat).
Streaming: ``run_stream`` is an async generator of event dicts the SSE route forwards
({type: token|tool|done|error}). Read-only throughout — same safety posture as
QueryAgent (the tools never write; there is no write path).
"""

import json
import logging

from backend.agents.tools import TOOL_SCHEMAS, dispatch_tool
from backend.db.queries.chat import load_history, save_turn
from backend.llm.client import SYNTHESIS_MODEL, stream_chat
from backend.llm.prompts.chat import CHAT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_MAX_TOOL_ITERS = 6  # safety cap on the tool loop (each iter = one LLM turn)


def _assistant_msg(msg: dict) -> dict:
    """Normalised {content, tool_calls} -> OpenAI assistant message for the next call."""
    out: dict = {"role": "assistant", "content": msg.get("content")}
    if msg.get("tool_calls"):
        out["tool_calls"] = [
            {"id": c["id"], "type": "function",
             "function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in msg["tool_calls"]
        ]
    return out


class ChatAgent:
    agent_name = "ChatAgent"
    agent_version = "0.1.0"

    async def run_stream(self, session_id: str, question: str, tissue: str = "all"):
        """Yield event dicts: {type:'token',text} | {type:'tool',name,status} |
        {type:'done',answer} | {type:'error',message}."""
        history = await load_history(session_id)
        messages: list[dict] = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
        messages += [{"role": h["role"], "content": h["content"]} for h in history]
        user_content = question if tissue in (None, "", "all") else (
            f"{question}\n(Focus on the '{tissue}' tissue where relevant.)"
        )
        messages.append({"role": "user", "content": user_content})

        answer_parts: list[str] = []
        try:
            for _ in range(_MAX_TOOL_ITERS):
                final_msg: dict | None = None
                async for kind, payload in stream_chat(SYNTHESIS_MODEL, messages, TOOL_SCHEMAS):
                    if kind == "text":
                        answer_parts.append(payload)
                        yield {"type": "token", "text": payload}
                    elif kind == "message":
                        final_msg = payload

                if not final_msg or not final_msg.get("tool_calls"):
                    break  # no tool calls -> the streamed text was the final answer

                # Run the requested tools, append assistant + tool messages, loop.
                messages.append(_assistant_msg(final_msg))
                for call in final_msg["tool_calls"]:
                    yield {"type": "tool", "name": call["name"], "status": "running"}
                    result = await dispatch_tool(call["name"], call["arguments"])
                    yield {"type": "tool", "name": call["name"], "status": "done"}
                    messages.append({
                        "role": "tool", "tool_call_id": call["id"], "content": result,
                    })
        except Exception as exc:  # noqa: BLE001 — surface a clean error, don't 500 mid-stream
            logger.warning("ChatAgent stream failed: %s", exc)
            yield {"type": "error", "message": "The assistant hit an error. Please retry."}
            return

        answer = "".join(answer_parts).strip()
        await save_turn(session_id, "user", question)
        await save_turn(session_id, "assistant", answer)
        yield {"type": "done", "answer": answer}

    async def run(self, session_id: str, question: str, tissue: str = "all") -> dict:
        """Non-streaming convenience wrapper: drain the stream into a final answer +
        the tool names used (for the plain POST /chat endpoint / tests)."""
        answer = ""
        tools_used: list[str] = []
        async for ev in self.run_stream(session_id, question, tissue):
            if ev["type"] == "tool" and ev["status"] == "running":
                tools_used.append(ev["name"])
            elif ev["type"] == "done":
                answer = ev["answer"]
            elif ev["type"] == "error":
                return {"answer": ev["message"], "tools_used": tools_used, "error": True}
        return {"answer": answer, "tools_used": tools_used, "error": False}


chat_agent = ChatAgent()
