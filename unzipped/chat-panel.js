/* ============================================================
   ccg-chat-panel.js
   Chat panel — orchestrates all chat sub-components.
   This is the single file you import to get a working chat panel.

   Depends on:
     ccg-panel.js            → openPanel, closePanel, initPanelSwipe
     ccg-bubble-menu.js      → initBubbleMenu
     ccg-chat-bubble.js      → createBubble, createMeta, createDateStamp, nowTime
     ccg-chat-input.js       → initChatInput
     ccg-typing-indicator.js → showTyping, hideTyping

   Usage:
     import { initChatPanel, openChatPanel, closeChatPanel } from './ccg-chat-panel.js'

     // Call once after DOM ready
     initChatPanel()

     // Open from nav button or anywhere
     openChatPanel()

   ╔══════════════════════════════════════════════════════════╗
   ║  SUPABASE INTEGRATION MAP                                ║
   ║  Search "SUPABASE:" to find every hook                   ║
   ║  ─────────────────────────────────────────────────────── ║
   ║  1. Auth        → get current user ID                    ║
   ║  2. loadHistory → fetch past messages on open            ║
   ║  3. subscribe   → realtime new messages                  ║
   ║  4. sendMessage → insert new user message                ║
   ║  5. markRead    → mark admin messages read on open       ║
   ║  6. uploadImage → storage upload + message insert        ║
   ║  7. presence    → admin online/typing status             ║
   ║  8. theme       → persist theme preference               ║
   ╚══════════════════════════════════════════════════════════╝
   ============================================================ */

import { openPanel, closePanel, initPanelSwipe } from './panel.js';
import { initBubbleMenu }                         from './bubble-menu.js';
import { createBubble, createMeta, nowTime }      from './chat-bubble.js';
import { initChatInput }                          from './chat-input.js';
import { showTyping, hideTyping }                 from './typing-indic.js';

// ── Module state ─────────────────────────────────────────────
let _initialized = false;
let _bubbleMenu  = null;
let _replyIdx    = 0;

// SUPABASE 1: Auth — get current user
// ─────────────────────────────────────────────────────────────
// import { supabase } from './ccg-supabase.js'
// let USER_ID = null
//
// export async function authInit() {
//   const { data: { user } } = await supabase.auth.getUser()
//   USER_ID = user?.id
//   return USER_ID
// }

// ── Simulated admin replies ───────────────────────────────────
// REMOVE this array when Supabase realtime is connected.
const SIMULATED_REPLIES = [
  "got it — I'll look into that for you.",
  "noted. give me a moment.",
  "on it ✦",
  "sounds good. I'll follow up shortly.",
  "checking now — standby.",
  "already on it, actually.",
  "interesting — let me pull that up.",
];

// ── Admin online status ───────────────────────────────────────
// Controls the green dot + green LIVE CHAT text in the header.
// In production: driven by Supabase presence.
let _adminOnline = true; // hardcoded for demo

export function setAdminOnline(online) {
  _adminOnline = online;
  const breadcrumb = document.getElementById('chat-breadcrumb');
  const dot        = document.getElementById('online-dot');
  if (!breadcrumb) return;
  const color = online ? 'var(--online)' : 'var(--ink-mute)';
  breadcrumb.style.color = color;
  if (dot) dot.style.background = online ? 'var(--online)' : 'var(--ink-mute)';
}

// SUPABASE 7: Presence — watch admin online/typing status
// ─────────────────────────────────────────────────────────────
// let _typingRow = null
//
// function subscribePresence() {
//   supabase.channel('presence:admin')
//     .on('presence', { event: 'sync' }, () => {
//       const state   = supabase.channel('presence:admin').presenceState()
//       const admin   = Object.values(state).find(u => u[0]?.role === 'admin')
//       const online  = !!admin
//       const typing  = admin?.[0]?.typing ?? false
//       setAdminOnline(online)
//       const msgs = document.getElementById('chat-messages')
//       if (typing && !_typingRow) {
//         _typingRow = showTyping(msgs)
//         scrollToBottom()
//       } else if (!typing && _typingRow) {
//         hideTyping(_typingRow)
//         _typingRow = null
//       }
//     })
//     .subscribe()
// }

