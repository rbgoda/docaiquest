import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import {
  fetchAuthConfig, fetchMe, ssoExchange, loginWithPassword, logout as apiLogout, updateMe,
} from "../api";
import { setUnauthorizedHandler } from "../api/client";

// Status:
//   booting   — first /api/me roundtrip in flight
//   anon      — known to not be logged in (401 from /api/me or after logout)
//   ready     — logged in, `user` populated
const AuthCtx = createContext(null);

// Persona override — a multi-role user can "view as" a single role to
// preview the stripped UI shells. Stored in localStorage so it survives
// reload. Backend permissions are unchanged; this is purely UI shaping.
//
// KEY PER USER (since 2026-05-16): the storage key is namespaced by the
// signed-in user's email. Without this, logging out + back in as a
// different user would silently apply the *previous* user's persona —
// e.g. Elena toggled to reviewer, log in as James (also has reviewer),
// James would land on the reviewer shell because the single global key
// still said "reviewer". User-keyed storage isolates each account.
//
// Migrates the legacy single-key (docaiq.viewAs.v1) once on first read
// so existing sessions don't lose their persona.
const LEGACY_PERSONA_KEY = "docaiq.viewAs.v1";
function personaKeyFor(email) {
  // Lowercase normalisation so capitalisation drift between logins (e.g.
  // typing "Elena@…" vs "elena@…") doesn't split storage.
  return `docaiq.viewAs.${(email || "anon").toLowerCase()}`;
}
function loadPersona(email) {
  if (!email) return null;
  try {
    const userKey = personaKeyFor(email);
    let v = localStorage.getItem(userKey);
    if (v === null) {
      // One-time migration from the legacy single key. Only the current
      // user inherits it; everyone else starts fresh.
      const legacy = localStorage.getItem(LEGACY_PERSONA_KEY);
      if (legacy) {
        localStorage.setItem(userKey, legacy);
        localStorage.removeItem(LEGACY_PERSONA_KEY);
        v = legacy;
      }
    }
    return v || null;
  } catch { return null; }
}
function savePersona(email, role) {
  if (!email) return;
  try {
    const userKey = personaKeyFor(email);
    if (role) localStorage.setItem(userKey, role);
    else localStorage.removeItem(userKey);
  } catch {}
}

