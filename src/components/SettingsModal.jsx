import { useState } from 'react';
import { X, Eye, EyeOff, ExternalLink } from 'lucide-react';
import { saveSettings } from '../utils/storage';

export default function SettingsModal({ settings, onClose, onSave }) {
  const [apiKey, setApiKey] = useState(settings.openaiKey || '');
  const [showKey, setShowKey] = useState(false);

  function handleSave() {
    const updated = { ...settings, openaiKey: apiKey.trim() };
    saveSettings(updated);
    onSave(updated);
    onClose();
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-slate-100">
          <h2 className="text-lg font-bold text-slate-800">Settings</h2>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Content */}
        <div className="p-5 space-y-5">
          {/* OpenAI API Key */}
          <div>
            <label className="block text-sm font-semibold text-slate-700 mb-1">
              OpenAI API Key
            </label>
            <p className="text-xs text-slate-500 mb-2">
              Required for the AI Assistant and daily tips. Your key is stored locally in your browser only.
            </p>
            <div className="relative">
              <input
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder="sk-..."
                className="w-full border border-slate-200 rounded-xl px-3 py-2.5 pr-10 text-sm focus:outline-none focus:border-teal-400 focus:ring-2 focus:ring-teal-100 font-mono"
              />
              <button
                type="button"
                onClick={() => setShowKey(!showKey)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
              >
                {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <a
              href="https://platform.openai.com/api-keys"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-teal-600 hover:underline mt-1.5"
            >
              <ExternalLink size={11} />
              Get an OpenAI API key
            </a>
          </div>

          {/* Privacy notice */}
          <div className="bg-slate-50 rounded-xl p-3 border border-slate-200">
            <h3 className="text-xs font-semibold text-slate-600 mb-1.5">🔒 Privacy & HIPAA Compliance</h3>
            <ul className="text-xs text-slate-500 space-y-1">
              <li>• All data is stored locally in your browser only</li>
              <li>• No data is sent to external servers (except OpenAI when using AI features)</li>
              <li>• Never enter patient health information (PHI) anywhere in this app</li>
              <li>• This app is designed for operational/administrative use only</li>
            </ul>
          </div>

          {/* App info */}
          <div className="text-xs text-slate-400 text-center">
            DayAnchor v1.0 · Orthopedic Clinic Productivity Tool
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 pb-5">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-slate-500 hover:bg-slate-100 rounded-xl transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="px-4 py-2 bg-teal-600 text-white text-sm font-medium rounded-xl hover:bg-teal-700 transition-colors"
          >
            Save Settings
          </button>
        </div>
      </div>
    </div>
  );
}
