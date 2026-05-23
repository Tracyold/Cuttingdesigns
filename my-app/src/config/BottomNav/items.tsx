import { Home, Search, User } from 'lucide-react';
import type { ReactNode } from 'react';

interface NavItem {
  id: string;
  icon: ReactNode;
  label?: string;
  isFab?: boolean;
}

export const navItems: NavItem[] = [
  { id: '/',        icon: <Home />,   label: 'Home'    },
  { id: '/search',  icon: <Search />, label: 'Search'  },
  { id: '/profile', icon: <User />,   label: 'Profile' },
];