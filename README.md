# DayAnchor
Daily productivity app for both personal and clinic responsibilities with AI incorporation

## Overview

**DayAnchor** is a browser-based daily productivity application designed for healthcare professionals working in an outpatient orthopedic clinic environment. It helps you stay organized, efficient, and focused — without ever touching Protected Health Information (PHI).

## Features

### 📊 Dashboard
- Daily greeting with date/time context
- At-a-glance task stats (total, completed, pending, completion rate)
- AI-powered tip of the day (refreshable)
- High-priority task overview
- Tasks by category breakdown
- Recently completed tasks

### ✅ Task Management
- Add tasks with categories: **Admin**, **Equipment**, **Education**, **Personal**, **Meeting**, **Supply**, **Other**
- Set priority levels: **High**, **Medium**, **Low**
- Filter by status (All / Pending / Completed) and category
- In-line task editing
- PHI reminder on every task creation form

### 🤖 AI Assistant (GPT-4o mini)
- Chat interface powered by OpenAI's GPT-4o mini
- Pre-seeded with clinic-specific productivity context
- Quick-prompt suggestions for common queries
- Strict no-PHI prompt design
- Privacy reminder banner

### 📝 Daily Notes
- Auto-saving notes editor (per day)
- 6 structured templates:
  - Morning Briefing
  - Equipment Log
  - Supply Notes
  - Education/Learning
  - Meeting Notes
  - End-of-Day Wrap
- PHI warning prominently displayed

### 📈 Productivity Review
- 7-day completion bar chart
- Daily streak tracker 🔥
- Category performance breakdown
- Motivational feedback

### ⚙️ Settings
- OpenAI API key management (stored locally, never sent to any server except OpenAI)
- Privacy & HIPAA compliance notes

## Privacy & HIPAA

- **No PHI is ever collected or stored** — the app is designed for operational/administrative use only
- All data is stored in browser `localStorage` — no backend, no database
- API calls go only to OpenAI when using AI features
- PHI reminders appear throughout the UI

## Getting Started

### Prerequisites
- Node.js 18+
- An OpenAI API key (for AI features)

### Install & Run

```bash
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

### Configure AI
1. Click the ⚙️ Settings icon in the top-right
2. Enter your OpenAI API key (`sk-...`)
3. Click Save Settings

### Build for Production
```bash
npm run build
npm run preview
```

## Tech Stack

- **React 19** + Vite 8
- **Tailwind CSS v4**
- **OpenAI API** (GPT-4o mini)
- **date-fns** for date formatting
- **lucide-react** for icons
- **localStorage** for data persistence

