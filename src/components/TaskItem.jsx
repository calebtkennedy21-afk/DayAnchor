import { useState } from 'react';
import { CheckCircle2, Circle, Trash2, Pencil, Check, X } from 'lucide-react';

const CATEGORY_COLORS = {
  Admin: 'bg-blue-100 text-blue-700',
  Equipment: 'bg-orange-100 text-orange-700',
  Education: 'bg-purple-100 text-purple-700',
  Personal: 'bg-green-100 text-green-700',
  Meeting: 'bg-pink-100 text-pink-700',
  Supply: 'bg-yellow-100 text-yellow-700',
  Other: 'bg-slate-100 text-slate-600',
};

const PRIORITY_DOTS = {
  high: 'bg-red-500',
  medium: 'bg-yellow-400',
  low: 'bg-green-400',
};

export default function TaskItem({ task, onToggle, onDelete, onEdit }) {
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(task.text);

  function saveEdit() {
    if (editText.trim() && editText.trim() !== task.text) {
      onEdit(task.id, { text: editText.trim() });
    }
    setEditing(false);
  }

  function cancelEdit() {
    setEditText(task.text);
    setEditing(false);
  }

  return (
    <div
      className={`group flex items-start gap-3 bg-white rounded-xl border shadow-sm p-3 transition-all hover:shadow-md ${
        task.completed ? 'opacity-70' : 'border-slate-200'
      }`}
    >
      {/* Priority indicator */}
      <div className="flex flex-col items-center gap-1 mt-0.5">
        <span className={`w-2 h-2 rounded-full shrink-0 ${PRIORITY_DOTS[task.priority]}`} title={`${task.priority} priority`} />
      </div>

      {/* Checkbox */}
      <button
        onClick={() => onToggle(task.id)}
        className="mt-0.5 shrink-0 text-slate-400 hover:text-teal-600 transition-colors"
        title={task.completed ? 'Mark incomplete' : 'Mark complete'}
      >
        {task.completed
          ? <CheckCircle2 size={20} className="text-teal-500" />
          : <Circle size={20} />
        }
      </button>

      {/* Content */}
      <div className="flex-1 min-w-0">
        {editing ? (
          <div className="flex items-center gap-2">
            <input
              value={editText}
              onChange={e => setEditText(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
              className="flex-1 border border-teal-300 rounded px-2 py-0.5 text-sm focus:outline-none focus:ring-2 focus:ring-teal-100"
              autoFocus
              maxLength={200}
            />
            <button onClick={saveEdit} className="text-teal-600 hover:text-teal-800">
              <Check size={15} />
            </button>
            <button onClick={cancelEdit} className="text-slate-400 hover:text-slate-600">
              <X size={15} />
            </button>
          </div>
        ) : (
          <p className={`text-sm leading-snug ${task.completed ? 'line-through text-slate-400' : 'text-slate-700'}`}>
            {task.text}
          </p>
        )}
        <div className="flex items-center gap-2 mt-1.5">
          <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${CATEGORY_COLORS[task.category] || CATEGORY_COLORS.Other}`}>
            {task.category}
          </span>
          {task.completedAt && (
            <span className="text-xs text-slate-400">
              ✓ {new Date(task.completedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
        {!task.completed && !editing && (
          <button
            onClick={() => setEditing(true)}
            className="p-1.5 text-slate-400 hover:text-teal-600 hover:bg-teal-50 rounded-lg transition-colors"
            title="Edit"
          >
            <Pencil size={14} />
          </button>
        )}
        <button
          onClick={() => onDelete(task.id)}
          className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
          title="Delete"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}
