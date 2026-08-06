// Entity-kind color/label palette — shared by all graph components.
const KIND_COLORS = {
  person:      "#F59E0B",
  org:         "#8B5CF6",
  money:       "#10B981",
  date:        "#6366F1",
  location:    "#F97316",
  identifier:  "#EC4899",
  document:    "#EF4444",
  standard:    "#06B6D4",
  transaction: "#14B8A6",
  category:    "#D946EF",
  event:       "#F472B6",  // Triangle of Attribution hub
};
const KIND_LABELS = {
  person: "People", org: "Orgs", money: "Money", date: "Dates",
  location: "Places", identifier: "IDs", document: "Docs", standard: "Standards",
  transaction: "Txns", category: "Categories",
  event: "Events",
};
const FALLBACK_COLOR = "#94A3B8";

export function kindColor(kind) {
  return KIND_COLORS[kind] || FALLBACK_COLOR;
}

export function kindLabel(kind) {
  if (kind == null) return "other";
  return KIND_LABELS[kind] || String(kind);
}

export { KIND_COLORS, KIND_LABELS, FALLBACK_COLOR };
