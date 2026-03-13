import React from "react";

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[ErrorBoundary] Caught error:", error);
    console.error("[ErrorBoundary] Component stack:", info.componentStack);
  }

  handleReload = () => {
    window.location.reload();
  };

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div style={containerStyle}>
          <div style={cardStyle}>
            <div style={iconStyle}>!</div>
            <h2 style={titleStyle}>页面渲染出错</h2>
            <p style={messageStyle}>
              应用遇到了意外错误。你可以尝试重新加载页面。
            </p>
            {this.state.error && (
              <pre style={errorDetailStyle}>
                {this.state.error.message}
              </pre>
            )}
            <div style={buttonRowStyle}>
              <button onClick={this.handleReset} style={secondaryBtnStyle}>
                重试
              </button>
              <button onClick={this.handleReload} style={primaryBtnStyle}>
                重新加载
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const containerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  height: "100%",
  width: "100%",
  padding: 32,
};

const cardStyle: React.CSSProperties = {
  background: "var(--bg-surface)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: "48px 40px",
  maxWidth: 460,
  textAlign: "center",
};

const iconStyle: React.CSSProperties = {
  width: 48,
  height: 48,
  borderRadius: "50%",
  background: "var(--accent-subtle)",
  color: "var(--error)",
  fontSize: 24,
  fontWeight: 700,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  marginBottom: 20,
};

const titleStyle: React.CSSProperties = {
  color: "var(--text-primary)",
  fontSize: 20,
  fontWeight: 600,
  margin: "0 0 8px",
  fontFamily: "var(--font-sans)",
};

const messageStyle: React.CSSProperties = {
  color: "var(--text-secondary)",
  fontSize: 14,
  lineHeight: 1.6,
  margin: "0 0 20px",
  fontFamily: "var(--font-sans)",
};

const errorDetailStyle: React.CSSProperties = {
  background: "var(--bg-surface-2)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: "10px 14px",
  fontSize: 12,
  color: "var(--error)",
  textAlign: "left",
  overflow: "auto",
  maxHeight: 120,
  marginBottom: 24,
  fontFamily: "var(--font-mono)",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};

const buttonRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 12,
  justifyContent: "center",
};

const baseBtnStyle: React.CSSProperties = {
  padding: "10px 24px",
  borderRadius: 8,
  fontSize: 14,
  fontWeight: 500,
  cursor: "pointer",
  border: "none",
  fontFamily: "var(--font-sans)",
  transition: "background 0.15s",
};

const primaryBtnStyle: React.CSSProperties = {
  ...baseBtnStyle,
  background: "var(--accent)",
  color: "#fff",
};

const secondaryBtnStyle: React.CSSProperties = {
  ...baseBtnStyle,
  background: "var(--bg-surface-2)",
  color: "var(--text-primary)",
  border: "1px solid var(--border)",
};