export function AuthProvider({ children }) {
  const [status, setStatus] = useState("booting");
  const [user, setUser] = useState(null);
  const [config, setConfig] = useState(null);  // { devLoginEnabled, googleLoginEnabled, tenant }
  // Persona starts null until we know which user is signed in — the
  // effect below sets it from per-user localStorage once `user` lands.
  const [viewAs, setViewAsState] = useState(null);

  // Bootstrap: ask the backend who we are. 401 → anon.
  useEffect(() => {
    const ctrl = new AbortController();
    Promise.all([
      fetchAuthConfig({ signal: ctrl.signal }).catch(() => null),
      fetchMe({ signal: ctrl.signal }).catch(err => {
        if (err.name === "AbortError") throw err;
        return null;
      }),
    ]).then(([cfg, me]) => {
      setConfig(cfg);
      // Anon visitors must SEE the landing (hero, demo, CTA) and click to sign in —
      // do NOT silently exchange the suite cookie here (that auto-skipped the
      // landing). Suite SSO is click-initiated via ssoLogin() instead.
      if (me) { setUser(me); setStatus("ready"); }
      else setStatus("anon");
    }).catch(err => {
      if (err.name !== "AbortError") setStatus("anon");
    });
    return () => ctrl.abort();
  }, []);

  // Whenever the signed-in user changes (initial load or after login),
  // pull their stored persona. Logout clears the state, not the key —
  // re-logging-in restores their last persona cleanly.
  useEffect(() => {
    setViewAsState(loadPersona(user?.email));
  }, [user?.email]);

  // Any 401 from any endpoint anywhere → drop back to anon.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      setStatus("anon");
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (email, password) => {
    const me = await loginWithPassword({ email, password });
    setUser(me);
    setStatus("ready");
    return me;
  }, []);

  const logout = useCallback(async () => {
    try { await apiLogout(); } catch { /* ignore — clearing state regardless */ }
    setUser(null);
    setStatus("anon");
  }, []);

  // AIQ Suite SSO — click-initiated. If the visitor already has a suite session
  // (jicama_sso cookie from another suite app), exchange it for a native
  // session instantly → into the app. Returns the user, or null when there's no
  // suite session (caller then falls back to Google OAuth). NOT run on boot, so
  // the landing stays visible until the visitor chooses to sign in.
  const ssoLogin = useCallback(async () => {
    try {
      const me = await ssoExchange();
      if (me) { setUser(me); setStatus("ready"); return me; }
    } catch { /* no suite session */ }
    return null;
  }, []);

  // Self-service profile edit (display name today). Persists via PATCH /me and
  // folds the fresh server state back into the in-memory user so the header /
  // chat / settings all update without a reload.
  const updateProfile = useCallback(async (fields) => {
    const me = await updateMe(fields);
    setUser((prev) => ({ ...prev, ...me }));
    return me;
  }, []);

  // Effective roles — either the user's real roles, or a single-role
  // override when persona switching is active. We snap the override back
  // to null if the user doesn't actually hold that role (defence: stops
  // someone localStorage-hacking themselves into seeing admin chrome).
  //
  // useMemo here — and the rolesKey deps below — is the fix for TODO #28.
  // Before this change, effectiveRoles was rebuilt every render (new
  // array identity each time), `effectiveRoles.join(",")` was recomputed
  // for every useCallback dep array, and hasRole/isViewing got fresh
  // identities even when the underlying roles hadn't changed. That
  // cascaded into useEffect storms (App.jsx persona snap-back fired on
  // every state update across the tree). Memoising by a string key
  // breaks the loop.
  const realRoles = user?.roles || [];
  const realRolesKey = realRoles.join(",");
  const effectiveRoles = useMemo(
    () => (viewAs && realRoles.includes(viewAs) ? [viewAs] : realRoles),
    [viewAs, realRolesKey],
  );
  const effectiveRolesKey = effectiveRoles.join(",");

  const hasRole = useCallback((...roles) => {
    if (!user) return false;
    // The owner-implicitly-passes rule applies only to the REAL role set —
    // when viewAs="reviewer", we don't want owner-implicit to leak through.
    if (effectiveRoles.length > 1 && effectiveRoles.includes("owner")) return true;
    if (effectiveRoles.length === 1 && effectiveRoles[0] === "owner") return true;
    return roles.some(r => effectiveRoles.includes(r));
  }, [user, effectiveRolesKey, effectiveRoles]);

  // isViewing(role) · stricter than hasRole. Asks "is the user CURRENTLY
  // working as this role" rather than "can they do role-X things." Use for
  // role-specific workspace surfaces (the Reviewer tab inside Vendor Portal,
  // VendorHome, etc) where an owner with reviewer in their role set should
  // still NOT see reviewer-only UI unless they've persona-toggled to it.
  // Single-role users (e.g. marcus = pure reviewer) trivially pass.
  const isViewing = useCallback(
    (role) => effectiveRoles.length === 1 && effectiveRoles[0] === role,
    [effectiveRolesKey, effectiveRoles],
  );

  const setViewAs = useCallback((role) => {
    setViewAsState(role);
    savePersona(user?.email, role);
  }, [user?.email]);

  // List of persona options the user can switch into — their actual roles,
  // ordered high → low. Returns empty when they only have one role
  // (toggle is hidden by the topbar in that case). Memoised so the topbar
  // doesn't re-render on every keystroke elsewhere.
  const availablePersonas = useMemo(() => {
    const order = ["owner", "admin", "reviewer", "vendor"];
    return order.filter(r => realRoles.includes(r));
  }, [realRolesKey]);

  // P2 · license mode — deployment-wide constant from /api/auth/config.
  const isCloud = config?.licenseMode === "cloud";

  // Memoise the whole context value so consumers don't re-render just
  // because AuthProvider re-rendered for an unrelated reason.
  const ctxValue = useMemo(
    () => ({
      status, user, config, login, logout, ssoLogin, updateProfile, hasRole, isViewing,
      viewAs, setViewAs, availablePersonas, effectiveRoles, isCloud,
    }),
    [status, user, config, login, logout, ssoLogin, updateProfile, hasRole, isViewing,
     viewAs, setViewAs, availablePersonas, effectiveRoles, isCloud],
  );

  return <AuthCtx.Provider value={ctxValue}>{children}</AuthCtx.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
