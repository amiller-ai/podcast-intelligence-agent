import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  failed: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // The browser UI deliberately avoids logging transcript-bearing render inputs.
  }

  render() {
    if (this.state.failed) {
      return (
        <main className="welcome-state" role="alert">
          <p className="eyebrow">Safe failure</p>
          <h1>Podcast Intelligence could not render this view.</h1>
          <p>Reload the local application. No transcript content was logged.</p>
        </main>
      );
    }
    return this.props.children;
  }
}
