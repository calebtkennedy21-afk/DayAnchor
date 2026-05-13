import { useState, useCallback } from 'react';
import { sendMessageToAI, getDailySuggestion } from '../utils/ai';

export function useAI(apiKey) {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [suggestion, setSuggestion] = useState(null);
  const [loadingSuggestion, setLoadingSuggestion] = useState(false);

  const sendMessage = useCallback(async (userMessage) => {
    if (!userMessage.trim()) return;

    const userMsg = { role: 'user', content: userMessage, id: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    setLoading(true);
    setError(null);

    try {
      const reply = await sendMessageToAI(apiKey, messages, userMessage);
      setMessages(prev => [...prev, { role: 'assistant', content: reply, id: Date.now() + 1 }]);
    } catch (err) {
      setError(err.message || 'Failed to get AI response. Check your API key in Settings.');
    } finally {
      setLoading(false);
    }
  }, [apiKey, messages]);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  const fetchSuggestion = useCallback(async (tasks, date) => {
    setLoadingSuggestion(true);
    setSuggestion(null);
    try {
      const tip = await getDailySuggestion(apiKey, tasks, date);
      setSuggestion(tip);
    } catch {
      setSuggestion(null);
    } finally {
      setLoadingSuggestion(false);
    }
  }, [apiKey]);

  return { messages, loading, error, suggestion, loadingSuggestion, sendMessage, clearMessages, fetchSuggestion };
}
