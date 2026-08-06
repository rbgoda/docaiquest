// Shared formatting helpers. Add more as they surface across the codebase.

/**
 * Format a snake_case or kebab-case string for display.
 * "bank_statement" → "Bank Statement"
 */
export function prettyType(s) {
  return (s || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
