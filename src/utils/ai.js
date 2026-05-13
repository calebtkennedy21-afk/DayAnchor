import OpenAI from 'openai';

// Builds a DayAnchor-focused system prompt
export function buildSystemPrompt() {
  return `You are DayAnchor AI, a daily productivity assistant for a healthcare professional who works in an outpatient orthopedic clinic with a busy foot and ankle surgeon.

Your role is to help with:
- Daily task planning and prioritization
- Clinic workflow efficiency (scheduling, supply management, equipment, staff coordination)
- Personal productivity and work-life balance
- End-of-day reflections and next-day planning
- Professional development in the orthopedic/podiatric space

IMPORTANT RULES:
- NEVER ask for, store, or discuss any patient health information (PHI)
- NEVER reference individual patients or patient-specific details
- Keep all advice focused on workflow, processes, and professional development
- Be concise, practical, and supportive
- Use medical-friendly language but remain accessible

When helping with tasks, focus on: administrative workflows, team communication, supply/inventory management, equipment maintenance scheduling, staff education, continuing education, and personal productivity.`;
}

export async function sendMessageToAI(apiKey, messages, userMessage) {
  if (!apiKey || !apiKey.trim()) {
    throw new Error('Please add your OpenAI API key in Settings to use the AI Assistant.');
  }

  const client = new OpenAI({ apiKey: apiKey.trim(), dangerouslyAllowBrowser: true });

  const conversation = [
    { role: 'system', content: buildSystemPrompt() },
    ...messages.map(m => ({ role: m.role, content: m.content })),
    { role: 'user', content: userMessage },
  ];

  const response = await client.chat.completions.create({
    model: 'gpt-4o-mini',
    messages: conversation,
    max_tokens: 600,
    temperature: 0.7,
  });

  return response.choices[0].message.content;
}

export async function getDailySuggestion(apiKey, tasks, date) {
  if (!apiKey || !apiKey.trim()) return null;

  const client = new OpenAI({ apiKey: apiKey.trim(), dangerouslyAllowBrowser: true });

  const taskSummary = tasks.length > 0
    ? tasks.map(t => `- [${t.completed ? 'done' : 'pending'}] (${t.category}) ${t.text}`).join('\n')
    : 'No tasks added yet.';

  const prompt = `Today is ${date}. Here are the current tasks for this clinic day:\n\n${taskSummary}\n\nProvide a brief, encouraging daily productivity tip (2-3 sentences max) for someone working in a busy foot and ankle orthopedic clinic. Focus on workflow efficiency or well-being. No patient information needed.`;

  const response = await client.chat.completions.create({
    model: 'gpt-4o-mini',
    messages: [
      { role: 'system', content: buildSystemPrompt() },
      { role: 'user', content: prompt },
    ],
    max_tokens: 150,
    temperature: 0.8,
  });

  return response.choices[0].message.content;
}
