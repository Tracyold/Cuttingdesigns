#!/usr/bin/env python3
"""
extract_behaviors.py

Scans AI-written React components (.tsx .jsx .ts .js) and:

  1. Detects every interaction/behavior pattern by component
  2. Writes a REPORT showing what was found and where
  3. Generates ready-to-use TypeScript hook files  →  src/behaviors/
  4. Lists every CSS class name each behavior needs  →  to wire into locs/

Detected behaviors
──────────────────
  useInteractive    press + hover state + mouse/touch handlers
  useToggle         boolean flip state (show/hide panels, open/close)
  useClickOutside   close-on-outside-click via useEffect + ref
  useEnterExit      visible + animating state with timed exit cleanup
  useSwipeDismiss   horizontal touch/mouse drag-to-close (drawer)
  usePinchZoomPan   pinch-to-zoom + mouse pan (image viewer)
  useReadItems      Set-based read/unread item tracker
  useTheme          dark / light boolean toggle

The generated hooks all return:
  { className, handlers, ...state }

className is a space-separated string of is-* class names.
Wire it into matr/ components.  Style the classes in locs/.
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime
from collections import OrderedDict
from textwrap import dedent


EXTENSIONS = {'.tsx', '.jsx', '.ts', '.js'}


# ── Output path prompts ───────────────────────────────────────────────────────

def prompt_hooks_dir() -> Path:
    """Ask where to save the generated hook .ts files."""
    print()
    raw = input('  Path to save component files (e.g. src/behaviors): ').strip().strip('"\'')
    p = Path(raw)
    p.mkdir(parents=True, exist_ok=True)
    return p

def prompt_report_dir() -> Path:
    """Ask where to save the audit report .txt file."""
    raw = input('  Path to save audit report:                    ').strip().strip('"\'')
    p = Path(raw)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Input prompt ───────────────────────────────────────────────────────────────

def prompt_target():
    print()
    print('  [1]  Single file')
    print('  [2]  Entire directory')
    print()
    choice = input('  Choice (1 or 2): ').strip()
    if choice == '1':
        while True:
            raw = input('\n  File path: ').strip().strip('"\'')
            p = Path(raw)
            if p.is_file():
                return 'file', [p]
            print(f'  ✗  Not found: {raw}')
    else:
        while True:
            raw = input('\n  Directory path: ').strip().strip('"\'')
            p = Path(raw)
            if p.is_dir():
                files = []
                for ext in EXTENSIONS:
                    files.extend(p.rglob(f'*{ext}'))
                return 'dir', sorted(files)
            print(f'  ✗  Not found: {raw}')


# ── Header / component detection ──────────────────────────────────────────────
# (same approach as extract_ai_styles.py)

_HEAD_PAT = re.compile(
    r'(?:'
    r'^[ \t]*//[ \t]*[─═━\-]{2,}[ \t]+(.+?)[ \t]*[─═━\-]*[ \t]*$'       # // ── Name ───
    r'|\{/\*+[ \t]*(.+?)[ \t]*\*+/\}'                                      # {/* Name */}
    r'|^[ \t]*function\s+([A-Z]\w+)\s*\('                                   # function Name(
    r'|^[ \t]*(?:export\s+default\s+function|export\s+function)\s+([A-Z]\w+)\s*\(' # export function Name(
    r')',
    re.MULTILINE
)

def find_components(text: str) -> list:
    """Return sorted [(pos, component_name)] from headers + function declarations."""
    hits = []
    for m in _HEAD_PAT.finditer(text):
        name = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or '').strip()
        name = re.sub(r'[─═━\-]{3,}', '', name).strip()
        if name and 2 < len(name) < 60:
            hits.append((m.start(), name))
    hits.sort(key=lambda x: x[0])
    # Deduplicate
    out, last = [], -999
    for pos, name in hits:
        if pos - last > 10:
            out.append((pos, name))
            last = pos
    return out

def nearest_component(pos: int, components: list) -> str:
    label = '[top level]'
    for c_pos, c_name in components:
        if c_pos <= pos:
            label = c_name
        else:
            break
    return label


# ── Behavior detectors ─────────────────────────────────────────────────────────
#
# Each detector is a dict with:
#   name          str    hook name
#   desc          str    one-line description
#   all_of        list   ALL of these patterns must match the file
#   any_of        list   AT LEAST ONE of these must match (can be empty)
#   classnames    list   CSS is-* classes this behavior drives
#   notes         str    human-readable wiring hint
# ──────────────────────────────────────────────────────────────────────────────

DETECTORS = [
    {
        'name': 'useInteractive',
        'desc': 'press + hover state with mouse/touch handlers',
        'all_of': [
            r'\bconst\s+\[(?:pressed|isPressed),\s*set(?:Pressed|IsPressed)\]\s*=\s*useState\(false\)',
            r'onMouseDown[^=]*=.*?set(?:Pressed|IsPressed)\(true\)',
        ],
        'any_of': [
            r'onMouseUp[^=]*=.*?set(?:Pressed|IsPressed)\(false\)',
            r'onTouchStart[^=]*=.*?set(?:Pressed|IsPressed)\(true\)',
        ],
        'classnames': ['is-pressed', 'is-hovered'],
        'notes': (
            'Spread { ...handlers } onto the element. '
            'className gets "is-pressed" and/or "is-hovered" added automatically.'
        ),
    },
    {
        'name': 'useToggle',
        'desc': 'boolean flip state — show/hide panels, open/close overlays',
        'all_of': [
            r'useState\(false\)',
            r'set\w+\((?:v|p|s|prev)\s*=>\s*!(?:v|p|s|prev)\)',
        ],
        'any_of': [],
        'classnames': ['is-open', 'is-closed'],
        'notes': (
            'Replace setShowX(v => !v) calls with toggle(). '
            'className becomes "is-open" or "is-closed".'
        ),
    },
    {
        'name': 'useClickOutside',
        'desc': 'close when clicking outside a ref element',
        'all_of': [
            r'useEffect\s*\(',
            r'document\.addEventListener\s*\(\s*["\']mousedown["\']',
            r'\.current\s*&&\s*!.*?\.contains\s*\(',
        ],
        'any_of': [],
        'classnames': [],
        'notes': (
            'Pass the ref and the close callback. '
            'No classnames needed — purely side-effect behavior.'
        ),
    },
    {
        'name': 'useEnterExit',
        'desc': 'visible + animating state pair with timed exit',
        'all_of': [
            r'const\s+\[(?:visible|isVisible),\s*set(?:Visible|IsVisible)\]\s*=\s*useState\(false\)',
            r'const\s+\[(?:animating|isAnimating),\s*set(?:Animating|IsAnimating)\]\s*=\s*useState\(false\)',
            r'setTimeout\s*\(',
        ],
        'any_of': [],
        'classnames': ['is-visible', 'is-open', 'is-animating'],
        'notes': (
            'Replace the open prop pattern. '
            'Drives is-visible (mount/unmount), is-open (fully open), is-animating (exit).'
        ),
    },
    {
        'name': 'useSwipeDismiss',
        'desc': 'horizontal swipe-to-close gesture (drawer / bottom sheet)',
        'all_of': [
            r'addEventListener\s*\(\s*["\']touchstart["\']',
            r'addEventListener\s*\(\s*["\']touchmove["\']',
            r'translateX',
        ],
        'any_of': [
            r'THRESHOLD',
            r'DRAWER_W',
            r'e\.touches\[0\]',
        ],
        'classnames': ['is-dragging'],
        'notes': (
            'Pass drawerRef, scrimRef, and onDismiss. '
            'is-dragging suppresses transitions while the finger is moving.'
        ),
    },
    {
        'name': 'usePinchZoomPan',
        'desc': 'pinch-to-zoom + mouse pan for image / circle viewer',
        'all_of': [
            r'e\.touches\.length\s*===?\s*2',
            r'const\s+\[scale,\s*setScale\]',
            r'const\s+\[pos,\s*setPos\]',
        ],
        'any_of': [],
        'classnames': ['is-zoomed'],
        'notes': (
            'Attach handlers to the zoomable container. '
            'is-zoomed changes cursor and enables panning.'
        ),
    },
    {
        'name': 'useReadItems',
        'desc': 'Set-based read/unread item tracker',
        'all_of': [
            r'useState\s*\(\s*new\s+Set\s*\(',
            r'new\s+Set\s*\(\s*\[\.\.\.(?:prev|p)',
        ],
        'any_of': [],
        'classnames': ['is-new', 'is-read'],
        'notes': (
            'Pass the item id to markRead(id). '
            'is-new drives the highlight/pulse; is-read removes it.'
        ),
    },
    {
        'name': 'useTheme',
        'desc': 'dark / light boolean toggle',
        'all_of': [
            r'const\s+\[dark,\s*setDark\]\s*=\s*useState\s*\(\s*(?:true|false)\s*\)',
            r'setDark\s*\(',
        ],
        'any_of': [],
        'classnames': ['is-dark'],
        'notes': (
            'Returns { dark, toggle, className }. '
            'Set data-theme on <html> or apply is-dark to the root wrapper.'
        ),
    },
]


# ── Scanning ───────────────────────────────────────────────────────────────────

def scan_file(text: str) -> dict:
    """
    Returns { behavior_name: [component_name, ...] }
    for every behavior found in the file.
    """
    components = find_components(text)
    found: dict = {}

    for det in DETECTORS:
        # Must match every pattern in all_of
        if not all(re.search(pat, text, re.DOTALL) for pat in det['all_of']):
            continue
        # Must match at least one pattern in any_of (if any_of is non-empty)
        if det['any_of'] and not any(re.search(pat, text, re.DOTALL) for pat in det['any_of']):
            continue

        # Which components use it?
        # Check each pattern match position → nearest component
        using: set = set()
        for pat in det['all_of']:
            for m in re.finditer(pat, text, re.DOTALL):
                comp = nearest_component(m.start(), components)
                using.add(comp)

        found[det['name']] = sorted(using)

    return found


# ── Component templates ───────────────────────────────────────────────────────
# Each returns a complete .tsx file as a string.
# Components wrap children and inject is-* classnames automatically.
# Use as: <Interactive className="tile">...</Interactive>

from textwrap import dedent as _d

def _comp_Interactive() -> str:
    return _d("""\
    import { useState } from 'react';

    // ── Interactive ───────────────────────────────────────────────────────────────
    //
    // Wraps any element and injects is-pressed / is-hovered classnames.
    //
    // Usage:
    //   <Interactive className="tile" onClick={handleClick}>
    //     content
    //   </Interactive>
    //
    //   <Interactive as="button" className="btn" onClick={submit}>
    //     Submit
    //   </Interactive>
    //
    // Add to locs/:
    //   .tile.is-pressed  { transform: scale(0.96);           box-shadow: ... }
    //   .tile.is-hovered  { transform: translate(-1px,-1px);  box-shadow: ... }
    // ─────────────────────────────────────────────────────────────────────────────

    interface InteractiveProps {
      children?:  React.ReactNode;
      className?: string;
      /** HTML tag to render — default "div" */
      as?:        React.ElementType;
      [key: string]: any;
    }

    export function Interactive({
      children,
      className = '',
      as: Tag   = 'div',
      ...props
    }: InteractiveProps) {
      const [pressed, setPressed] = useState(false);
      const [hovered, setHovered] = useState(false);

      const bClass = [
        pressed ? 'is-pressed' : '',
        hovered ? 'is-hovered' : '',
      ].filter(Boolean).join(' ');

      return (
        <Tag
          className={[className, bClass].filter(Boolean).join(' ')}
          onMouseDown={() => setPressed(true)}
          onMouseUp={() => setPressed(false)}
          onMouseLeave={() => { setPressed(false); setHovered(false); }}
          onMouseEnter={() => setHovered(true)}
          onTouchStart={() => setPressed(true)}
          onTouchEnd={() => setPressed(false)}
          onTouchCancel={() => setPressed(false)}
          {...props}
        >
          {children}
        </Tag>
      );
    }
    """)


def _comp_Toggle() -> str:
    return _d("""\
    import { useState } from 'react';

    // ── Toggle ────────────────────────────────────────────────────────────────────
    //
    // Manages a boolean and exposes it via render prop.
    //
    // Usage:
    //   <Toggle>
    //     {({ on, toggle, className }) => (
    //       <>
    //         <button onClick={toggle}>{on ? 'hide' : 'show'}</button>
    //         <div className={`panel ${className}`}>content</div>
    //       </>
    //     )}
    //   </Toggle>
    //
    //   <Toggle initial={true}>
    //     {({ on, open, close }) => ( ... )}
    //   </Toggle>
    //
    // Add to locs/:
    //   .panel.is-open   { display: flex; }
    //   .panel.is-closed { display: none; }
    // ─────────────────────────────────────────────────────────────────────────────

    interface ToggleRenderProps {
      on:        boolean;
      toggle:    () => void;
      open:      () => void;
      close:     () => void;
      /** 'is-open' | 'is-closed' */
      className: string;
    }

    interface ToggleProps {
      initial?:  boolean;
      children:  (props: ToggleRenderProps) => React.ReactNode;
    }

    export function Toggle({ initial = false, children }: ToggleProps) {
      const [on, setOn] = useState(initial);

      return (
        <>
          {children({
            on,
            toggle:    () => setOn(v => !v),
            open:      () => setOn(true),
            close:     () => setOn(false),
            className: on ? 'is-open' : 'is-closed',
          })}
        </>
      );
    }
    """)


def _comp_ClickOutside() -> str:
    return _d("""\
    import { useEffect, useRef } from 'react';

    // ── ClickOutside ──────────────────────────────────────────────────────────────
    //
    // Fires onClose() when a mousedown occurs outside the wrapped element.
    //
    // Usage:
    //   <ClickOutside onClose={() => setOpen(false)} enabled={open}>
    //     <div className="modal">content</div>
    //   </ClickOutside>
    //
    // No classnames — purely a side-effect wrapper.
    // ─────────────────────────────────────────────────────────────────────────────

    interface ClickOutsideProps {
      children:   React.ReactNode;
      onClose:    () => void;
      enabled?:   boolean;
      className?: string;
      as?:        React.ElementType;
    }

    export function ClickOutside({
      children,
      onClose,
      enabled   = true,
      className = '',
      as: Tag   = 'div',
    }: ClickOutsideProps) {
      const ref = useRef<HTMLDivElement>(null);

      useEffect(() => {
        if (!enabled) return;
        function handle(e: MouseEvent) {
          if (ref.current && !ref.current.contains(e.target as Node)) {
            onClose();
          }
        }
        document.addEventListener('mousedown', handle);
        return () => document.removeEventListener('mousedown', handle);
      }, [onClose, enabled]);

      return (
        <Tag ref={ref} className={className}>
          {children}
        </Tag>
      );
    }
    """)


def _comp_EnterExit() -> str:
    return _d("""\
    import { useState, useEffect } from 'react';

    // ── EnterExit ─────────────────────────────────────────────────────────────────
    //
    // Mounts children when open=true, adds an exit animation window before unmount.
    // Returns null when fully closed so the element leaves the DOM cleanly.
    //
    // Usage:
    //   <EnterExit open={menuOpen} className="drawer">
    //     content
    //   </EnterExit>
    //
    //   <EnterExit open={menuOpen} duration={300} className="drawer" as="aside">
    //     content
    //   </EnterExit>
    //
    // Add to locs/:
    //   .drawer.is-open      { transform: translateX(0); }
    //   .drawer.is-animating { transform: translateX(100%); transition: ... }
    // ─────────────────────────────────────────────────────────────────────────────

    interface EnterExitProps {
      open:       boolean;
      children:   React.ReactNode;
      className?: string;
      /** Exit animation duration in ms — default 300 */
      duration?:  number;
      as?:        React.ElementType;
    }

    export function EnterExit({
      open,
      children,
      className = '',
      duration  = 300,
      as: Tag   = 'div',
    }: EnterExitProps) {
      const [visible,   setVisible]   = useState(false);
      const [animating, setAnimating] = useState(false);

      useEffect(() => {
        if (open) {
          setVisible(true);
          setAnimating(false);
        } else if (visible) {
          setAnimating(true);
          const t = setTimeout(() => {
            setVisible(false);
            setAnimating(false);
          }, duration);
          return () => clearTimeout(t);
        }
      }, [open, duration]);  // eslint-disable-line react-hooks/exhaustive-deps

      if (!visible) return null;

      const bClass = [
        'is-visible',
        animating  ? 'is-animating' : 'is-open',
      ].filter(Boolean).join(' ');

      return (
        <Tag className={[className, bClass].filter(Boolean).join(' ')}>
          {children}
        </Tag>
      );
    }
    """)


def _comp_SwipeDismiss() -> str:
    return _d("""\
    import { useEffect, useRef } from 'react';

    // ── SwipeDismiss ──────────────────────────────────────────────────────────────
    //
    // Horizontal swipe-to-close for drawers and bottom sheets.
    // Uses a render prop to attach refs to the drawer and scrim separately.
    //
    // Usage:
    //   <SwipeDismiss open={open} onDismiss={onClose}>
    //     {({ drawerRef, scrimRef }) => (
    //       <>
    //         <div ref={scrimRef}  className="scrim"  onClick={onClose} />
    //         <div ref={drawerRef} className="drawer">content</div>
    //       </>
    //     )}
    //   </SwipeDismiss>
    //
    // Add to locs/:
    //   .drawer              { will-change: transform; }
    //   .drawer.is-dragging  { transition: none !important; }
    //   .scrim.is-dragging   { transition: none !important; }
    // ─────────────────────────────────────────────────────────────────────────────

    interface SwipeDismissRenderProps {
      drawerRef: React.RefObject<HTMLDivElement>;
      scrimRef:  React.RefObject<HTMLDivElement>;
    }

    interface SwipeDismissProps {
      open:       boolean;
      onDismiss:  () => void;
      /** Fraction of width that triggers dismiss — default 0.35 */
      threshold?: number;
      children:   (props: SwipeDismissRenderProps) => React.ReactNode;
    }

    export function SwipeDismiss({
      open,
      onDismiss,
      threshold = 0.35,
      children,
    }: SwipeDismissProps) {
      const drawerRef = useRef<HTMLDivElement>(null);
      const scrimRef  = useRef<HTMLDivElement>(null);

      useEffect(() => {
        if (!open) return;
        const drw  = drawerRef.current;
        const scrim = scrimRef.current;
        if (!drw || !scrim) return;

        const DRAWER_W  = drw.offsetWidth || 332;
        const THRESHOLD = DRAWER_W * threshold;

        let startX: number | null = null;
        let startY: number | null = null;
        let dragging = false;
        let curX     = 0;

        function applyDrag(dx: number) {
          const progress = Math.min(Math.max(dx / DRAWER_W, 0), 1);
          drw!.style.transform              = `translateX(${dx}px)`;
          scrim!.style.opacity              = String(1 - progress);
          scrim!.style.backdropFilter       = `blur(${progress * 8}px)`;
          (scrim!.style as any).webkitBackdropFilter = `blur(${progress * 8}px)`;
        }

        function onStart(e: TouchEvent | MouseEvent) {
          const t  = 'touches' in e ? e.touches[0] : e;
          startX   = t.clientX; startY = t.clientY;
          dragging = false; curX = 0;
          drw!.style.transition    = 'none';
          scrim!.style.transition  = 'none';
          drw!.classList.add('is-dragging');
          scrim!.classList.add('is-dragging');
        }

        function onMove(e: TouchEvent | MouseEvent) {
          if (startX === null) return;
          const t  = 'touches' in e ? e.touches[0] : e;
          const dx = t.clientX - startX;
          const dy = Math.abs(t.clientY - (startY ?? 0));
          if (!dragging) {
            if (Math.abs(dx) < 5 && dy < 5) return;
            if (dy > Math.abs(dx)) { startX = null; return; }
            dragging = true;
          }
          if (dx <= 0) return;
          curX = dx;
          applyDrag(curX);
          if ('cancelable' in e && e.cancelable) e.preventDefault();
        }

        function onEnd() {
          drw!.classList.remove('is-dragging');
          scrim!.classList.remove('is-dragging');
          if (!dragging) { startX = null; return; }
          startX = null; dragging = false;
          if (curX > THRESHOLD) {
            const ms = Math.max((DRAWER_W - curX) / DRAWER_W * 260, 80);
            drw!.style.transition  = `transform ${ms}ms cubic-bezier(0.4,0,1,1)`;
            scrim!.style.transition = `opacity ${ms}ms linear`;
            applyDrag(DRAWER_W);
            setTimeout(() => onDismiss(), ms);
          } else {
            const ms = Math.max(curX / DRAWER_W * 260, 80);
            drw!.style.transition  = `transform ${ms}ms cubic-bezier(0.16,1,0.3,1)`;
            scrim!.style.transition = `opacity ${ms}ms ease`;
            applyDrag(0);
            setTimeout(() => { drw!.style.transition = ''; scrim!.style.transition = ''; }, ms);
          }
        }

        drw.addEventListener('touchstart',   onStart as EventListener, { passive: true });
        drw.addEventListener('touchmove',    onMove  as EventListener, { passive: false });
        drw.addEventListener('touchend',     onEnd);
        drw.addEventListener('mousedown',    onStart as EventListener);
        window.addEventListener('mousemove', onMove  as EventListener);
        window.addEventListener('mouseup',   onEnd);

        return () => {
          drw.removeEventListener('touchstart',   onStart as EventListener);
          drw.removeEventListener('touchmove',    onMove  as EventListener);
          drw.removeEventListener('touchend',     onEnd);
          drw.removeEventListener('mousedown',    onStart as EventListener);
          window.removeEventListener('mousemove', onMove  as EventListener);
          window.removeEventListener('mouseup',   onEnd);
        };
      }, [open, onDismiss, threshold]);

      return <>{children({ drawerRef, scrimRef })}</>;
    }
    """)


def _comp_PinchZoomPan() -> str:
    return _d("""\
    import { useState, useRef } from 'react';

    // ── PinchZoomPan ──────────────────────────────────────────────────────────────
    //
    // Pinch-to-zoom and mouse-pan for image and media viewers.
    // Uses a render prop to give you the inner style and handlers separately.
    //
    // Usage:
    //   <PinchZoomPan maxScale={4} className="viewer">
    //     {({ innerStyle, innerHandlers }) => (
    //       <div style={innerStyle} {...innerHandlers}>
    //         <img src="..." />
    //       </div>
    //     )}
    //   </PinchZoomPan>
    //
    // Add to locs/:
    //   .viewer           { cursor: zoom-in;  overflow: hidden; }
    //   .viewer.is-zoomed { cursor: grab; }
    // ─────────────────────────────────────────────────────────────────────────────

    interface PinchZoomPanRenderProps {
      scale:         number;
      innerStyle:    React.CSSProperties;
      innerHandlers: Record<string, React.EventHandler<any>>;
      reset:         () => void;
    }

    interface PinchZoomPanProps {
      children:   (props: PinchZoomPanRenderProps) => React.ReactNode;
      className?: string;
      maxScale?:  number;
    }

    export function PinchZoomPan({
      children,
      className = '',
      maxScale  = 4,
    }: PinchZoomPanProps) {
      const [scale,    setScale]    = useState(1);
      const [pos,      setPos]      = useState({ x: 0, y: 0 });
      const [startPan, setStartPan] = useState<{ mx: number; my: number; px: number; py: number } | null>(null);
      const [dragging, setDragging] = useState(false);
      const lastDist = useRef<number | null>(null);

      const reset = () => { setScale(1); setPos({ x: 0, y: 0 }); };

      const innerHandlers = {
        onTouchMove: (e: React.TouchEvent) => {
          if (e.touches.length !== 2) return;
          e.preventDefault();
          const dx   = e.touches[0].clientX - e.touches[1].clientX;
          const dy   = e.touches[0].clientY - e.touches[1].clientY;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (lastDist.current !== null) {
            setScale(s => Math.min(Math.max(s + (dist - lastDist.current!) * 0.01, 1), maxScale));
          }
          lastDist.current = dist;
        },
        onTouchEnd:  () => { lastDist.current = null; },
        onClick:     (e: React.MouseEvent) => {
          e.stopPropagation();
          if (dragging) return;
          setScale(s => { if (s >= maxScale) { reset(); return 1; } return Math.min(s + 1, maxScale); });
        },
        onMouseDown: (e: React.MouseEvent) => {
          if (scale <= 1) return;
          e.stopPropagation();
          setDragging(false);
          setStartPan({ mx: e.clientX, my: e.clientY, px: pos.x, py: pos.y });
        },
        onMouseMove: (e: React.MouseEvent) => {
          if (!startPan) return;
          const dx = e.clientX - startPan.mx;
          const dy = e.clientY - startPan.my;
          if (Math.abs(dx) > 3 || Math.abs(dy) > 3) setDragging(true);
          setPos({ x: startPan.px + dx, y: startPan.py + dy });
        },
        onMouseUp: () => setStartPan(null),
      };

      const innerStyle: React.CSSProperties = {
        transform:       `scale(${scale}) translate(${pos.x / scale}px, ${pos.y / scale}px)`,
        transformOrigin: 'center center',
        transition:      (dragging || startPan) ? 'none' : 'transform 0.2s ease',
      };

      const bClass = scale > 1 ? 'is-zoomed' : '';

      return (
        <div className={[className, bClass].filter(Boolean).join(' ')}>
          {children({ scale, innerStyle, innerHandlers, reset })}
        </div>
      );
    }
    """)


def _comp_ReadItems() -> str:
    return _d("""\
    import { useState } from 'react';

    // ── ReadItems ─────────────────────────────────────────────────────────────────
    //
    // Tracks which items in a list have been seen/read.
    // Uses a render prop so it can drive multiple elements per item.
    //
    // Usage:
    //   <ReadItems>
    //     {({ isNew, markRead }) =>
    //       items.map((item, i) => (
    //         <div
    //           key={i}
    //           className={`row ${isNew(i) ? 'is-new' : 'is-read'}`}
    //           onClick={() => markRead(i)}
    //         >
    //           {item.label}
    //         </div>
    //       ))
    //     }
    //   </ReadItems>
    //
    // Add to locs/:
    //   .row.is-new  { color: accent;  animation: pulse 2s infinite; }
    //   .row.is-read { color: muted;   animation: none; }
    // ─────────────────────────────────────────────────────────────────────────────

    type ItemId = number | string;

    interface ReadItemsRenderProps {
      isNew:     (id: ItemId) => boolean;
      markRead:  (id: ItemId) => void;
      readItems: Set<ItemId>;
      /** Returns 'is-new' or 'is-read' directly */
      className: (id: ItemId) => string;
    }

    interface ReadItemsProps {
      children: (props: ReadItemsRenderProps) => React.ReactNode;
    }

    export function ReadItems({ children }: ReadItemsProps) {
      const [readItems, setReadItems] = useState<Set<ItemId>>(new Set());

      const markRead  = (id: ItemId) => setReadItems(prev => new Set([...prev, id]));
      const isNew     = (id: ItemId) => !readItems.has(id);
      const className = (id: ItemId) => readItems.has(id) ? 'is-read' : 'is-new';

      return <>{children({ isNew, markRead, readItems, className })}</>;
    }
    """)


def _comp_Theme() -> str:
    return _d("""\
    import { useState, useEffect } from 'react';

    // ── Theme ─────────────────────────────────────────────────────────────────────
    //
    // Wraps your app and manages dark / light mode.
    // Syncs to <html data-theme="dark"> so CSS custom properties resolve.
    //
    // Usage:
    //   <Theme initial={true}>
    //     {({ dark, toggle, className }) => (
    //       <div className={`app ${className}`}>
    //         <button onClick={toggle}>toggle theme</button>
    //         {children}
    //       </div>
    //     )}
    //   </Theme>
    //
    // Add to decs/colors.scss:
    //   :root              { --surface: #e8ecf2; ... }
    //   :root[data-theme="dark"] { --surface: #1c2230; ... }
    // ─────────────────────────────────────────────────────────────────────────────

    interface ThemeRenderProps {
      dark:      boolean;
      toggle:    () => void;
      setDark:   (v: boolean) => void;
      /** 'is-dark' | '' */
      className: string;
    }

    interface ThemeProps {
      initial?:  boolean;
      children:  (props: ThemeRenderProps) => React.ReactNode;
    }

    export function Theme({ initial = false, children }: ThemeProps) {
      const [dark, setDarkState] = useState(initial);

      const toggle  = () => setDarkState(d => !d);
      const setDark = (v: boolean) => setDarkState(v);

      useEffect(() => {
        if (dark) {
          document.documentElement.setAttribute('data-theme', 'dark');
        } else {
          document.documentElement.removeAttribute('data-theme');
        }
      }, [dark]);

      return (
        <>
          {children({ dark, toggle, setDark, className: dark ? 'is-dark' : '' })}
        </>
      );
    }
    """)


COMP_GENERATORS: dict = {
    'useInteractive':  _comp_Interactive,
    'useToggle':       _comp_Toggle,
    'useClickOutside': _comp_ClickOutside,
    'useEnterExit':    _comp_EnterExit,
    'useSwipeDismiss': _comp_SwipeDismiss,
    'usePinchZoomPan': _comp_PinchZoomPan,
    'useReadItems':    _comp_ReadItems,
    'useTheme':        _comp_Theme,
}

# Map behavior name → component filename (drop 'use', add .tsx)
def comp_filename(bname: str) -> str:
    return bname[3:] + '.tsx'  # useInteractive → Interactive.tsx



# ── Report builder ─────────────────────────────────────────────────────────────

CSS_CLASSES: dict = {
    'useInteractive':  ['is-pressed', 'is-hovered'],
    'useToggle':       ['is-open', 'is-closed'],
    'useClickOutside': [],
    'useEnterExit':    ['is-visible', 'is-open', 'is-animating'],
    'useSwipeDismiss': ['is-dragging'],
    'usePinchZoomPan': ['is-zoomed'],
    'useReadItems':    ['is-new', 'is-read'],
    'useTheme':        ['is-dark'],
}

BEHAVIOR_DESC: dict = {d['name']: d['desc'] for d in DETECTORS}
BEHAVIOR_NOTES: dict = {d['name']: d['notes'] for d in DETECTORS}


def build_report(
    all_found: dict,         # { filename: { behavior: [components] } }
    generated: list,         # list of filenames written
    behs_dir: Path,
) -> str:
    lines = [
        '╔══════════════════════════════════════════════════════════════════╗',
        '║  BEHAVIOR AUDIT — components generated                           ║',
        '║  extracted by extract_behaviors.py                               ║',
        '╚══════════════════════════════════════════════════════════════════╝',
        '',
        f'  Generated: {datetime.now().strftime("%Y-%m-%d %H-%M-%S")}',
        '',
    ]

    # ── Per-file summary ──────────────────────────────────────────────────────
    for filename, found in all_found.items():
        lines.append(f'\n{"═" * 70}')
        lines.append(f'  ── {filename}')
        lines.append('═' * 70)

        if not found:
            lines.append('\n  (no behavior patterns detected)\n')
            continue

        # Group by behavior → which components use it
        for bname, components in sorted(found.items()):
            desc  = BEHAVIOR_DESC.get(bname, '')
            notes = BEHAVIOR_NOTES.get(bname, '')
            css   = CSS_CLASSES.get(bname, [])

            lines.append(f'\n  {bname}')
            lines.append(f'  {"─" * (len(bname) + 2)}')
            lines.append(f'  {desc}')
            lines.append('')
            lines.append(f'  Found in:')
            for comp in components:
                lines.append(f'    └ {comp}')
            if css:
                lines.append('')
                lines.append(f'  CSS classes needed in locs/:')
                for cls in css:
                    lines.append(f'    .{cls}')
            if notes:
                lines.append('')
                lines.append(f'  Wiring note:')
                # Word-wrap at 66 chars
                words, line_buf = notes.split(), []
                for w in words:
                    if sum(len(x) + 1 for x in line_buf) + len(w) > 66:
                        lines.append(f'    {"".join(line_buf)}')
                        line_buf = []
                    line_buf.append(w + ' ')
                if line_buf:
                    lines.append(f'    {"".join(line_buf).strip()}')

    # ── Generated files list ─────────────────────────────────────────────────
    lines.append(f'\n{"═" * 70}')
    lines.append('  ── Generated component files')
    lines.append('═' * 70)
    lines.append('')

    if generated:
        for fn in generated:
            lines.append(f'  ✓  {behs_dir / fn}')
    else:
        lines.append('  (none — no behaviors detected)')

    # ── CSS class interface reference ────────────────────────────────────────
    lines.append(f'\n{"═" * 70}')
    lines.append('  ── CSS class interface  (add these to locs/)')
    lines.append('═' * 70)
    lines.append('')
    lines.append(
        f'  {"CLASS":<22} {"DRIVEN BY":<22} WHAT IT STYLES'
    )
    lines.append(f'  {"─" * 22} {"─" * 22} {"─" * 24}')

    class_rows = [
        ('is-pressed',   'useInteractive',  'scale + shadow on press'),
        ('is-hovered',   'useInteractive',  'translate + shadow on hover'),
        ('is-open',      'useToggle / useEnterExit', 'panel visible / fully open'),
        ('is-closed',    'useToggle',       'panel hidden'),
        ('is-visible',   'useEnterExit',    'element in DOM'),
        ('is-animating', 'useEnterExit',    'exit transition active'),
        ('is-dragging',  'useSwipeDismiss', 'transition: none during drag'),
        ('is-zoomed',    'usePinchZoomPan', 'cursor, pointer-events'),
        ('is-new',       'useReadItems',    'accent color + pulse anim'),
        ('is-read',      'useReadItems',    'muted color, no animation'),
        ('is-dark',      'useTheme',        'dark theme CSS vars active'),
    ]
    for cls, driver, note in class_rows:
        lines.append(f'  .{cls:<21} {driver:<22} {note}')

    lines.append('')
    return '\n'.join(lines)


# ── Run ────────────────────────────────────────────────────────────────────────

def run():
    print()
    print('╔══════════════════════════════════════════════════════╗')
    print('║   Behavior Extractor                                 ║')
    print('╚══════════════════════════════════════════════════════╝')
    print()
    print('  Finds all interaction patterns in React code.')
    print('  Generates component files to a path you choose.')
    print('  Shows what locs/ CSS classes each behavior needs.')

    mode, files = prompt_target()

    if not files:
        print('\n  No files found.')
        return

    behs_dir   = prompt_hooks_dir()
    output_dir = prompt_report_dir()

    print(f'\n  Scanning {len(files)} file{"s" if len(files) > 1 else ""}...\n')

    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    all_found: dict  = {}        # filename → { behavior_name: [components] }
    all_behaviors    = set()     # which hook names were found across all files

    for p in files:
        try:
            text = p.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            print(f'  ✗  {p.name}: {e}')
            continue

        found = scan_file(text)
        if found:
            all_found[p.name] = found
            all_behaviors.update(found.keys())
            hits = '  '.join(f'{k}({len(v)})' for k, v in found.items())
            print(f'  ✓  {p.name:<40} {hits}')
        else:
            print(f'  –  {p.name:<40} (no behavior patterns detected)')

    # ── Generate hook files ───────────────────────────────────────────────────
    generated = []
    for bname in sorted(all_behaviors):
        if bname in COMP_GENERATORS:
            fn      = comp_filename(bname)
            content = COMP_GENERATORS[bname]()
            path    = behs_dir / fn
            path.write_text(content, encoding='utf-8')
            generated.append(fn)

    # ── Write report ──────────────────────────────────────────────────────────
    report      = build_report(all_found, generated, behs_dir)
    report_path = output_dir / f'behavior-audit_{timestamp}.txt'
    report_path.write_text(report, encoding='utf-8')

    # ── Terminal summary ──────────────────────────────────────────────────────
    total_behaviors = sum(len(v) for v in all_found.values())
    print(f'\n  {total_behaviors} behavior pattern{"s" if total_behaviors != 1 else ""} found')
    print(f'  {len(generated)} component file{"s" if len(generated) != 1 else ""} written → {behs_dir}')
    print(f'\n  Report → {report_path}')
    print()

    if generated:
        print('  Generated components:')
        for fn in generated:
            print(f'    {behs_dir / fn}')
    print()


if __name__ == '__main__':
    run()