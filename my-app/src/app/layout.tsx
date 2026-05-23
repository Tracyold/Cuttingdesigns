import '../styles/globals.scss';
import { BottomNav } from '../comps/life/BottomNav/BottomNav';

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html>
      <body>
        {children}
        <BottomNav />
      </body>
    </html>
  );
}
