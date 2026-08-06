// Generic error boundary — catches render errors in a subtree and shows a
// fallback instead of crashing the whole app.
import React from "react";

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, textAlign: "center" }}>
          <div style={{ fontSize: 24, marginBottom: 8 }}>⚠️</div>
          <div className="serif" style={{ fontSize: 16, color: "var(--ink)" }}>
            Something went wrong
          </div>
          <div className="ink3" style={{ fontSize: 12, marginTop: 6 }}>
            {this.state.error.message || "Unexpected error"}
          </div>
          <button
            onClick={() => this.setState({ error: null })}
            className="border bg2 hover-bg"
            style={{ marginTop: 12, padding: "6px 14px", borderRadius: 999, cursor: "pointer", fontSize: 12 }}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
