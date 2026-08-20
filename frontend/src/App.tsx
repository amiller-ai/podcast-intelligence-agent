import { useEffect, useRef, useState, type FormEvent } from "react";
import "./App.css";
import {
  ApiError,
  podcastApi,
  type AnalysisResult,
  type EpisodeDetail,
  type EpisodeSummary,
  type PodcastApi,
  type QuestionResult,
} from "./api/client";

type Confirmation =
  | { kind: "analysis"; refresh: boolean; transcriptRunId: number }
  | { kind: "question"; question: string; transcriptRunId: number };

interface AppProps {
  api?: PodcastApi;
}

function App({ api = podcastApi }: AppProps) {
  const [episodes, setEpisodes] = useState<EpisodeSummary[]>([]);
  const [listStatus, setListStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [listError, setListError] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [detail, setDetail] = useState<EpisodeDetail | null>(null);
  const [detailStatus, setDetailStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [detailError, setDetailError] = useState("");
  const [question, setQuestion] = useState("");
  const [questionResult, setQuestionResult] = useState<QuestionResult | null>(
    null,
  );
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [activeAction, setActiveAction] = useState<
    "analysis" | "question" | null
  >(null);
  const [actionError, setActionError] = useState("");
  const [actionNotice, setActionNotice] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setListStatus("loading");
    api
      .listEpisodes(controller.signal)
      .then((items) => {
        setEpisodes(items);
        setListStatus("ready");
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        setListError(messageFor(error));
        setListStatus("error");
      });
    return () => controller.abort();
  }, [api]);

  useEffect(() => {
    if (selectedRunId === null) {
      setDetail(null);
      setDetailStatus("idle");
      return;
    }
    const controller = new AbortController();
    setDetailStatus("loading");
    setDetailError("");
    setActionError("");
    setActionNotice("");
    setQuestionResult(null);
    api
      .getEpisode(selectedRunId, controller.signal)
      .then((result) => {
        setDetail(result);
        setDetailStatus("ready");
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return;
        setDetailError(messageFor(error));
        setDetailStatus("error");
      });
    return () => controller.abort();
  }, [api, selectedRunId]);

  function selectEpisode(runId: number) {
    if (activeAction !== null || confirmation !== null) return;
    setSelectedRunId(runId);
  }

  function requestAnalysis(refresh: boolean) {
    if (
      selectedRunId === null ||
      activeAction !== null ||
      confirmation !== null
    )
      return;
    setActionError("");
    setActionNotice("");
    setConfirmation({
      kind: "analysis",
      refresh,
      transcriptRunId: selectedRunId,
    });
  }

  function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = question.trim();
    if (!normalized) {
      setActionError("Enter a question before continuing.");
      return;
    }
    if (
      selectedRunId === null ||
      activeAction !== null ||
      confirmation !== null
    )
      return;
    setActionError("");
    setActionNotice("");
    setConfirmation({
      kind: "question",
      question: normalized,
      transcriptRunId: selectedRunId,
    });
  }

  async function confirmAction() {
    const approved = confirmation;
    if (!approved || activeAction !== null) return;
    setConfirmation(null);
    setActionError("");
    setActionNotice("");
    setActiveAction(approved.kind);
    try {
      if (approved.kind === "analysis") {
        const analysis = await api.runAnalysis(
          approved.transcriptRunId,
          approved.refresh,
        );
        setDetail((current) =>
          current?.episode.transcript_run_id === approved.transcriptRunId
            ? { ...current, analysis }
            : current,
        );
        setActionNotice(
          analysis.cache_status === "cached"
            ? "Reused the matching local analysis."
            : "Created and validated a new analysis.",
        );
      } else {
        setQuestionResult(null);
        const result = await api.askQuestion(
          approved.transcriptRunId,
          approved.question,
        );
        setQuestionResult(result);
        setActionNotice(
          "Question answered from validated transcript evidence.",
        );
      }
    } catch (error: unknown) {
      setActionError(messageFor(error));
    } finally {
      setActiveAction(null);
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header" inert={confirmation !== null}>
        <div>
          <p className="eyebrow">Local workspace</p>
          <h1>Podcast Intelligence</h1>
        </div>
        <p className="privacy-note">
          SQLite and credentials stay local. Sending transcript content requires
          confirmation.
        </p>
      </header>

      <main className="workspace" inert={confirmation !== null}>
        <aside
          className="episode-sidebar"
          aria-labelledby="episode-list-heading"
        >
          <div className="section-heading">
            <div>
              <p className="eyebrow">Library</p>
              <h2 id="episode-list-heading">Persisted episodes</h2>
            </div>
            {listStatus === "ready" && (
              <span className="count">{episodes.length}</span>
            )}
          </div>

          <div className="live-region" aria-live="polite">
            {listStatus === "loading" && (
              <p className="status-message">Loading local episodes…</p>
            )}
            {listStatus === "error" && (
              <p role="alert" className="error-message">
                {listError}
              </p>
            )}
            {listStatus === "ready" && episodes.length === 0 && (
              <div className="empty-state">
                <h3>No persisted episodes</h3>
                <p>
                  Use the existing ingestion workflow before opening this
                  workspace.
                </p>
              </div>
            )}
          </div>

          {episodes.length > 0 && (
            <ul className="episode-list">
              {episodes.map((episode) => {
                const selectable = episode.transcript_run_id !== null;
                const selected = episode.transcript_run_id === selectedRunId;
                return (
                  <li key={episode.episode_id}>
                    <button
                      type="button"
                      className="episode-button"
                      aria-pressed={selected}
                      disabled={!selectable || activeAction !== null}
                      onClick={() =>
                        selectable && selectEpisode(episode.transcript_run_id!)
                      }
                    >
                      <span className="episode-title">{episode.title}</span>
                      <span className="episode-status-row">
                        <Status
                          label={
                            episode.transcript_available
                              ? "Transcript ready"
                              : "No transcript"
                          }
                          tone={
                            episode.transcript_available ? "success" : "muted"
                          }
                        />
                        {episode.analysis_available && (
                          <Status label="Analyzed" tone="accent" />
                        )}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <section
          className="episode-workspace"
          aria-labelledby="workspace-heading"
        >
          {selectedRunId === null && (
            <div className="welcome-state">
              <p className="eyebrow">Evidence first</p>
              <h2 id="workspace-heading">Choose an episode</h2>
              <p>
                Select a transcript to review its cached intelligence or ask a
                grounded question.
              </p>
            </div>
          )}

          {detailStatus === "loading" && (
            <p className="status-message" aria-live="polite">
              Loading episode details…
            </p>
          )}
          {detailStatus === "error" && (
            <p role="alert" className="error-message">
              {detailError}
            </p>
          )}

          {detailStatus === "ready" && detail && (
            <>
              <header className="episode-header">
                <div>
                  <p className="eyebrow">
                    Transcript run {detail.episode.transcript_run_id}
                  </p>
                  <h2 id="workspace-heading">{detail.episode.title}</h2>
                  <p className="episode-meta">
                    {detail.episode.transcription_model} · stored{" "}
                    {formatDate(detail.episode.created_at)}
                  </p>
                </div>
                <a
                  className="source-link"
                  href={detail.episode.feed_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  RSS source
                </a>
              </header>

              <div className="action-feedback" aria-live="polite">
                {activeAction && (
                  <p className="status-message">
                    {activeAction === "analysis"
                      ? "Analyzing the selected transcript…"
                      : "Retrieving evidence and answering…"}
                  </p>
                )}
                {actionNotice && (
                  <p className="success-message">{actionNotice}</p>
                )}
                {actionError && (
                  <p role="alert" className="error-message">
                    {actionError}
                  </p>
                )}
              </div>

              <section
                className="content-section"
                aria-labelledby="analysis-heading"
              >
                <div className="section-heading action-heading">
                  <div>
                    <p className="eyebrow">Structured intelligence</p>
                    <h3 id="analysis-heading">Episode analysis</h3>
                  </div>
                  <div className="button-row">
                    {detail.analysis ? (
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={activeAction !== null}
                        onClick={() => requestAnalysis(true)}
                      >
                        Refresh analysis
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="primary-button"
                        disabled={activeAction !== null}
                        onClick={() => requestAnalysis(false)}
                      >
                        Analyze episode
                      </button>
                    )}
                  </div>
                </div>
                {detail.analysis ? (
                  <AnalysisPanel analysis={detail.analysis} />
                ) : (
                  <div className="empty-state compact">
                    <h4>No matching analysis</h4>
                    <p>
                      Create a schema-validated analysis with exact transcript
                      citations.
                    </p>
                  </div>
                )}
              </section>

              <section
                className="content-section"
                aria-labelledby="question-heading"
              >
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">Grounded Q&amp;A</p>
                    <h3 id="question-heading">Ask this episode</h3>
                  </div>
                </div>
                <form className="question-form" onSubmit={submitQuestion}>
                  <label htmlFor="episode-question">Question</label>
                  <textarea
                    id="episode-question"
                    value={question}
                    maxLength={detail.max_question_chars}
                    disabled={activeAction !== null}
                    onChange={(event) => setQuestion(event.target.value)}
                    placeholder="What are the episode's main evidence-backed claims?"
                  />
                  <div className="form-footer">
                    <span>
                      {question.length}/{detail.max_question_chars}
                    </span>
                    <button
                      type="submit"
                      className="primary-button"
                      disabled={activeAction !== null}
                    >
                      Ask question
                    </button>
                  </div>
                </form>
                {questionResult && <QuestionPanel result={questionResult} />}
              </section>
            </>
          )}
        </section>
      </main>

      {confirmation && (
        <ConfirmationDialog
          confirmation={confirmation}
          onCancel={() => setConfirmation(null)}
          onConfirm={confirmAction}
        />
      )}
    </div>
  );
}

function AnalysisPanel({ analysis }: { analysis: AnalysisResult }) {
  const groups: Array<[string, typeof analysis.analysis.topics]> = [
    ["Topics", analysis.analysis.topics],
    ["People", analysis.analysis.people],
    ["Claims", analysis.analysis.claims],
    ["Actionable insights", analysis.analysis.actionable_insights],
  ];
  return (
    <div className="analysis-panel">
      <div className="result-meta">
        <Status
          label={analysis.cache_status === "cached" ? "Cached" : "Created"}
          tone="accent"
        />
        <span>{analysis.model}</span>
        <span>{formatTokens(analysis.usage.total_tokens)} tokens</span>
      </div>
      <EvidenceItem heading="Summary" item={analysis.analysis.summary} />
      <div className="analysis-grid">
        {groups.map(([heading, items]) => (
          <section key={heading} className="analysis-group">
            <h4>{heading}</h4>
            {items.length === 0 ? (
              <p className="muted-copy">No supported items.</p>
            ) : (
              items.map((item, index) => (
                <EvidenceItem key={`${heading}-${index}`} item={item} />
              ))
            )}
          </section>
        ))}
      </div>
      <section className="limitations">
        <h4>Limitations</h4>
        {analysis.analysis.limitations.length === 0 ? (
          <p className="muted-copy">None reported.</p>
        ) : (
          <ul>
            {analysis.analysis.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function EvidenceItem({
  heading,
  item,
}: {
  heading?: string;
  item: AnalysisResult["analysis"]["summary"];
}) {
  return (
    <article className="evidence-item">
      {heading && <h4>{heading}</h4>}
      <p>{item.text}</p>
      <EvidenceList evidence={item.evidence} />
    </article>
  );
}

function EvidenceList({ evidence }: { evidence: QuestionResult["evidence"] }) {
  return (
    <div className="evidence-list">
      {evidence.map((citation) => (
        <blockquote key={`${citation.segment_id}-${citation.quote}`}>
          <p>“{citation.quote}”</p>
          <cite>Segment {citation.segment_id}</cite>
        </blockquote>
      ))}
    </div>
  );
}

function QuestionPanel({ result }: { result: QuestionResult }) {
  return (
    <article className="question-result">
      <div className="result-meta">
        <Status
          label={
            result.insufficient_evidence
              ? "Insufficient evidence"
              : "Evidence supported"
          }
          tone={result.insufficient_evidence ? "warning" : "success"}
        />
        <span>{result.model}</span>
        <span>{formatTokens(result.usage.total_tokens)} tokens</span>
      </div>
      <h4>Answer</h4>
      <p className="answer-copy">{result.answer}</p>
      <EvidenceList evidence={result.evidence} />
      <details className="trace-details">
        <summary>
          Observable trace · {result.tool_calls.length} tool call
          {result.tool_calls.length === 1 ? "" : "s"}
        </summary>
        {result.tool_calls.length === 0 ? (
          <p className="muted-copy">No local retrieval tool was needed.</p>
        ) : (
          <>
            <p className="trace-lineage">
              Response lineage:{" "}
              {result.response_ids.map((responseId) => (
                <code key={responseId}>{responseId}</code>
              ))}
            </p>
            <ul>
              {result.tool_calls.map((call) => (
                <li key={call.call_id}>
                  <strong>
                    <code>{call.tool_name}</code>
                  </strong>
                  <span>
                    Response <code>{call.response_id}</code> · call{" "}
                    <code>{call.call_id}</code>
                  </span>
                  <span>
                    Segments:{" "}
                    {call.result_segment_ids.map((segmentId) => (
                      <code key={segmentId}>{segmentId}</code>
                    ))}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </details>
    </article>
  );
}

function ConfirmationDialog({
  confirmation,
  onCancel,
  onConfirm,
}: {
  confirmation: Confirmation;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    cancelRef.current?.focus();
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
        return;
      }
      if (event.key !== "Tab") return;
      const first = cancelRef.current;
      const last = confirmRef.current;
      if (!first || !last) return;
      const focusIsInside =
        document.activeElement instanceof Node &&
        dialogRef.current?.contains(document.activeElement);
      if (
        event.shiftKey &&
        (document.activeElement === first || !focusIsInside)
      ) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("keydown", handleKey);
      previousFocus?.focus();
    };
  }, [onCancel]);
  const isAnalysis = confirmation.kind === "analysis";
  return (
    <div className="dialog-backdrop">
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="consent-title"
        aria-describedby="consent-description"
        className="consent-dialog"
      >
        <p className="eyebrow">Transmission consent</p>
        <h2 id="consent-title">Send content to OpenAI?</h2>
        <p id="consent-description">
          {isAnalysis
            ? "This may send the selected canonical transcript to OpenAI for structured analysis."
            : "This may send selected transcript excerpts to OpenAI to answer your question."}{" "}
          The API request keeps response storage disabled. Audio is never
          downloaded or retranscribed here.
        </p>
        <div className="dialog-actions">
          <button
            ref={cancelRef}
            type="button"
            className="secondary-button"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className="primary-button"
            onClick={onConfirm}
          >
            I consent, continue
          </button>
        </div>
      </section>
    </div>
  );
}

function Status({
  label,
  tone,
}: {
  label: string;
  tone: "success" | "accent" | "warning" | "muted";
}) {
  return <span className={`status status-${tone}`}>{label}</span>;
}

function messageFor(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "The local application could not complete that request.";
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function formatTokens(value: number | null): string {
  return value === null ? "unknown" : value.toLocaleString();
}

export default App;
