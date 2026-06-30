import { useEffect, useRef, useState } from 'react';

import { useChat } from '../hooks/useChat';

interface Props {
  tissue: string;
}

// Agentic chat panel (Feature 1): multi-turn, streaming, tool-using assistant over
// the live graph. Sits beside QueryPanel (which stays as the one-shot Text2Cypher box).
export default function ChatPanel({ tissue }: Props) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const { messages, draft, streaming, tool, error, send } = useChat();
  const scrollRef = useRef<HTMLDivElement>(null);

  // Keep the latest turn in view as tokens stream in.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, draft, tool]);

  const submit = () => {
    if (input.trim() && !streaming) {
      send(input.trim(), tissue);
      setInput('');
    }
  };

  return (
    <div className={`chat-panel${open ? ' open' : ''}`}>
      <button className="chat-toggle" onClick={() => setOpen((v) => !v)}>
        {open ? '▾ Chat with OmniGraph' : '▸ Chat with OmniGraph'}
      </button>

      {open && (
        <div className="chat-body">
          <div className="chat-log" ref={scrollRef}>
            {messages.length === 0 && !streaming && (
              <div className="chat-hint">
                Ask about the graph — e.g. “How are TP53 and EGFR connected?” or
                “What metabolites does LDHA catalyse?”. I can search, traverse, and
                trace paths.
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`chat-msg chat-${m.role}`}>
                {m.content}
              </div>
            ))}
            {streaming && (
              <div className="chat-msg chat-assistant chat-streaming">
                {draft}
                {tool && <span className="chat-tool">running {tool}…</span>}
                {!draft && !tool && <span className="chat-tool">thinking…</span>}
              </div>
            )}
          </div>

          {error && <div className="chat-error">{error}</div>}

          <div className="chat-input-row">
            <input
              className="chat-input"
              placeholder="Ask OmniGraph…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submit()}
              disabled={streaming}
            />
            <button className="chat-send" onClick={submit} disabled={streaming}>
              {streaming ? '…' : 'Send'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
