import { useState } from 'react';
import { Plus, X } from 'lucide-react';

const CATEGORIES = ['Admin', 'Equipment', 'Education', 'Personal', 'Meeting', 'Supply', 'Other'];
const PRIORITIES = [
  { value: 'high', label: 'High', color: 'text-red-600 bg-red-50 border-red-200' },
  { value: 'medium', label: 'Medium', color: 'text-yellow-600 bg-yellow-50 border-yellow-200' },
  { value: 'low', label: 'Low', color: 'text-green-600 bg-green-50 border-green-200' },
];

export default function AddTask({ onAdd }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [category, setCategory] = useState('Admin');
  const [priority, setPriority] = useState('medium');

  function handleSubmit(e) {
    e.preventDefault();
    if (!text.trim()) return;
    onAdd(text, category, priority);
    setText('');
    setCategory('Admin');
    setPriority('medium');
    setOpen(false);
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-full flex items-center gap-2 p-3 border-2 border-dashed border-slate-300 rounded-xl text-slate-500 hover:border-teal-400 hover:text-teal-600 hover:bg-teal-50/40 transition-all text-sm font-medium"
      >
        <Plus size={18} />
        Add new task
      </button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-white rounded-xl border-2 border-teal-300 shadow-md p-4 space-y-3"
    >
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-slate-700 text-sm">New Task</h3>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-slate-400 hover:text-slate-600 p-1 rounded"
        >
          <X size={16} />
        </button>
      </div>

      {/* Task text */}
      <input
        type="text"
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder="What needs to be done? (no patient info)"
        className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-teal-400 focus:ring-2 focus:ring-teal-100"
        autoFocus
        maxLength={200}
      />

      <div className="flex gap-3 flex-wrap">
        {/* Category */}
        <div className="flex-1 min-w-32">
          <label className="block text-xs text-slate-500 mb-1 font-medium">Category</label>
          <select
            value={category}
            onChange={e => setCategory(e.target.value)}
            className="w-full border border-slate-200 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:border-teal-400 bg-white"
          >
            {CATEGORIES.map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>

        {/* Priority */}
        <div className="flex-1 min-w-40">
          <label className="block text-xs text-slate-500 mb-1 font-medium">Priority</label>
          <div className="flex gap-1.5">
            {PRIORITIES.map(p => (
              <button
                key={p.value}
                type="button"
                onClick={() => setPriority(p.value)}
                className={`flex-1 text-xs py-1.5 px-2 rounded-lg border font-medium transition-all ${
                  priority === p.value ? p.color + ' ring-2 ring-offset-1' : 'text-slate-500 border-slate-200 hover:border-slate-300'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* PHI reminder */}
      <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-1.5">
        🔒 Reminder: Do not enter any patient health information (PHI)
      </p>

      <div className="flex gap-2 justify-end">
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="px-3 py-1.5 text-sm text-slate-500 hover:bg-slate-100 rounded-lg transition-colors"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={!text.trim()}
          className="px-4 py-1.5 bg-teal-600 text-white text-sm font-medium rounded-lg hover:bg-teal-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          Add Task
        </button>
      </div>
    </form>
  );
}
