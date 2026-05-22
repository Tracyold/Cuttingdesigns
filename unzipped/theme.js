/* ============================================================
   ccg-theme.js
   Theme management — dark / light toggle.
   Applies to .phone element and body.

   Usage:
     import { setTheme, toggleTheme, getTheme } from './ccg-theme.js'
     setTheme(true)   // dark
     setTheme(false)  // light
     toggleTheme()

   SUPABASE: persist theme preference to user profile
   ============================================================ */

let _isDark = true;

export function getTheme() {
  return _isDark;
}

export function setTheme(dark, phoneEl = null) {
  _isDark = dark;
  const phone = phoneEl || document.getElementById('phone');
  const body  = document.body;

  if (dark) {
    phone?.classList.remove('light');
    body?.classList.remove('light');
  } else {
    phone?.classList.add('light');
    body?.classList.add('light');
  }

  // SUPABASE: persist to user profile
  // import { supabase } from './ccg-supabase.js'
  // await supabase.from('profiles')
  //   .update({ theme: dark ? 'dark' : 'light' })
  //   .eq('id', USER_ID)
}

export function toggleTheme(phoneEl = null) {
  setTheme(!_isDark, phoneEl);
}

// SUPABASE: load saved theme on init
// export async function loadTheme(userId) {
//   const { data } = await supabase
//     .from('profiles')
//     .select('theme')
//     .eq('id', userId)
//     .single()
//   if (data) setTheme(data.theme === 'dark')
// }
