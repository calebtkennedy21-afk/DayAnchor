import { format } from 'date-fns';
import { CheckCircle2, Clock, Target, Sparkles, RefreshCw } from 'lucide-react';

const CATEGORY_COLORS = {
  Admin: 'bg-blue-100 text-blue-700',
  Equipment: 'bg-orange-100 text-orange-700',
  Education: 'bg-purple-100 text-purple-700',
  Personal: 'bg-green-100 text-green-700',
  Meeting: 'bg-pink-100 text-pink-700',
  Supply: 'bg-yellow-100 text-yellow-700',
  Other: 'bg-slate-100 text-slate-600',
};

const PRIORITY_COLORS = {
  high: 'border-l-red-500',
  medium: 'border-l-yellow-400',
  low: 'border-l-green-400',
};

export default function Dashboard({ stats, tasks, suggestion, loadingSuggestion, onFetchSuggestion, onNavigate }) {
  const today = format(new Date(), 'EEEE, MMMM d');
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';

  const pendingHighPriority = tasks.filter(t => !t.completed && t.priority === 'high');
  const recentCompleted = tasks.filter(t => t.completed).slice(-3);

  return (
    <div className="space-y-5">
      {/* Greeting banner */}
      <div className="bg-gradient-to-r from-teal-600 to-teal-700 rounded-xl p-5 text-white shadow-md">
        <p className="text-teal-100 text-sm font-medium mb-1">{today}</p>
        <h2 className="text-2xl font-bold mb-1">{greeting}! 👋</h2>
        <p className="text-teal-100 text-sm">Ready to anchor your day at the clinic.</p>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard icon={<Target size={20} />} label="Total Tasks" value={stats.total} color="blue" />
        <StatCard icon={<CheckCircle2 size={20} />} label="Completed" value={stats.completed} color="green" />
        <StatCard icon={<Clock size={20} />} label="Pending" value={stats.pending} color="yellow" />
        <StatCard
          icon={<span className="text-lg font-bold">{stats.completionRate}%</span>}
          label="Completion Rate"
          value={<ProgressBar value={stats.completionRate} />}
          color="teal"
          isProgress
        />
      </div>

      {/* AI Tip of the day */}
      <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Sparkles size={18} className="text-teal-600" />
            <h3 className="font-semibold text-slate-700">AI Tip of the Day</h3>
          </div>
          <button
            onClick={onFetchSuggestion}
            className="flex items-center gap-1 text-xs text-teal-600 hover:text-teal-800 hover:bg-teal-50 px-2 py-1 rounded-md transition-colors"
            disabled={loadingSuggestion}
          >
            <RefreshCw size={13} className={loadingSuggestion ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
        {loadingSuggestion ? (
          <div className="flex items-center gap-2 text-slate-400 text-sm">
            <div className="flex gap-1">
              <span className="typing-dot w-2 h-2 rounded-full bg-teal-400 inline-block" />
              <span className="typing-dot w-2 h-2 rounded-full bg-teal-400 inline-block" />
              <span className="typing-dot w-2 h-2 rounded-full bg-teal-400 inline-block" />
            </div>
            Generating tip...
          </div>
        ) : suggestion ? (
          <p className="text-slate-600 text-sm leading-relaxed">{suggestion}</p>
        ) : (
          <p className="text-slate-400 text-sm italic">
            Click Refresh to get an AI-powered productivity tip for today (requires API key in Settings).
          </p>
        )}
      </div>

      {/* Two-column layout for priorities and recent completions */}
      <div className="grid lg:grid-cols-2 gap-4">
        {/* High priority pending */}
        <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
          <h3 className="font-semibold text-slate-700 mb-3 flex items-center gap-2">
            <span className="w-2 h-2 bg-red-500 rounded-full" />
            High Priority
          </h3>
          {pendingHighPriority.length === 0 ? (
            <p className="text-slate-400 text-sm italic">No high-priority tasks pending 🎉</p>
          ) : (
            <ul className="space-y-2">
              {pendingHighPriority.slice(0, 5).map(t => (
                <li key={t.id} className={`text-sm border-l-4 ${PRIORITY_COLORS[t.priority]} pl-3 py-1`}>
                  <span className="text-slate-700">{t.text}</span>
                  <span className={`ml-2 text-xs px-1.5 py-0.5 rounded-full ${CATEGORY_COLORS[t.category] || CATEGORY_COLORS.Other}`}>
                    {t.category}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <button
            onClick={() => onNavigate('Tasks')}
            className="mt-3 text-xs text-teal-600 hover:underline"
          >
            View all tasks →
          </button>
        </div>

        {/* Category breakdown */}
        <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
          <h3 className="font-semibold text-slate-700 mb-3 flex items-center gap-2">
            <span className="w-2 h-2 bg-teal-500 rounded-full" />
            Tasks by Category
          </h3>
          {Object.keys(stats.byCategory).length === 0 ? (
            <p className="text-slate-400 text-sm italic">No tasks yet today</p>
          ) : (
            <ul className="space-y-2">
              {Object.entries(stats.byCategory).map(([cat, count]) => (
                <li key={cat} className="flex items-center justify-between text-sm">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${CATEGORY_COLORS[cat] || CATEGORY_COLORS.Other}`}>
                    {cat}
                  </span>
                  <span className="text-slate-600 font-medium">{count} task{count !== 1 ? 's' : ''}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Recent completions */}
      {recentCompleted.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
          <h3 className="font-semibold text-slate-700 mb-3 flex items-center gap-2">
            <CheckCircle2 size={16} className="text-green-500" />
            Recently Completed
          </h3>
          <ul className="space-y-1.5">
            {recentCompleted.map(t => (
              <li key={t.id} className="flex items-center gap-2 text-sm text-slate-500">
                <CheckCircle2 size={14} className="text-green-400 shrink-0" />
                <span className="line-through">{t.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function StatCard({ icon, label, value, color, isProgress }) {
  const colorMap = {
    blue: 'bg-blue-50 text-blue-600',
    green: 'bg-green-50 text-green-600',
    yellow: 'bg-yellow-50 text-yellow-600',
    teal: 'bg-teal-50 text-teal-600',
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
      <div className={`inline-flex p-2 rounded-lg mb-2 ${colorMap[color]}`}>
        {icon}
      </div>
      <p className="text-xs text-slate-500 font-medium">{label}</p>
      {isProgress ? (
        value
      ) : (
        <p className="text-2xl font-bold text-slate-800 mt-1">{value}</p>
      )}
    </div>
  );
}

function ProgressBar({ value }) {
  return (
    <div className="mt-1">
      <div className="w-full bg-slate-100 rounded-full h-2">
        <div
          className="bg-teal-500 h-2 rounded-full transition-all duration-500"
          style={{ width: `${value}%` }}
        />
      </div>
      <p className="text-xs text-slate-500 mt-1">{value}% done</p>
    </div>
  );
}
