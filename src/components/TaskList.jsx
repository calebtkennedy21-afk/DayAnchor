import { useState } from 'react';
import TaskItem from './TaskItem';
import AddTask from './AddTask';
import { Filter } from 'lucide-react';

const CATEGORIES = ['All', 'Admin', 'Equipment', 'Education', 'Personal', 'Meeting', 'Supply', 'Other'];
const FILTER_STATES = ['All', 'Pending', 'Completed'];

export default function TaskList({ tasks, stats, onAdd, onToggle, onDelete, onEdit }) {
  const [categoryFilter, setCategoryFilter] = useState('All');
  const [statusFilter, setStatusFilter] = useState('All');

  const filtered = tasks.filter(t => {
    const catOk = categoryFilter === 'All' || t.category === categoryFilter;
    const statusOk =
      statusFilter === 'All' ||
      (statusFilter === 'Pending' && !t.completed) ||
      (statusFilter === 'Completed' && t.completed);
    return catOk && statusOk;
  });

  // Sort: incomplete first (high priority → medium → low), then completed
  const sorted = [...filtered].sort((a, b) => {
    if (a.completed !== b.completed) return a.completed ? 1 : -1;
    const pMap = { high: 0, medium: 1, low: 2 };
    return (pMap[a.priority] ?? 1) - (pMap[b.priority] ?? 1);
  });

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="bg-white rounded-xl border border-slate-200 p-3 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <Filter size={14} className="text-slate-500" />
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Filters</span>
        </div>
        <div className="space-y-2">
          {/* Status filter */}
          <div className="flex gap-1.5 flex-wrap">
            {FILTER_STATES.map(s => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`px-3 py-1 text-xs rounded-full font-medium transition-all ${
                  statusFilter === s
                    ? 'bg-teal-600 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
              >
                {s}
                {s === 'Pending' && stats.pending > 0 && (
                  <span className="ml-1 bg-white/30 px-1 rounded-full">{stats.pending}</span>
                )}
                {s === 'Completed' && stats.completed > 0 && (
                  <span className="ml-1 bg-white/30 px-1 rounded-full">{stats.completed}</span>
                )}
              </button>
            ))}
          </div>
          {/* Category filter */}
          <div className="flex gap-1.5 flex-wrap">
            {CATEGORIES.map(c => (
              <button
                key={c}
                onClick={() => setCategoryFilter(c)}
                className={`px-2 py-0.5 text-xs rounded-full font-medium border transition-all ${
                  categoryFilter === c
                    ? 'bg-teal-600 text-white border-teal-600'
                    : 'border-slate-200 text-slate-500 hover:border-teal-300 hover:text-teal-600'
                }`}
              >
                {c}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Add task */}
      <AddTask onAdd={onAdd} />

      {/* Task count */}
      {tasks.length > 0 && (
        <p className="text-xs text-slate-500 px-1">
          Showing {filtered.length} of {tasks.length} tasks
          {' · '}
          <span className="text-teal-600 font-medium">{stats.completionRate}% complete</span>
        </p>
      )}

      {/* Progress bar */}
      {tasks.length > 0 && (
        <div className="w-full bg-slate-100 rounded-full h-1.5">
          <div
            className="bg-teal-500 h-1.5 rounded-full transition-all duration-500"
            style={{ width: `${stats.completionRate}%` }}
          />
        </div>
      )}

      {/* Task items */}
      {sorted.length === 0 ? (
        <div className="text-center py-10 text-slate-400">
          <p className="text-3xl mb-2">📋</p>
          <p className="text-sm font-medium">
            {tasks.length === 0
              ? "No tasks yet — add one to get started!"
              : "No tasks match the current filters"}
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {sorted.map(task => (
            <li key={task.id}>
              <TaskItem
                task={task}
                onToggle={onToggle}
                onDelete={onDelete}
                onEdit={onEdit}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
