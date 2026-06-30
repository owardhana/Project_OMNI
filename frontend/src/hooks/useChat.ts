import { useCallback, useRef, useState } from 'react';

import { chatStream } from '../api/client';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

// State for the agentic chat panel (Feature 1): multi-turn history, live-streamed
// assistant tokens, and the currently-running tool name (for a "thinking" chip).
export function useChat() {
  // One session id per mounted conversation -> server-side conversational memory.
  const sessionId = useRef(
    typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : String(Date.now()),
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [draft, setDraft] = useState(''); // assistant text streaming in
  const [tool, setTool] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(
    async (message: string, tissue = 'all') => {
      const text = message.trim();
      if (!text || streaming) return;
      setError(null);
      setMessages((m) => [...m, { role: 'user', content: text }]);
      setStreaming(true);
      setDraft('');
      setTool(null);

      let acc = '';
      try {
        await chatStream({ session_id: sessionId.current, message: text, tissue }, (ev) => {
          if (ev.type === 'token') {
            acc += ev.text;
            setDraft(acc);
          } else if (ev.type === 'tool') {
            setTool(ev.status === 'running' ? ev.name : null);
          } else if (ev.type === 'done') {
            const final = ev.answer || acc;
            setMessages((m) => [...m, { role: 'assistant', content: final }]);
            setDraft('');
            setTool(null);
          } else if (ev.type === 'error') {
            setError(ev.message);
          }
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setStreaming(false);
        setTool(null);
      }
    },
    [streaming],
  );

  return { messages, draft, streaming, tool, error, send };
}
