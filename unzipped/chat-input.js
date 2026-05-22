/* ============================================================
   ccg-chat-input.js
   Chat input bar — auto-resize textarea, send on Enter.

   Usage:
     import { initChatInput } from './ccg-chat-input.js'
     initChatInput({
       inputId:  'chat-input',
       sendId:   'send-btn',
       onSend:   (text) => handleSend(text),
     })

   Future custom element:
     <ccg-chat-input placeholder="message admin..." />
     element.addEventListener('ccg-send', e => handleSend(e.detail.text))
   ============================================================ */

export function initChatInput({ inputId, sendId, onSend }) {
  const input = document.getElementById(inputId);
  const btn   = document.getElementById(sendId);
  if (!input || !btn) return;

  // Auto-resize as user types
  input.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 80) + 'px';
  });

  // Send on Enter (Shift+Enter = newline)
  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  btn.addEventListener('click', send);

  function send() {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    input.style.height = 'auto';
    if (onSend) onSend(text);
  }
}
