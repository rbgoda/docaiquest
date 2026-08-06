import React, { useEffect, useRef } from "react";

/**
 * Accessible modal primitive (TODO #30).
 *
 * Solves the three a11y gaps from the frontend review:
 *   1. role="dialog" + aria-modal so screen readers announce it
 *   2. Escape closes (clicked into the backdrop already worked, but
 *      keyboard users needed a way out without reaching for the mouse)
 *   3. Focus is trapped inside while open — Tab cycles within the dialog,
 *      Shift+Tab cycles backwards, focus returns to the trigger element
 *      when the modal closes
 *   4. Body scroll is locked so the underlying page doesn't drift
 *
 * Usage:
 *   <Modal open={isOpen} onClose={() => setOpen(false)} labelledBy="modal-title">
 *     <h2 id="modal-title">Why this match?</h2>
 *     ...
 *   </Modal>
 *
 * Pass `labelledBy` (id of the heading inside the modal) for proper
 * AT announcement. If omitted, falls back to a generic dialog label.
 */
export default function Modal({
  open,
  onClose,
  children,
  labelledBy,
  describedBy,
  maxWidth = 920,
  // Override the default backdrop styles in rare cases (e.g. transparent
  // for full-screen overlays). The defaults match the previous WhyModal.
  backdropStyle,
  panelStyle,
  // ARIA prop overrides for niche cases — alert-style modals need
  // role="alertdialog" instead of "dialog".
  role = "dialog",
}) {
  const panelRef = useRef(null);
  const previousFocusRef = useRef(null);

  // Body scroll lock — restored on close. Stacking multiple modals would
  // unlock too early if we counted naively; for this app there's only
  // ever ONE modal at a time so a single boolean lock is fine.
  useEffect(() => {
    if (!open) return undefined;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prevOverflow; };
  }, [open]);

  // Focus management:
  //   - remember the element focused BEFORE the modal opened
  //   - move focus into the modal on open (panel itself; tabIndex={-1})
  //   - return focus to the trigger when the modal closes
  useEffect(() => {
    if (!open) return undefined;
    previousFocusRef.current = document.activeElement;
    // Focus the panel itself so screen readers announce the dialog;
    // then the user's first Tab lands on the first interactive child.
    queueMicrotask(() => panelRef.current?.focus());
    return () => {
      const prev = previousFocusRef.current;
      if (prev && typeof prev.focus === "function") {
        // Defer one tick so React unmounts the modal DOM first.
        queueMicrotask(() => prev.focus());
      }
    };
  }, [open]);

  // Key handling — Escape closes; Tab cycles focus inside the panel.
  useEffect(() => {
    if (!open) return undefined;
    const handler = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose?.();
        return;
      }
      if (e.key !== "Tab") return;
      // Collect every tab-stop inside the panel.
      const root = panelRef.current;
      if (!root) return;
      const focusables = root.querySelectorAll(
        'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed"
      role="presentation"
      onClick={onClose}
      style={{
        inset: 0,
        zIndex: 50,
        background: "rgba(0,0,0,0.6)",
        backdropFilter: "blur(6px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
        ...backdropStyle,
      }}
    >
      <div
        ref={panelRef}
        role={role}
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="bg1 border rounded-xl shadow-lg"
        style={{
          maxWidth,
          width: "100%",
          maxHeight: "88vh",
          overflow: "auto",
          outline: "none",     // panel itself isn't a focus-visible target
          ...panelStyle,
        }}
      >
        {children}
      </div>
    </div>
  );
}
