import { useState, useEffect, useCallback } from 'react';
import { format } from 'date-fns';
import { loadTasks, saveTasks, loadHistory, saveHistory } from '../utils/storage';

const TODAY = format(new Date(), 'yyyy-MM-dd');

export function useTasks() {
  const [allTasks, setAllTasks] = useState(() => loadTasks());

  // Persist on every change
  useEffect(() => {
    saveTasks(allTasks);
  }, [allTasks]);

  // Today's tasks only
  const tasks = allTasks.filter(t => t.date === TODAY);

  // Archive yesterday's incomplete tasks
  useEffect(() => {
    const history = loadHistory();
    const yesterday = allTasks.filter(t => t.date !== TODAY);
    if (yesterday.length > 0) {
      const existing = new Set(history.map(h => h.id));
      const newHistory = [...history, ...yesterday.filter(t => !existing.has(t.id))];
      saveHistory(newHistory);
    }
    // Run only on mount to archive past tasks once per session.
    // allTasks is read from localStorage at that point, so no stale closure issue.
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const addTask = useCallback((text, category, priority = 'medium') => {
    const task = {
      id: Date.now().toString(),
      text: text.trim(),
      category,
      priority,
      completed: false,
      date: TODAY,
      createdAt: new Date().toISOString(),
    };
    setAllTasks(prev => [...prev, task]);
    return task;
  }, []);

  const toggleTask = useCallback((id) => {
    setAllTasks(prev =>
      prev.map(t =>
        t.id === id
          ? { ...t, completed: !t.completed, completedAt: !t.completed ? new Date().toISOString() : null }
          : t
      )
    );
  }, []);

  const deleteTask = useCallback((id) => {
    setAllTasks(prev => prev.filter(t => t.id !== id));
  }, []);

  const editTask = useCallback((id, updates) => {
    setAllTasks(prev => prev.map(t => t.id === id ? { ...t, ...updates } : t));
  }, []);

  const reorderTasks = useCallback((fromIndex, toIndex) => {
    setAllTasks(prev => {
      const todayTasks = prev.filter(t => t.date === TODAY);
      const otherTasks = prev.filter(t => t.date !== TODAY);
      const reordered = [...todayTasks];
      const [moved] = reordered.splice(fromIndex, 1);
      reordered.splice(toIndex, 0, moved);
      return [...reordered, ...otherTasks];
    });
  }, []);

  const stats = {
    total: tasks.length,
    completed: tasks.filter(t => t.completed).length,
    pending: tasks.filter(t => !t.completed).length,
    completionRate: tasks.length > 0
      ? Math.round((tasks.filter(t => t.completed).length / tasks.length) * 100)
      : 0,
    byCategory: tasks.reduce((acc, t) => {
      acc[t.category] = (acc[t.category] || 0) + 1;
      return acc;
    }, {}),
  };

  return { tasks, stats, addTask, toggleTask, deleteTask, editTask, reorderTasks };
}
