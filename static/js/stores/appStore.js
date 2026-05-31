import { create } from 'zustand';

export const useAppStore = create((set) => ({
  // Active panel
  activePanel: 'portfolio',
  setActivePanel: (panel) => set({ activePanel: panel }),

  // Data
  dashboard: null,
  scan: null,
  risk: null,
  audit: null,

  // Update data
  updateDashboard: (data) => set({ dashboard: data }),
  updateScan: (data) => set({ scan: data }),
  updateRisk: (data) => set({ risk: data }),
  updateAudit: (data) => set({ audit: data }),

  // WebSocket status
  wsStatus: 'disconnected',
  setWsStatus: (status) => set({ wsStatus: status }),
}));
