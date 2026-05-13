import { useState, useEffect } from 'react';
import { format } from 'date-fns';
import { loadNotes, saveNotes } from '../utils/storage';
import { AlertTriangle, Save } from 'lucide-react';

const TODAY = format(new Date(), 'yyyy-MM-dd');

const NOTE_TEMPLATES = [
  { label: '📋 Morning Briefing', template: '## Morning Briefing\n\n**Top priorities today:**\n- \n- \n- \n\n**Key reminders:**\n- \n\n**Follow-ups needed:**\n- ' },
  { label: '🔧 Equipment Log', template: '## Equipment Log\n\n**Equipment checked:**\n- \n\n**Issues noted:**\n- \n\n**Maintenance needed:**\n- ' },
  { label: '📦 Supply Notes', template: '## Supply Notes\n\n**Items to order:**\n- \n\n**Items received:**\n- \n\n**Stock concerns:**\n- ' },
  { label: '📚 Education/Learning', template: '## Learning Notes\n\n**Topic:**\n\n**Key takeaways:**\n- \n- \n\n**Action items:**\n- ' },
  { label: '🤝 Meeting Notes', template: '## Meeting Notes\n\n**Meeting topic:**\n\n**Attendees:**\n\n**Key decisions:**\n- \n\n**Action items:**\n- ' },
  { label: '🔄 End-of-Day Wrap', template: '## End-of-Day Wrap\n\n**Accomplished today:**\n- \n- \n\n**Carrying over to tomorrow:**\n- \n\n**One win today:**\n\n**One thing to improve:**\n' },
];

export default function Notes() {
  const [notes, setNotes] = useState(() => {
    const all = loadNotes();
    return all[TODAY] || '';
  });
  const [saved, setSaved] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);

  useEffect(() => {
    const timeout = setTimeout(() => {
      const all = loadNotes();
      all[TODAY] = notes;
      saveNotes(all);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    }, 800);
    return () => clearTimeout(timeout);
  }, [notes]);

  function applyTemplate(template) {
    setNotes(prev => prev ? prev + '\n\n' + template : template);
    setShowTemplates(false);
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-bold text-slate-800">Today's Notes</h2>
          <p className="text-sm text-slate-500">{format(new Date(), 'EEEE, MMMM d, yyyy')}</p>
        </div>
        <div className="flex items-center gap-2">
          {saved && (
            <span className="flex items-center gap-1 text-xs text-green-600">
              <Save size={13} /> Saved
            </span>
          )}
          <button
            onClick={() => setShowTemplates(!showTemplates)}
            className="text-sm px-3 py-1.5 border border-teal-300 text-teal-700 rounded-lg hover:bg-teal-50 transition-colors font-medium"
          >
            + Template
          </button>
        </div>
      </div>

      {/* PHI Warning */}
      <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 flex items-start gap-2">
        <AlertTriangle size={15} className="text-amber-600 mt-0.5 shrink-0" />
        <p className="text-xs text-amber-700">
          <strong>Important:</strong> This notes section is for clinic operations only. Do not enter any Protected Health Information (PHI) such as patient names, dates of birth, diagnoses, or treatment details.
        </p>
      </div>

      {/* Templates */}
      {showTemplates && (
        <div className="bg-white rounded-xl border border-slate-200 p-3 shadow-sm">
          <p className="text-xs text-slate-500 font-semibold uppercase tracking-wide mb-2">Choose a template</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {NOTE_TEMPLATES.map(t => (
              <button
                key={t.label}
                onClick={() => applyTemplate(t.template)}
                className="text-xs text-left px-3 py-2 border border-slate-200 rounded-lg hover:border-teal-300 hover:bg-teal-50 transition-colors text-slate-600"
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Textarea */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <textarea
          value={notes}
          onChange={e => setNotes(e.target.value)}
          placeholder="Write your clinic notes here... Use templates above for structured notes.&#10;&#10;Remember: No patient names, DOBs, diagnoses, or other PHI."
          className="w-full h-[calc(100vh-360px)] min-h-[350px] p-4 text-sm text-slate-700 resize-none focus:outline-none font-mono leading-relaxed"
        />
        <div className="border-t border-slate-100 px-4 py-2 flex items-center justify-between">
          <span className="text-xs text-slate-400">{notes.length} characters</span>
          <span className="text-xs text-slate-400">Auto-saves as you type</span>
        </div>
      </div>
    </div>
  );
}
