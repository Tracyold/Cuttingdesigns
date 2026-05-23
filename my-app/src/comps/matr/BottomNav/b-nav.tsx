import type { ReactNode } from 'react';

interface BNavProps {
  icon: ReactNode;
  label?: string;
  isActive: boolean;
  isFab?: boolean;
  onClick: () => void;
}

export function BNav({ icon, label, isActive, isFab, onClick }: BNavProps) {
  const classes = [
    'nav-btn',
    isFab                ? 'nav-btnfab'          : '',
    isActive && isFab    ? 'nav-btnfabactive'    : '',
    isActive && !isFab   ? 'nav-btnactive'       : '',
    isActive && !isFab   ? 'nav-btnactivenotfab' : '',
  ].filter(Boolean).join(' ');

  return (
    <button
      className={classes}
      onClick={onClick}
      aria-current={isActive ? 'page' : undefined}
      aria-label={label}
    >
      {icon}
      {label && <span>{label}</span>}
    </button>
  );
}