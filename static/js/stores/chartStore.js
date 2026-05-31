import { create } from 'zustand';

export const useChartStore = create((set) => ({
  // Current stock
  currentStock: null,
  setCurrentStock: (stock) => set({ currentStock: stock }),

  // Timeframe
  timeframe: '1D',
  setTimeframe: (tf) => set({ timeframe: tf }),

  // Technical indicators
  indicators: ['MA'],
  toggleIndicator: (ind) => set((state) => ({
    indicators: state.indicators.includes(ind)
      ? state.indicators.filter(i => i !== ind)
      : [...state.indicators, ind]
  })),
}));
