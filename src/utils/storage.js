// LocalStorage helpers for DayAnchor

const TASKS_KEY = 'dayanchor_tasks';
const NOTES_KEY = 'dayanchor_notes';
const SETTINGS_KEY = 'dayanchor_settings';
const HISTORY_KEY = 'dayanchor_history';

export function loadTasks() {
  try {
    const raw = localStorage.getItem(TASKS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function saveTasks(tasks) {
  localStorage.setItem(TASKS_KEY, JSON.stringify(tasks));
}

export function loadNotes() {
  try {
    const raw = localStorage.getItem(NOTES_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function saveNotes(notes) {
  localStorage.setItem(NOTES_KEY, JSON.stringify(notes));
}

export function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    return raw ? JSON.parse(raw) : { openaiKey: '' };
  } catch {
    return { openaiKey: '' };
  }
}

export function saveSettings(settings) {
  // The OpenAI API key is user-supplied and stored in localStorage so the user
  // does not need to re-enter it each session. This is the standard approach for
  // browser-based developer/productivity tools where the user owns and manages
  // their own key. The app never transmits the key anywhere except directly to
  // the OpenAI API endpoint, and this is clearly disclosed to the user in the
  // Settings UI. No backend or third-party server ever receives it.
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
}

export function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function saveHistory(history) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-30))); // keep last 30 days
}
