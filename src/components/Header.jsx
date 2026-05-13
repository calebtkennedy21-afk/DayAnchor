import { format } from 'date-fns';
import { Anchor, Settings } from 'lucide-react';

const VIEWS = ['Dashboard', 'Tasks', 'AI Assistant', 'Notes', 'Review'];
const VIEW_ICONS = { Dashboard: '🏠', Tasks: '✅', 'AI Assistant': '🤖', Notes: '📝', Review: '📊' };

export default function Header({ activeView, setActiveView, onSettingsClick }) {
  const today = format(new Date(), 'EEEE, MMMM d, yyyy');

  return (
    <header className="bg-white border-b border-slate-200 shadow-sm sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4">
        {/* Top bar */}
        <div className="flex items-center justify-between py-3">
          <div className="flex items-center gap-2">
            <div className="bg-teal-600 text-white p-1.5 rounded-lg">
              <Anchor size={20} />
            </div>
            <div>
              <h1 className="text-xl font-bold text-slate-800 leading-none">DayAnchor</h1>
              <p className="text-xs text-slate-500">Orthopedic Clinic Productivity</p>
            </div>
          </div>
          <div className="hidden sm:block text-sm text-slate-500 font-medium">{today}</div>
          <button
            onClick={onSettingsClick}
            className="p-2 text-slate-500 hover:text-teal-600 hover:bg-teal-50 rounded-lg transition-colors"
            title="Settings"
          >
            <Settings size={20} />
          </button>
        </div>

        {/* Navigation tabs */}
        <nav className="flex gap-1 pb-0">
          {VIEWS.map(view => (
            <button
              key={view}
              onClick={() => setActiveView(view)}
              className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-t-lg border-b-2 transition-all ${
                activeView === view
                  ? 'border-teal-600 text-teal-700 bg-teal-50'
                  : 'border-transparent text-slate-500 hover:text-slate-700 hover:bg-slate-50'
              }`}
            >
              <span>{VIEW_ICONS[view]}</span>
              <span className="hidden sm:inline">{view}</span>
            </button>
          ))}
        </nav>
      </div>
    </header>
  );
}
