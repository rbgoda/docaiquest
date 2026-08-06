import React, { useState, useEffect, useRef, createContext, useContext, useCallback } from "react";
import Modal from "./Modal.jsx";

/**
 * Replacements for native window.confirm / window.prompt / window.alert.
 *
 * Native dialogs are killable by the browser's "prevent additional
 * dialogs" toggle, can't be styled, and have no character counter or
 * formatting for vendor-facing reason text. These components reuse the
 * accessible <Modal/> primitive (focus trap, Escape, ARIA).
 *
 * Mounted at the React root via <DialogHost/>. Imperative API:
 *   const confirm = useConfirm();
 *   const ok = await confirm({ title: "Delete?", body: "Cannot undo." });
 *
 *   const prompt = usePrompt();
 *   const reason = await prompt({ title: "Reason", placeholder: "..." });
 *
 * Sync window.confirm/prompt/alert call sites turn into `await` —
 * unavoidable, since custom modals can't block the event loop. Migrate
 * sites case-by-case; the originals keep working until then.
 */

const DialogCtx = createContext(null);

export function DialogHost({ children }) {
  const [confirmState, setConfirmState] = useState(null);
  const [promptState, setPromptState] = useState(null);
  const [alertState, setAlertState] = useState(null);

  const confirm = useCallback((opts) => {
    return new Promise((resolve) => {
      setConfirmState({ ...opts, resolve });
    });
  }, []);

  const prompt = useCallback((opts) => {
    return new Promise((resolve) => {
      setPromptState({ ...opts, resolve });
    });
  }, []);

  const alert = useCallback((opts) => {
    return new Promise((resolve) => {
      setAlertState({ ...opts, resolve });
    });
  }, []);

  const value = { confirm, prompt, alert };

  return (
    <DialogCtx.Provider value={value}>
      {children}
      <ConfirmDialogView state={confirmState} setState={setConfirmState} />
      <PromptDialogView state={promptState} setState={setPromptState} />
      <AlertDialogView state={alertState} setState={setAlertState} />
    </DialogCtx.Provider>
  );
}

export function useConfirm() {
  const ctx = useContext(DialogCtx);
  if (!ctx) throw new Error("useConfirm must be used inside <DialogHost>");
  return ctx.confirm;
}

export function usePrompt() {
  const ctx = useContext(DialogCtx);
  if (!ctx) throw new Error("usePrompt must be used inside <DialogHost>");
  return ctx.prompt;
}

export function useAlert() {
  const ctx = useContext(DialogCtx);
  if (!ctx) throw new Error("useAlert must be used inside <DialogHost>");
  return ctx.alert;
}

function ConfirmDialogView({ state, setState }) {
  if (!state) return null;
  const { title = "Are you sure?", body, confirmLabel = "Confirm", cancelLabel = "Cancel", destructive = false, resolve } = state;
  const close = (answer) => { resolve(answer); setState(null); };
  return (
    <Modal open onClose={() => close(false)} labelledBy="confirm-title" maxWidth={460} role="alertdialog">
      <div className="p-5">
        <h2 id="confirm-title" className="serif font-semibold tracking-tight mb-2" style={{ fontSize: 18 }}>{title}</h2>
        {body && <p className="ink2" style={{ fontSize: 13, lineHeight: 1.5 }}>{body}</p>}
        <div className="row gap-2 mt-4" style={{ justifyContent: "flex-end" }}>
          <button onClick={() => close(false)} className="border bg1" style={{ padding: "6px 14px", borderRadius: 6, fontSize: 13 }}>{cancelLabel}</button>
          <button autoFocus onClick={() => close(true)} className={destructive ? "" : "btn-gold"}
            style={destructive
              ? { padding: "6px 14px", borderRadius: 6, fontSize: 13, background: "#D8625E", color: "white", border: "none" }
              : { padding: "6px 14px", borderRadius: 6, fontSize: 13 }}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function PromptDialogView({ state, setState }) {
  const [value, setValue] = useState("");
  const inputRef = useRef(null);
  useEffect(() => {
    if (state) {
      setValue(state.defaultValue || "");
      queueMicrotask(() => inputRef.current?.focus());
    }
  }, [state]);
  if (!state) return null;
  const { title = "Enter value", body, placeholder = "", confirmLabel = "OK", cancelLabel = "Cancel", required = false, maxLength, resolve } = state;
  const close = (answer) => { resolve(answer); setState(null); };
  const trimmed = (value || "").trim();
  const canSubmit = !required || trimmed.length > 0;
  return (
    <Modal open onClose={() => close(null)} labelledBy="prompt-title" maxWidth={460}>
      <form className="p-5" onSubmit={(e) => { e.preventDefault(); if (canSubmit) close(value); }}>
        <h2 id="prompt-title" className="serif font-semibold tracking-tight mb-2" style={{ fontSize: 18 }}>{title}</h2>
        {body && <p className="ink2 mb-3" style={{ fontSize: 13, lineHeight: 1.5 }}>{body}</p>}
        <textarea
          ref={inputRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          maxLength={maxLength}
          rows={3}
          className="bg2 border rounded-md"
          style={{ width: "100%", padding: 8, fontSize: 13, fontFamily: "inherit", resize: "vertical" }}
          aria-required={required}
        />
        {maxLength && (
          <div className="ink3 mono mt-1" style={{ fontSize: 11, textAlign: "right" }}>
            {trimmed.length} / {maxLength}
          </div>
        )}
        <div className="row gap-2 mt-3" style={{ justifyContent: "flex-end" }}>
          <button type="button" onClick={() => close(null)} className="border bg1" style={{ padding: "6px 14px", borderRadius: 6, fontSize: 13 }}>{cancelLabel}</button>
          <button type="submit" disabled={!canSubmit} className="btn-gold" style={{ padding: "6px 14px", borderRadius: 6, fontSize: 13, opacity: canSubmit ? 1 : 0.5 }}>{confirmLabel}</button>
        </div>
      </form>
    </Modal>
  );
}

function AlertDialogView({ state, setState }) {
  if (!state) return null;
  const { title = "Notice", body, okLabel = "OK", resolve } = state;
  const close = () => { resolve(); setState(null); };
  return (
    <Modal open onClose={close} labelledBy="alert-title" maxWidth={420} role="alertdialog">
      <div className="p-5">
        <h2 id="alert-title" className="serif font-semibold tracking-tight mb-2" style={{ fontSize: 18 }}>{title}</h2>
        {body && <p className="ink2" style={{ fontSize: 13, lineHeight: 1.5 }}>{body}</p>}
        <div className="row mt-4" style={{ justifyContent: "flex-end" }}>
          <button autoFocus onClick={close} className="btn-gold" style={{ padding: "6px 14px", borderRadius: 6, fontSize: 13 }}>{okLabel}</button>
        </div>
      </div>
    </Modal>
  );
}
