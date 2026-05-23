// src/components/matr/BottomNav.tsx
'use client';


interface NavItem {
  id: string;
  icon: React.ReactNode;
  label?: string;
  isFab?: boolean;
}

interface BottomNavProps {
  items: NavItem[];
  activeId: string;
  onSelect: (id: string) => void;
}

export function BottomNav({ items, activeId, onSelect }: BottomNavProps) {
  return (
    <nav className="bottom-nav">
      {items.map((item) => {
        const isActive = item.id === activeId;

        const classes = [
          'nav-btn',
          item.isFab                  ? 'nav-btnfab'          : '',
          isActive && item.isFab      ? 'nav-btnfabactive'    : '',
          isActive && !item.isFab     ? 'nav-btnactive'       : '',
          isActive && !item.isFab     ? 'nav-btnactivenotfab' : '',
        ].filter(Boolean).join(' ');

        return (
          <button
            key={item.id}
            className={classes}
            onClick={() => onSelect(item.id)}
            aria-current={isActive ? 'page' : undefined}
            aria-label={item.label}
          >
            {item.icon}
            {item.label && <span>{item.label}</span>}
          </button>
        );
      })}
    </nav>
  );
}