// ── initChatPanel — wire everything, call once ────────────────
export function initChatPanel() {
  if (_initialized) return;
  _initialized = true;

  // Panel swipe — drag header down to close
  initPanelSwipe('chat-panel', 'chat-header', 'panel-scrim', () => {
    document.getElementById('status-label').textContent = 'home · B';
    _bubbleMenu?.close();
  });

  // Bubble menu — + button reveals upload + save icons
  _bubbleMenu = initBubbleMenu({
    triggerId: 'header-plus',
    menuId:    'bubble-menu',
    buttons:   ['save-btn', 'upload-btn'],
    panelId:   'chat-panel',
    titleId:   'panel-title',
  });

  // Close button
  document.getElementById('chat-close').addEventListener('click', e => {
    e.stopPropagation();
    _bubbleMenu?.close();
    closeChatPanel();
  });

  // Scrim tap closes
  document.getElementById('panel-scrim').addEventListener('click', () => {
    _bubbleMenu?.close();
    closeChatPanel();
  });

  // Upload image button
  document.getElementById('upload-btn').addEventListener('click', e => {
    e.stopPropagation();
    _bubbleMenu?.close();
    document.getElementById('file-input').click();
  });

  document.getElementById('file-input').addEventListener('change', function() {
    if (!this.files?.[0]) return;
    const file = this.files[0];

    // SUPABASE 6: Upload image to storage, insert message row with URL
    // ───────────────────────────────────────────────────────────────
    // async function handleImageUpload(file) {
    //   const path = `chat/${USER_ID}/${Date.now()}-${file.name}`
    //   const { error: uploadErr } = await supabase.storage
    //     .from('chat-attachments')
    //     .upload(path, file)
    //   if (uploadErr) { console.error(uploadErr); return }
    //   const { data: { publicUrl } } = supabase.storage
    //     .from('chat-attachments')
    //     .getPublicUrl(path)
    //   await supabase.from('chat_messages').insert({
    //     user_id:     USER_ID,
    //     content:     publicUrl,
    //     type:        'image',
    //     sender_role: 'user',
    //     read:        false,
    //   })
    // }
    // handleImageUpload(file)

    // Local fallback
    const msgs = document.getElementById('chat-messages');
    msgs.appendChild(createBubble('📎 ' + file.name, 'user'));
    msgs.appendChild(createMeta('sent · ' + nowTime(), 'user'));
    scrollToBottom();
    this.value = '';
  });

  // Save transcript
  document.getElementById('save-btn').addEventListener('click', e => {
    e.stopPropagation();
    _bubbleMenu?.close();
    downloadTranscript();
  });

  // Chat input — send on Enter or button tap
  initChatInput({
    inputId: 'chat-input',
    sendId:  'send-btn',
    onSend:  handleSend,
  });

  // SUPABASE 7: Subscribe to presence
  // subscribePresence()
}

// ── openChatPanel ─────────────────────────────────────────────
export function openChatPanel() {
  openPanel('chat-panel', 'panel-scrim');
  document.getElementById('status-label').textContent = 'chat · live';
  scrollToBottom();

  // SUPABASE 2: Load message history on first open
  // ───────────────────────────────────────────────
  // if (!_historyLoaded) {
  //   _historyLoaded = true
  //   loadHistory()
  //   subscribeMessages()
  //   markRead()
  // }
}

// ── closeChatPanel ────────────────────────────────────────────
export function closeChatPanel() {
  closePanel('chat-panel', 'panel-scrim', () => {
    document.getElementById('status-label').textContent = 'home · B';
  });
}

// ── handleSend — called by initChatInput onSend ───────────────
function handleSend(text) {
  const msgs = document.getElementById('chat-messages');
  const t    = nowTime();

  msgs.appendChild(createBubble(text, 'user'));
  const meta = createMeta('sent · ' + t, 'user');
  msgs.appendChild(meta);
  scrollToBottom();

  // SUPABASE 4: Insert user message
  // ───────────────────────────────
  // await supabase.from('chat_messages').insert({
  //   user_id:     USER_ID,
  //   content:     text,
  //   type:        'text',
  //   sender_role: 'user',
  //   read:        false,
  // })

  // Optimistic read receipt
  // REMOVE when real read receipts arrive via realtime subscription
  setTimeout(() => { meta.textContent = 'read ✓ · ' + t; }, 1200);

  // REMOVE: simulated reply — replaced by SUPABASE 3 realtime subscription
  setTimeout(() => _simulatedReply(msgs), 1800);
}

