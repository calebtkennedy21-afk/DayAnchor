import { format, subDays } from 'date-fns';
import { loadHistory, loadTasks } from '../utils/storage';
import { TrendingUp, Award, BarChart2 } from 'lucide-react';

const CATEGORY_COLORS = {
  Admin: 'bg-blue-500',
  Equipment: 'bg-orange-500',
  Education: 'bg-purple-500',
  Personal: 'bg-green-500',
  Meeting: 'bg-pink-500',
  Supply: 'bg-yellow-500',
  Other: 'bg-slate-400',
};

function getWeekData() {
  const today = new Date();
  const allTasks = loadTasks();
  const history = loadHistory();
  const combined = [...allTasks, ...history];

  return Array.from({ length: 7 }, (_, i) => {
    const day = subDays(today, 6 - i);
    const dateKey = format(day, 'yyyy-MM-dd');
    const dayTasks = combined.filter(t => t.date === dateKey);
    const completed = dayTasks.filter(t => t.completed).length;
    const total = dayTasks.length;
    return {
      label: format(day, 'EEE'),
      dateKey,
      completed,
      total,
      rate: total > 0 ? Math.round((completed / total) * 100) : 0,
      isToday: dateKey === format(today, 'yyyy-MM-dd'),
    };
  });
}

function getCategoryStats() {
  const allTasks = loadTasks();
  const history = loadHistory();
  const combined = [...allTasks, ...history];
  const today = format(new Date(), 'yyyy-MM-dd');
  const weekAgo = format(subDays(new Date(), 7), 'yyyy-MM-dd');
  const thisWeek = combined.filter(t => t.date >= weekAgo && t.date <= today);

  return thisWeek.reduce((acc, t) => {
    if (!acc[t.category]) acc[t.category] = { total: 0, completed: 0 };
    acc[t.category].total += 1;
    if (t.completed) acc[t.category].completed += 1;
    return acc;
  }, {});
}

export default function Review({ stats }) {
  const weekData = getWeekData();
  const categoryStats = getCategoryStats();
  const maxTotal = Math.max(...weekData.map(d => d.total), 1);

  const avgRate = weekData.filter(d => d.total > 0).reduce((sum, d) => sum + d.rate, 0) /
    (weekData.filter(d => d.total > 0).length || 1);

  const streakDays = (() => {
    let streak = 0;
    for (let i = weekData.length - 1; i >= 0; i--) {
      if (weekData[i].total > 0 && weekData[i].rate >= 50) streak++;
      else break;
    }
    return streak;
  })();

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-bold text-slate-800">Productivity Review</h2>
        <p className="text-sm text-slate-500">7-day overview of your clinic day productivity</p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-white rounded-xl border border-slate-200 p-3 text-center shadow-sm">
          <div className="text-2xl font-bold text-teal-600">{Math.round(avgRate)}%</div>
          <p className="text-xs text-slate-500 mt-1">Avg. Completion</p>
        </div>
        <div className="bg-white rounded-xl border border-slate-200 p-3 text-center shadow-sm">
          <div className="text-2xl font-bold text-amber-500">{streakDays}</div>
          <p className="text-xs text-slate-500 mt-1">Day Streak 🔥</p>
        </div>
        <div className="bg-white rounded-xl border border-slate-200 p-3 text-center shadow-sm">
          <div className="text-2xl font-bold text-green-600">{stats.completed}</div>
          <p className="text-xs text-slate-500 mt-1">Done Today</p>
        </div>
      </div>

      {/* Weekly bar chart */}
      <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
        <div className="flex items-center gap-2 mb-4">
          <BarChart2 size={18} className="text-teal-600" />
          <h3 className="font-semibold text-slate-700">Weekly Activity</h3>
        </div>
        <div className="flex items-end gap-2 h-28">
          {weekData.map((day, i) => (
            <div key={i} className="flex-1 flex flex-col items-center gap-1">
              <div className="w-full flex flex-col justify-end" style={{ height: '80px' }}>
                {day.total > 0 ? (
                  <div
                    className={`w-full rounded-t transition-all ${day.isToday ? 'bg-teal-500' : 'bg-slate-200'}`}
                    style={{ height: `${(day.total / maxTotal) * 80}px`, minHeight: '6px' }}
                  >
                    <div
                      className={`w-full rounded-t ${day.isToday ? 'bg-teal-700' : 'bg-slate-400'}`}
                      style={{ height: `${(day.completed / day.total) * 100}%` }}
                    />
                  </div>
                ) : (
                  <div className="w-full rounded bg-slate-100" style={{ height: '6px' }} />
                )}
              </div>
              <span className={`text-xs font-medium ${day.isToday ? 'text-teal-600' : 'text-slate-400'}`}>
                {day.label}
              </span>
              {day.total > 0 && (
                <span className="text-xs text-slate-400">{day.rate}%</span>
              )}
            </div>
          ))}
        </div>
        <div className="flex items-center gap-4 mt-3 text-xs text-slate-500">
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded bg-slate-400" /> Completed
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded bg-slate-200" /> Total
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded bg-teal-600" /> Today
          </div>
        </div>
      </div>

      {/* Category breakdown this week */}
      {Object.keys(categoryStats).length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp size={18} className="text-teal-600" />
            <h3 className="font-semibold text-slate-700">Category Performance (7 days)</h3>
          </div>
          <div className="space-y-3">
            {Object.entries(categoryStats).map(([cat, data]) => {
              const rate = data.total > 0 ? Math.round((data.completed / data.total) * 100) : 0;
              return (
                <div key={cat}>
                  <div className="flex items-center justify-between text-sm mb-1">
                    <span className="font-medium text-slate-600">{cat}</span>
                    <span className="text-slate-400 text-xs">{data.completed}/{data.total} · {rate}%</span>
                  </div>
                  <div className="w-full bg-slate-100 rounded-full h-2">
                    <div
                      className={`h-2 rounded-full transition-all duration-500 ${CATEGORY_COLORS[cat] || 'bg-slate-400'}`}
                      style={{ width: `${rate}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Encouragement */}
      <div className="bg-gradient-to-r from-teal-50 to-cyan-50 border border-teal-100 rounded-xl p-4">
        <div className="flex items-center gap-2 mb-1">
          <Award size={18} className="text-teal-600" />
          <h3 className="font-semibold text-teal-700">Keep it up!</h3>
        </div>
        <p className="text-sm text-teal-600">
          {stats.completionRate >= 80
            ? "Excellent work today! You're crushing it. 🏆"
            : stats.completionRate >= 50
            ? "Great progress! You're over halfway through your tasks. 💪"
            : stats.total === 0
            ? "No tasks added yet — start your day by adding some tasks!"
            : "You've got this! Every completed task is a win. 🎯"}
        </p>
      </div>
    </div>
  );
}
