import { useState, useRef, useEffect } from 'react';
import { Send, Bot, User, Trash2, Info } from 'lucide-react';

const QUICK_PROMPTS = [
  "Help me prioritize my tasks for today",
  "How can I improve clinic workflow efficiency?",
  "Suggest a morning routine for clinic days",
  "What are good strategies for managing a busy surgical schedule?",
  "Give me tips for end-of-day clinic cleanup",
  "How can I stay organized with supply management?",
];

export default function AIAssistant({ messages, loading, error, onSend, onClear, hasApiKey }) {
  const [input, setInput] = useState('');
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  function handleSubmit(e) {
    e.preventDefault();
    if (!input.trim() || loading) return;
    onSend(input.trim());
    setInput('');
  }

  function handleQuickPrompt(prompt) {
    onSend(prompt);
  }

  return (
    <div className="flex flex-col h-[calc(100vh-160px)] min-h-[500px]">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="bg-teal-600 text-white p-1.5 rounded-lg">
            <Bot size={18} />
          </div>
          <div>
            <h2 className="font-semibold text-slate-800">AI Assistant</h2>
            <p className="text-xs text-slate-500">Powered by GPT-4o mini</p>
          </div>
        </div>
        {messages.length > 0 && (
          <button
            onClick={onClear}
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-red-500 hover:bg-red-50 px-2 py-1 rounded-lg transition-colors"
          >
            <Trash2 size={13} />
            Clear
          </button>
        )}
      </div>

      {/* PHI Warning */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 mb-3 flex items-start gap-2">
        <Info size={15} className="text-amber-600 mt-0.5 shrink-0" />
        <p className="text-xs text-amber-700">
          <strong>Privacy Reminder:</strong> Do not share any patient health information (PHI). Keep all conversations focused on workflows, schedules, and professional topics only.
        </p>
      </div>

      {/* Chat area */}
      <div className="flex-1 bg-white rounded-xl border border-slate-200 overflow-y-auto p-4 space-y-4 shadow-sm">
        {!hasApiKey && (
          <div className="text-center py-8">
            <Bot size={40} className="mx-auto text-slate-300 mb-3" />
            <p className="text-slate-500 text-sm font-medium mb-1">AI Assistant not configured</p>
            <p className="text-slate-400 text-xs">Add your OpenAI API key in Settings to start chatting.</p>
          </div>
        )}

        {hasApiKey && messages.length === 0 && !loading && (
          <div className="space-y-4">
            <div className="text-center pt-4">
              <Bot size={36} className="mx-auto text-teal-300 mb-2" />
              <p className="text-slate-500 text-sm">
                Hi! I'm your DayAnchor AI assistant. I can help with clinic productivity, task prioritization, and workflow tips.
              </p>
            </div>
            <div>
              <p className="text-xs text-slate-500 font-medium mb-2 uppercase tracking-wide">Quick prompts:</p>
              <div className="flex flex-wrap gap-2">
                {QUICK_PROMPTS.map((p, i) => (
                  <button
                    key={i}
                    onClick={() => handleQuickPrompt(p)}
                    className="text-xs px-3 py-1.5 border border-teal-200 text-teal-700 rounded-full hover:bg-teal-50 transition-colors text-left"
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <ChatBubble key={msg.id} message={msg} />
        ))}

        {loading && (
          <div className="flex items-start gap-2">
            <div className="bg-teal-600 text-white p-1.5 rounded-full shrink-0">
              <Bot size={14} />
            </div>
            <div className="bg-slate-100 rounded-2xl rounded-tl-sm px-4 py-3">
              <div className="flex gap-1 items-center">
                <span className="typing-dot w-2 h-2 rounded-full bg-slate-400 inline-block" />
                <span className="typing-dot w-2 h-2 rounded-full bg-slate-400 inline-block" />
                <span className="typing-dot w-2 h-2 rounded-full bg-slate-400 inline-block" />
              </div>
            </div>
          </div>
        )}

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-3 text-sm text-red-600">
            {error}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="flex gap-2 mt-3">
        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder={hasApiKey ? "Ask about productivity, workflows, or planning... (no patient info)" : "Add API key in Settings to chat"}
          disabled={!hasApiKey || loading}
          className="flex-1 border border-slate-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-teal-400 focus:ring-2 focus:ring-teal-100 disabled:bg-slate-50 disabled:text-slate-400"
        />
        <button
          type="submit"
          disabled={!input.trim() || !hasApiKey || loading}
          className="px-4 py-2.5 bg-teal-600 text-white rounded-xl hover:bg-teal-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          <Send size={16} />
        </button>
      </form>
    </div>
  );
}

function ChatBubble({ message }) {
  const isUser = message.role === 'user';
  return (
    <div className={`flex items-start gap-2 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div className={`p-1.5 rounded-full shrink-0 ${isUser ? 'bg-slate-600 text-white' : 'bg-teal-600 text-white'}`}>
        {isUser ? <User size={14} /> : <Bot size={14} />}
      </div>
      <div
        className={`max-w-[80%] px-4 py-2.5 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${
          isUser
            ? 'bg-slate-700 text-white rounded-tr-sm'
            : 'bg-slate-100 text-slate-700 rounded-tl-sm'
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}