// SUPABASE 2: Load message history
// ─────────────────────────────────────────────────────────────
// let _historyLoaded = false
//
// async function loadHistory() {
//   const { data, error } = await supabase
//     .from('chat_messages')
//     .select('id, content, type, sender_role, created_at, read')
//     .eq('user_id', USER_ID)
//     .order('created_at', { ascending: true })
//   if (error) { console.error(error); return }
//   const msgs = document.getElementById('chat-messages')
//   let lastDate = null
//   data.forEach(msg => {
//     const date = formatDate(msg.created_at)
//     if (date !== lastDate) {
//       msgs.appendChild(createDateStamp(date))
//       lastDate = date
//     }
//     const type = msg.sender_role === 'admin' ? 'admin' : 'user'
//     msgs.appendChild(createBubble(msg.content, type))
//     msgs.appendChild(createMeta(formatTime(msg.created_at), type === 'admin' ? 'admin' : ''))
//   })
//   scrollToBottom()
// }

// SUPABASE 3: Realtime new message subscription
// ─────────────────────────────────────────────────────────────
// function subscribeMessages() {
//   supabase.channel('chat:' + USER_ID)
//     .on('postgres_changes', {
//       event:  'INSERT',
//       schema: 'public',
//       table:  'chat_messages',
//       filter: `user_id=eq.${USER_ID}`,
//     }, payload => {
//       const msg = payload.new
//       if (msg.sender_role !== 'admin') return
//       const msgs = document.getElementById('chat-messages')
//       msgs.appendChild(createBubble(msg.content, 'admin'))
//       msgs.appendChild(createMeta(formatTime(msg.created_at), 'admin'))
//       scrollToBottom()
//     })
//     .subscribe()
// }

// SUPABASE 5: Mark admin messages as read when panel opens
// ─────────────────────────────────────────────────────────────
// async function markRead() {
//   await supabase.from('chat_messages')
//     .update({ read: true })
//     .eq('user_id', USER_ID)
//     .eq('sender_role', 'admin')
//     .eq('read', false)
// }

// ── _simulatedReply — REMOVE when realtime is live ────────────
function _simulatedReply(msgs) {
  const typingRow = showTyping(msgs);
  scrollToBottom();
  setTimeout(() => {
    hideTyping(typingRow);
    const text = SIMULATED_REPLIES[_replyIdx % SIMULATED_REPLIES.length];
    _replyIdx++;
    msgs.appendChild(createBubble(text, 'admin'));
    msgs.appendChild(createMeta(nowTime(), 'admin'));
    scrollToBottom();
  }, 1600);
}

// ── downloadTranscript ────────────────────────────────────────
function downloadTranscript() {
  const msgs  = document.getElementById('chat-messages');
  const nodes = msgs.querySelectorAll('.bubble, .chat-date-stamp, .bubble-meta');
  const lines = [];
  const today = new Date().toLocaleDateString('en-US', {
    year: 'numeric', month: 'long', day: 'numeric'
  });

  lines.push('CCG CHAT TRANSCRIPT');
  lines.push('exported ' + today);
  lines.push('----------------------------------------');
  lines.push('');

  nodes.forEach(el => {
    if (el.classList.contains('chat-date-stamp')) {
      lines.push(''); lines.push('── ' + el.textContent.trim() + ' ──'); lines.push('');
    } else if (el.classList.contains('bubble')) {
      const prefix = el.classList.contains('user') ? 'you:   ' : 'admin: ';
      lines.push(prefix + el.textContent.trim());
    } else if (el.classList.contains('bubble-meta')) {
      lines.push('       ' + el.textContent.trim()); lines.push('');
    }
  });

  lines.push(''); lines.push('----------------------------------------'); lines.push('end of transcript');

  const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url;
  a.download = 'ccg-chat-' + Date.now() + '.txt';
  document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  const stamp = document.createElement('div');
  stamp.className   = 'chat-date-stamp';
  stamp.textContent = 'transcript saved ↓';
  msgs.appendChild(stamp);
  scrollToBottom();
}

// ── Utility ───────────────────────────────────────────────────
function scrollToBottom() {
  const m = document.getElementById('chat-messages');
  if (m) setTimeout(() => { m.scrollTop = m.scrollHeight; }, 60);
}
