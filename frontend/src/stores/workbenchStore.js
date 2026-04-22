import { create } from "zustand";

export const useWorkbenchStore = create((set) => ({
  selectedCandidateId: "",
  filters: {
    pool: "pending_review",
    quickFilter: "all",
    search: "",
    risk: "all",
    sort: "priority_desc"
  },
  setSelectedCandidateId: (selectedCandidateId) => set({ selectedCandidateId }),
  setFilter: (key, value) =>
    set((state) => ({
      filters: {
        ...state.filters,
        [key]: value
      }
    })),
  resetFilters: () =>
    set({
      filters: {
        pool: "pending_review",
        quickFilter: "all",
        search: "",
        risk: "all",
        sort: "priority_desc"
      }
    })
}));
