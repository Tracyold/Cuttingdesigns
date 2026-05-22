/* ============================================================
   ccg-panel.js
   Base panel system — open, close, swipe-down-to-close.
   Shared by ALL panels: chat, invoices, service requests, etc.

   Usage:
     import { openPanel, closePanel, initPanelSwipe } from './ccg-panel.js'

     openPanel('my-panel', 'panel-scrim')
     closePanel('my-panel', 'panel-scrim', () => console.log('closed'))
     initPanelSwipe('my-panel', 'my-panel-header', 'panel-scrim')

   Wire a new panel in 3 lines:
     openPanel('invoice-panel', 'panel-scrim')
     closePanel('invoice-panel', 'panel-scrim', cb)
     initPanelSwipe('invoice-panel', 'invoice-header', 'panel-scrim')

   CSS required on the panel element:
     .panel { transform: translateY(100%); transition: transform 0.44s ... }
     .panel.open { transform: translateY(0); }
     .panel.closing { transform: translateY(100%); transition: ... faster }
   ============================================================ */

/**
 * openPanel — slides panel up, shows scrim
 * @param {string} panelId
 * @param {string} scrimId
 */
export function openPanel(panelId, scrimId) {
  const panel = document.getElementById(panelId);
  const scrim = document.getElementById(scrimId);
  if (!panel) return;
  panel.classList.remove('closing');
  panel.classList.add('open');
  if (scrim) {
    scrim.classList.add('open');
    scrim.style.pointerEvents = 'auto';
  }
}

/**
 * closePanel — slides panel down, hides scrim, runs callback
 * @param {string}   panelId
 * @param {string}   scrimId
 * @param {Function} cb — optional, fires after animation
 */
export function closePanel(panelId, scrimId, cb) {
  const panel = document.getElementById(panelId);
  const scrim = document.getElementById(scrimId);
  if (!panel) return;
  panel.classList.add('closing');
  panel.classList.remove('open');
  if (scrim) {
    scrim.classList.remove('open');
    scrim.style.pointerEvents = 'none';
  }
  setTimeout(() => {
    panel.classList.remove('closing');
    // Full inline style reset so CSS classes take over cleanly on next open
    panel.style.transform  = '';
    panel.style.transition = '';
    if (scrim) {
      scrim.style.opacity    = '';
      scrim.style.transition = '';
    }
    if (cb) cb();
  }, 320);
}

/**
 * initPanelSwipe — swipe-down-to-close gesture on the panel header
 *
 * Mirrors MenuDrawer swipe from dashboard-theme.txt but inverted:
 * drag DOWN to dismiss instead of right.
 *
 * Behavior:
 * - Locks to vertical axis (horizontal swipe = no-op)
 * - Threshold: 35% of panel height triggers close
 * - Speed-proportional snap on release
 * - Scrim opacity tracks drag in real time
 * - Full inline style reset after swipe-close so panel reopens cleanly
 *
 * @param {string}   panelId
 * @param {string}   headerId  — drag zone (the whole header element)
 * @param {string}   scrimId
 * @param {Function} onClose   — optional callback after swipe close
 */
export function initPanelSwipe(panelId, headerId, scrimId, onClose) {
  const panel  = document.getElementById(panelId);
  const header = document.getElementById(headerId);
  if (!panel || !header) return;

  const THRESHOLD_RATIO = 0.35;
  let startY = null, startX = null, curY = 0, dragging = false;

  function applyDrag(dy) {
    const h        = panel.offsetHeight || 600;
    const progress = Math.min(Math.max(dy / h, 0), 1);
    panel.style.transform = `translateY(${dy}px)`;
    const scrim = document.getElementById(scrimId);
    if (scrim) scrim.style.opacity = String(1 - progress);
  }

  function onStart(e) {
    const t = e.touches ? e.touches[0] : e;
    startY = t.clientY; startX = t.clientX;
    dragging = false; curY = 0;
    panel.style.transition = 'none';
    const scrim = document.getElementById(scrimId);
    if (scrim) scrim.style.transition = 'none';
  }

  function onMove(e) {
    if (startY === null) return;
    const t  = e.touches ? e.touches[0] : e;
    const dy = t.clientY - startY;
    const dx = Math.abs(t.clientX - startX);
    if (!dragging) {
      if (Math.abs(dy) < 5 && dx < 5) return;
      if (dx > Math.abs(dy)) { startY = null; return; } // horizontal — bail
      dragging = true;
    }
    if (dy <= 0) return; // downward only
    curY = dy;
    applyDrag(curY);
    if (e.cancelable) e.preventDefault();
  }

  function onEnd() {
    if (!dragging) { startY = null; return; }
    startY = null; dragging = false;

    const scrim = document.getElementById(scrimId);
    const h     = panel.offsetHeight || 600;

    if (curY > h * THRESHOLD_RATIO) {
      // Snap closed — speed proportional to remaining distance
      const ms = Math.max((h - curY) / h * 280, 80);
      panel.style.transition = `transform ${ms}ms cubic-bezier(0.4,0,1,1)`;
      if (scrim) scrim.style.transition = `opacity ${ms}ms linear`;
      applyDrag(h);
      setTimeout(() => {
        // Full reset — CSS classes take back control
        panel.style.transform  = '';
        panel.style.transition = '';
        if (scrim) { scrim.style.opacity = ''; scrim.style.transition = ''; }
        panel.classList.remove('open', 'closing');
        if (scrim) { scrim.classList.remove('open'); scrim.style.pointerEvents = 'none'; }
        if (onClose) onClose();
      }, ms);
    } else {
      // Snap back open
      const ms = Math.max(curY / h * 280, 80);
      panel.style.transition = `transform ${ms}ms cubic-bezier(0.16,1,0.3,1)`;
      if (scrim) scrim.style.transition = `opacity ${ms}ms ease`;
      applyDrag(0);
      setTimeout(() => {
        panel.style.transition = '';
        if (scrim) scrim.style.transition = '';
      }, ms);
    }
  }

  header.addEventListener('touchstart', onStart, { passive: true });
  header.addEventListener('touchmove',  onMove,  { passive: false });
  header.addEventListener('touchend',   onEnd);
  header.addEventListener('mousedown',  onStart);
  window.addEventListener('mousemove',  onMove);
  window.addEventListener('mouseup',    onEnd);
}
