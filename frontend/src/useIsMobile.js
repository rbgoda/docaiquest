import { useState, useEffect } from "react";

// True when the viewport is at/below `bp` (default 768px). Re-renders on resize.
// Used to switch fixed desktop layouts (resizable panes, side-by-side) to a
// single stacked column on phones/tablets.
//
// Hardened against the "opens in desktop layout until I sign out/in" bug: some
// load paths (a tab restored from background/bfcache, a viewport that hasn't
// settled, an embed) briefly report a stale/too-large window.innerWidth, which
// rendered the desktop shell and stuck there until a full reload. So we:
//   • take the SMALLEST of innerWidth / documentElement.clientWidth /
//     visualViewport.width (if any signal says "narrow", we're mobile), and
//   • re-measure on resize, orientation, pageshow (bfcache), focus,
//     visibilitychange, visualViewport resize, and a ResizeObserver on <html>.
export function useIsMobile(bp = 768) {
  const compute = () => {
    if (typeof window === "undefined") return false;
    const w = Math.min(
      window.innerWidth || Infinity,
      (document.documentElement && document.documentElement.clientWidth) || Infinity,
      (window.visualViewport && window.visualViewport.width) || Infinity,
    );
    const mq = window.matchMedia(`(max-width: ${bp}px)`).matches;
    return mq || w <= bp;
  };
  const [m, setM] = useState(compute);
  useEffect(() => {
    const on = () => setM(compute());
    const mql = window.matchMedia(`(max-width: ${bp}px)`);
    mql.addEventListener ? mql.addEventListener("change", on) : mql.addListener(on);
    window.addEventListener("resize", on);
    window.addEventListener("orientationchange", on);
    window.addEventListener("pageshow", on);          // bfcache restore
    window.addEventListener("focus", on);             // tab/app return
    document.addEventListener("visibilitychange", on);
    if (window.visualViewport) window.visualViewport.addEventListener("resize", on);
    let ro;
    if (window.ResizeObserver) { ro = new ResizeObserver(on); ro.observe(document.documentElement); }
    on();                                             // re-measure on mount
    const raf = requestAnimationFrame(on);            // and once layout has settled
    return () => {
      mql.removeEventListener ? mql.removeEventListener("change", on) : mql.removeListener(on);
      window.removeEventListener("resize", on);
      window.removeEventListener("orientationchange", on);
      window.removeEventListener("pageshow", on);
      window.removeEventListener("focus", on);
      document.removeEventListener("visibilitychange", on);
      if (window.visualViewport) window.visualViewport.removeEventListener("resize", on);
      if (ro) ro.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [bp]);
  return m;
}
