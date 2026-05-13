import { useState, useEffect } from 'react';
import { format } from 'date-fns';
import Header from './components/Header';
import Dashboard from './components/Dashboard';
import TaskList from './components/TaskList';
import AIAssistant from './components/AIAssistant';
import Notes from './components/Notes';
import Review from './components/Review';
import SettingsModal from './components/SettingsModal';
import { useTasks } from './hooks/useTasks';
import { useAI } from './hooks/useAI';
import { loadSettings } from './utils/storage';

export default function App() {
  const [activeView, setActiveView] = useState('Dashboard');
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState(() => loadSettings());

  const { tasks, stats, addTask, toggleTask, deleteTask, editTask } = useTasks();
  const { messages, loading, error, suggestion, loadingSuggestion, sendMessage, clearMessages, fetchSuggestion } =
    useAI(settings.openaiKey);

  // Fetch daily suggestion when Dashboard loads (if key present)
  useEffect(() => {
    if (activeView === 'Dashboard' && settings.openaiKey && !suggestion) {
      fetchSuggestion(tasks, format(new Date(), 'EEEE, MMMM d, yyyy'));
    }
    // Intentionally re-runs only when the view or API key changes, not on every task change.
    // This avoids triggering a new API call every time a task is added/completed.
  }, [activeView, settings.openaiKey]); // eslint-disable-line react-hooks/exhaustive-deps

  function handleRefreshSuggestion() {
    fetchSuggestion(tasks, format(new Date(), 'EEEE, MMMM d, yyyy'));
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <Header
        activeView={activeView}
        setActiveView={setActiveView}
        onSettingsClick={() => setShowSettings(true)}
      />

      <main className="max-w-3xl mx-auto px-4 py-5">
        {activeView === 'Dashboard' && (
          <Dashboard
            stats={stats}
            tasks={tasks}
            suggestion={suggestion}
            loadingSuggestion={loadingSuggestion}
            onFetchSuggestion={handleRefreshSuggestion}
            onNavigate={setActiveView}
          />
        )}
        {activeView === 'Tasks' && (
          <TaskList
            tasks={tasks}
            stats={stats}
            onAdd={addTask}
            onToggle={toggleTask}
            onDelete={deleteTask}
            onEdit={editTask}
          />
        )}
        {activeView === 'AI Assistant' && (
          <AIAssistant
            messages={messages}
            loading={loading}
            error={error}
            onSend={sendMessage}
            onClear={clearMessages}
            hasApiKey={!!settings.openaiKey}
          />
        )}
        {activeView === 'Notes' && <Notes />}
        {activeView === 'Review' && <Review stats={stats} />}
      </main>

      {showSettings && (
        <SettingsModal
          settings={settings}
          onClose={() => setShowSettings(false)}
          onSave={setSettings}
        />
      )}
    </div>
  );
}
