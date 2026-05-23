import type { ReactNode } from 'react';
import { BNav } from './b-nav';

interface NavItem {
  id: string;
  icon: ReactNode;
  label?: string;
  isFab?: boolean;
}

interface NavProps {
  items: NavItem[];
  activeId: string;
  onSelect: (id: string) => void;
}

export function Nav({ items, activeId, onSelect }: NavProps) {
  return (
    <nav className="bottom-nav">
      {items.map((item) => (
        <BNav
          key={item.id}
          icon={item.icon}
          label={item.label}
          isActive={item.id === activeId}
          isFab={item.isFab}
          onClick={() => onSelect(item.id)}
        />
      ))}
    </nav>
  );
}