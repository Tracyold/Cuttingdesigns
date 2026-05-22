/* ============================================================
   ccg-bubble-menu.js
   Floating circle icon menu triggered by a flat button.
   Circles slide horizontally from the trigger point.
   Admin title color changes to accent while menu is open.

   Usage:
     import { initBubbleMenu } from './ccg-bubble-menu.js'

     const menu = initBubbleMenu({
       triggerId: 'header-plus',     // the + button
       menuId:    'bubble-menu',     // the container
       buttons:   ['upload-btn', 'save-btn'],  // ordered right→left
       panelId:   'chat-panel',      // closes menu on panel body tap
       titleId:   'panel-title',     // optional — lights up accent when open
     })

     menu.open()
     menu.close()
     menu.isOpen()

   Future custom element:
     <ccg-bubble-menu trigger="#header-plus" title="#panel-title">
       <button slot="action" id="upload-btn">...</button>
       <button slot="action" id="save-btn">...</button>
     </ccg-bubble-menu>
   ============================================================ */

export function initBubbleMenu({ triggerId, menuId, buttons = [], panelId, titleId }) {
  const trigger = document.getElementById(triggerId);
  const menu    = document.getElementById(menuId);
  if (!trigger || !menu) return { open: () => {}, close: () => {}, isOpen: () => false };

  let _isOpen = false;

  const btns  = buttons.map(id => document.getElementById(id)).filter(Boolean);
  const title = titleId ? document.getElementById(titleId) : document.querySelector('.panel-title');

  function open() {
    _isOpen = true;
    trigger.style.opacity    = '1';
    menu.style.pointerEvents = 'auto';
    btns.forEach(btn => {
      btn.style.opacity   = '1';
      btn.style.transform = 'translateX(0) scale(1)';
    });
    // Light up admin title
    if (title) title.classList.add('menu-active');
  }

  function close() {
    _isOpen = false;
    trigger.style.opacity    = '0.5';
    menu.style.pointerEvents = 'none';
    btns.forEach(btn => {
      btn.style.opacity   = '0';
      btn.style.transform = 'translateX(20px) scale(0.6)';
    });
    // Restore admin title
    if (title) title.classList.remove('menu-active');
  }

  function isOpen() { return _isOpen; }

  // Toggle on trigger click
  trigger.addEventListener('click', e => {
    e.stopPropagation();
    _isOpen ? close() : open();
  });

  // Close on panel body tap
  if (panelId) {
    document.getElementById(panelId)?.addEventListener('mousedown', e => {
      if (_isOpen && e.target !== trigger && !menu.contains(e.target)) close();
    });
  }

  return { open, close, isOpen };
}
