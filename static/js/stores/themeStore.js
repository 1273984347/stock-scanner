import { create } from 'zustand';

export const useThemeStore = create((set) => ({
  // Theme mode
  theme: localStorage.getItem('theme') || 'light',
  setTheme: (theme) => {
    localStorage.setItem('theme', theme);
    document.documentElement.setAttribute('data-theme', theme);
    set({ theme });
  },
  toggleTheme: () => set((state) => {
    const next = state.theme === 'light' ? 'dark' : 'light';
    localStorage.setItem('theme', next);
    document.documentElement.setAttribute('data-theme', next);
    return { theme: next };
  }),

  // Color scheme (up/down colors)
  colorScheme: localStorage.getItem('colorScheme') || 'international',
  setColorScheme: (scheme) => {
    localStorage.setItem('colorScheme', scheme);
    document.documentElement.setAttribute('data-color-scheme', scheme);
    set({ colorScheme: scheme });
  },
}));
