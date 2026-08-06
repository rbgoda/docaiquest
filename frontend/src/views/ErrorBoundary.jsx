// Minimal error boundary — wraps a child subtree and renders the error
// stack inline instead of letting React unmount the page. Use around
// any tab body you suspect of runtime errors.

import React from "react";

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    this.setState({ info });
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", error, info);
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="bg1 border rounded-md p-4" style={{
        background: "rgba(216,98,94,0.10)",
        borderColor: "rgba(216,98,94,0.55)",
        fontSize: 12, lineHeight: 1.5, maxWidth: 900,
      }}>
        <div className="upper" style={{ fontSize: 10, letterSpacing: 0.6, color: "#D8625E", fontWeight: 700 }}>
          ⚠ Error rendering this tab
        </div>
        <div className="mono mt-2" style={{ fontSize: 12, color: "var(--ink)" }}>
          {String(this.state.error?.message || this.state.error)}
        </div>
        {this.state.info?.componentStack && (
          <pre className="mt-3 ink2" style={{
            fontSize: 10, whiteSpace: "pre-wrap",
            background: "var(--bg2)", padding: 8, borderRadius: 4,
            maxHeight: 300, overflow: "auto",
          }}>{this.state.info.componentStack}</pre>
        )}
        <button onClick={() => this.setState({ error: null, info: null })}
                className="btn-gold mt-3"
                style={{ padding: "6px 14px", borderRadius: 4, fontSize: 12, cursor: "pointer" }}>
          Try again
        </button>
      </div>
    );
  }
}
