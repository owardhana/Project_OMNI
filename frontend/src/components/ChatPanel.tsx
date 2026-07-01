import { useEffect, useRef, useState } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { useChat } from '../hooks/useChat';

interface Props {
  tissue: string;
}

// Markdown link -> open in a new tab (answers cite external PMIDs / resources).
// Strip react-markdown's `node` prop so it doesn't leak onto the DOM element.
const mdComponents: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
};

function Assistant({ text }: { text: string }) {
  return (
    <div className="chat-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

// The single "Ask OmniGraph" assistant (Feature 1): multi-turn, streaming, tool-using
// over the live graph, with server-side conversational memory. Replaces the former
// one-shot Text2Cypher box — this is the sole chat surface.
export default function ChatPanel({ tissue }: Props) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const { messages, draft, streaming, tool, error, send, reset } = useChat();
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
      <div className="chat-header">
        <button
          className="chat-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          <span className="chat-caret">{open ? '▾' : '▸'}</span>
          Ask OmniGraph
        </button>
        {open && messages.length > 0 && (
          <button
            className="chat-new"
            onClick={reset}
            disabled={streaming}
            title="Start a new conversation"
          >
            New chat
          </button>
        )}
      </div>

      {open && (
        <div className="chat-body">
          <div className="chat-log" ref={scrollRef}>
            {messages.length === 0 && !streaming && (
              <div className="chat-hint">
                Ask about the graph — e.g. <em>“How are TP53 and EGFR connected?”</em> or
                <em>“What metabolites does LDHA catalyse?”</em>. I search, traverse, and
                trace paths, and I remember earlier turns in this conversation.
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`chat-msg chat-${m.role}`}>
                {m.role === 'assistant' ? <Assistant text={m.content} /> : m.content}
              </div>
            ))}
            {streaming && (
              <div className="chat-msg chat-assistant chat-streaming">
                {draft ? (
                  <Assistant text={draft} />
                ) : (
                  <span className="chat-tool">{tool ? `running ${tool}…` : 'thinking…'}</span>
                )}
                {draft && tool && <span className="chat-tool">running {tool}…</span>}
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
            <button
              className="chat-send"
              onClick={submit}
              disabled={streaming || !input.trim()}
              aria-label="Send"
            >
              {streaming ? '…' : '↑'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
