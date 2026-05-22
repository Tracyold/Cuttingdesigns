/* ============================================================
   ccg-typing-indicator.js
   Animated three-dot typing indicator — shown while admin is typing.

   Usage:
     import { showTyping, hideTyping } from './ccg-typing-indicator.js'

     const row = showTyping(messagesEl)  // appends typing row
     hideTyping(row)                      // replaces with real bubble

   SUPABASE: wire to presence channel typing events
     channel.on('presence', { event: 'sync' }, () => {
       const state = channel.presenceState()
       const adminTyping = Object.values(state).some(u => u[0]?.typing)
       adminTyping ? showTyping(msgsEl) : hideTyping(currentTypingRow)
     })

   Future custom element:
     <ccg-typing-indicator visible="true" />
   ============================================================ */

export function showTyping(container) {
  const row = document.createElement('div');
  row.className = 'bubble-row admin';
  row.id        = 'typing-indicator';
  row.style.marginTop = '6px';
  row.innerHTML = `
    <div class="bubble admin" style="display:flex;gap:5px;align-items:center;padding:14px 18px;">
      <span class="dot"></span>
      <span class="dot"></span>
      <span class="dot"></span>
    </div>`;
  container.appendChild(row);
  return row;
}

export function hideTyping(row) {
  if (row && row.parentNode) row.parentNode.removeChild(row);
}
