// src/comps/life/BottomNav/BottomNav.tsx
'use client';

import { usePathname, useRouter } from 'next/navigation';
import { BNav } from '../../matr/BottomNav/b-nav';
import { navItems } from '../../../config/BottomNav/items';

export function BottomNav() {
  const pathname = usePathname();
  const router = useRouter();

  return (
    <nav className="bottom-nav">
      {navItems.map((item) => (
        <BNav
          key={item.id}
          icon={item.icon}
          label={item.label}
          isActive={item.id === pathname}
          isFab={item.isFab}
          onClick={() => router.push(item.id)}
        />
      ))}
    </nav>
  );
}