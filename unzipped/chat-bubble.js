/* ============================================================
   ccg-chat-bubble.js
   Chat bubble and meta timestamp factory functions.
   Used by ChatPanel to render messages from any source.

   Usage:
     import { createBubble, createMeta, createDateStamp } from './ccg-chat-bubble.js'

     messages.appendChild(createBubble('hello', 'user'))
     messages.appendChild(createMeta('read ✓ · 14:05', 'user'))
     messages.appendChild(createDateStamp('may 20 · today'))

   Future custom element:
     <ccg-chat-bubble type="user">yes, please. minimize loss.</ccg-chat-bubble>
     <ccg-chat-bubble type="admin">on it. WO-44 created.</ccg-chat-bubble>
   ============================================================ */

/**
 * createBubble — builds a bubble row div
 * @param {string} text  — message content
 * @param {string} type  — 'user' | 'admin'
 * @returns {HTMLElement}
 */
export function createBubble(text, type) {
  const row = document.createElement('div');
  row.className = `bubble-row ${type}`;
  row.style.marginTop = '6px';
  const b = document.createElement('div');
  b.className   = `bubble ${type}`;
  b.textContent = text;
  row.appendChild(b);
  return row;
}

/**
 * createMeta — builds a timestamp line below a bubble
 * @param {string} text   — e.g. 'read ✓ · 14:05'
 * @param {string} align  — 'admin' | '' (user = right-aligned by default)
 * @returns {HTMLElement}
 */
export function createMeta(text, align) {
  const m = document.createElement('div');
  m.className   = `bubble-meta${align === 'admin' ? ' admin' : ''}`;
  m.textContent = text;
  return m;
}

/**
 * createDateStamp — centered date separator between message groups
 * @param {string} text — e.g. 'may 20 · today'
 * @returns {HTMLElement}
 */
export function createDateStamp(text) {
  const d = document.createElement('div');
  d.className   = 'chat-date-stamp';
  d.textContent = text;
  return d;
}

/**
 * nowTime — current HH:MM string
 * @returns {string}
 */
export function nowTime() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

/**
 * formatTime — format an ISO timestamp to HH:MM
 * Used when rendering messages from Supabase
 * @param {string} iso
 * @returns {string}
 */
export function formatTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

/**
 * formatDate — format ISO to 'may 20' style date stamp
 * @param {string} iso
 * @returns {string}
 */
export function formatDate(iso) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }).toLowerCase();
}
