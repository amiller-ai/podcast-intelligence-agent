import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import {
  ApiError,
  type AnalysisResult,
  type EpisodeDetail,
  type EpisodeSummary,
  type PodcastApi,
  type QuestionResult,
} from "./api/client";

const segmentId = "a".repeat(64);

const episode: EpisodeSummary = {
  episode_id: 13,
  transcript_run_id: 7,
  spotify_episode_id: "spotify-episode",
  title: "Synthetic episode",
  latest_transcription_status: "succeeded",
  transcript_available: true,
  analysis_available: true,
  latest_analysis_status: "succeeded",
};

const analysis: AnalysisResult = {
  cache_status: "cached",
  analysis_run_id: 17,
  response_id: "resp_analysis",
  model: "gpt-5.6-sol",
  created_at: "2026-08-20T00:01:00Z",
  usage: { input_tokens: 100, output_tokens: 50, total_tokens: 150 },
  analysis: {
    summary: {
      text: "Evidence-backed summary.",
      evidence: [{ segment_id: segmentId, quote: "Exact quote" }],
    },
    topics: [
      {
        text: "Evaluation",
        evidence: [{ segment_id: segmentId, quote: "Exact quote" }],
      },
    ],
    people: [],
    claims: [],
    actionable_insights: [],
    limitations: ["No speaker timing."],
  },
};

const detail: EpisodeDetail = {
  episode: {
    transcript_run_id: 7,
    transcript_id: 11,
    episode_id: 13,
    feed_url: "https://example.test/feed.xml",
    rss_guid: "guid-1",
    spotify_episode_id: "spotify-episode",
    title: "Synthetic episode",
    transcription_model: "gpt-transcribe",
    created_at: "2026-08-20T00:00:00Z",
  },
  analysis,
  max_question_chars: 400,
};

const questionResult: QuestionResult = {
  cache_status: "not_persisted",
  response_id: "resp_answer",
  response_ids: ["resp_search", "resp_answer"],
  model: "gpt-5.6-sol",
  answer: "Supported answer.",
  insufficient_evidence: false,
  evidence: [{ segment_id: segmentId, quote: "Exact quote" }],
  usage: { input_tokens: 20, output_tokens: 10, total_tokens: 30 },
  tool_calls: [
    {
      response_id: "resp_search",
      call_id: "call_search",
      tool_name: "search_transcript",
      result_segment_ids: [segmentId],
    },
  ],
};

function fakeApi(overrides: Partial<PodcastApi> = {}): PodcastApi {
  return {
    listEpisodes: vi.fn(async () => [episode]),
    getEpisode: vi.fn(async () => detail),
    runAnalysis: vi.fn(async () => analysis),
    askQuestion: vi.fn(async () => questionResult),
    ...overrides,
  };
}

async function selectEpisode(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("button", { name: /synthetic episode/i }),
  );
  await screen.findByRole("heading", { name: "Synthetic episode", level: 2 });
}

describe("Podcast Intelligence UI", () => {
  it("renders loading and the transcript-safe empty state", async () => {
    const api = fakeApi({ listEpisodes: vi.fn(async () => []) });
    render(<App api={api} />);

    expect(screen.getByText("Loading local episodes…")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "No persisted episodes" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/existing ingestion workflow/i),
    ).toBeInTheDocument();
  });

  it("selects an episode and displays cached analysis with exact citations", async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} />);

    await selectEpisode(user);

    expect(screen.getByText("Evidence-backed summary.")).toBeInTheDocument();
    expect(screen.getAllByText("“Exact quote”").length).toBeGreaterThan(0);
    expect(screen.getAllByText(`Segment ${segmentId}`).length).toBeGreaterThan(
      0,
    );
    expect(screen.getByText("No speaker timing.")).toBeInTheDocument();
    expect(
      screen.queryByText("SENSITIVE FULL TRANSCRIPT"),
    ).not.toBeInTheDocument();
  });

  it("cancels analysis consent without invoking the provider-capable route", async () => {
    const user = userEvent.setup();
    const runAnalysis = vi.fn(async () => analysis);
    render(<App api={fakeApi({ runAnalysis })} />);
    await selectEpisode(user);

    const refreshButton = screen.getByRole("button", {
      name: "Refresh analysis",
    });
    await user.click(refreshButton);
    const dialog = screen.getByRole("dialog", {
      name: "Send content to OpenAI?",
    });
    expect(
      within(dialog).getByText(/canonical transcript/i),
    ).toBeInTheDocument();
    const cancelButton = within(dialog).getByRole("button", { name: "Cancel" });
    const confirmButton = within(dialog).getByRole("button", {
      name: "I consent, continue",
    });
    expect(cancelButton).toHaveFocus();
    await user.tab({ shift: true });
    expect(confirmButton).toHaveFocus();
    await user.tab();
    expect(cancelButton).toHaveFocus();
    await user.click(cancelButton);

    expect(runAnalysis).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(refreshButton).toHaveFocus();
  });

  it("binds consent to the transcript run that opened the dialog", async () => {
    const user = userEvent.setup();
    const secondEpisode: EpisodeSummary = {
      ...episode,
      episode_id: 14,
      transcript_run_id: 8,
      title: "Second episode",
    };
    const runAnalysis = vi.fn(async () => analysis);
    render(
      <App
        api={fakeApi({
          listEpisodes: vi.fn(async () => [episode, secondEpisode]),
          runAnalysis,
        })}
      />,
    );
    await selectEpisode(user);

    await user.click(screen.getByRole("button", { name: "Refresh analysis" }));
    fireEvent.click(screen.getByRole("button", { name: /second episode/i }));
    await user.click(
      screen.getByRole("button", { name: "I consent, continue" }),
    );

    await waitFor(() => expect(runAnalysis).toHaveBeenCalledWith(7, true));
  });

  it("runs consented analysis and renders the created cache state", async () => {
    const user = userEvent.setup();
    const created = { ...analysis, cache_status: "created" as const };
    const runAnalysis = vi.fn(async () => created);
    render(<App api={fakeApi({ runAnalysis })} />);
    await selectEpisode(user);

    await user.click(screen.getByRole("button", { name: "Refresh analysis" }));
    await user.click(
      screen.getByRole("button", { name: "I consent, continue" }),
    );

    await screen.findByText("Created and validated a new analysis.");
    expect(runAnalysis).toHaveBeenCalledWith(7, true);
    expect(screen.getByText("Created")).toBeInTheDocument();
  });

  it("starts missing analysis, supports Escape cancellation, and reuses a cache hit", async () => {
    const user = userEvent.setup();
    const runAnalysis = vi.fn(async () => analysis);
    render(
      <App
        api={fakeApi({
          getEpisode: vi.fn(async () => ({ ...detail, analysis: null })),
          runAnalysis,
        })}
      />,
    );
    await selectEpisode(user);

    await user.click(screen.getByRole("button", { name: "Analyze episode" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(runAnalysis).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Analyze episode" }));
    await user.click(
      screen.getByRole("button", { name: "I consent, continue" }),
    );
    expect(
      await screen.findByText("Reused the matching local analysis."),
    ).toBeInTheDocument();
    expect(runAnalysis).toHaveBeenCalledWith(7, false);
  });

  it("asks a consented question and renders answer, citations, usage, and safe trace", async () => {
    const user = userEvent.setup();
    const askQuestion = vi.fn(async () => questionResult);
    render(<App api={fakeApi({ askQuestion })} />);
    await selectEpisode(user);

    await user.type(screen.getByLabelText("Question"), "What happened?");
    await user.click(screen.getByRole("button", { name: "Ask question" }));
    const dialog = screen.getByRole("dialog", {
      name: "Send content to OpenAI?",
    });
    expect(
      within(dialog).getByText(/selected transcript excerpts/i),
    ).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "I consent, continue" }),
    );

    expect(await screen.findByText("Supported answer.")).toBeInTheDocument();
    expect(screen.getByText("Evidence supported")).toBeInTheDocument();
    expect(screen.getByText("30 tokens")).toBeInTheDocument();
    await user.click(screen.getByText(/observable trace/i));
    expect(screen.getByText("search_transcript")).toBeInTheDocument();
    expect(screen.getAllByText("resp_search")).toHaveLength(2);
    expect(screen.getByText("call_search")).toBeInTheDocument();
    expect(askQuestion).toHaveBeenCalledWith(7, "What happened?");
  });

  it("renders explicit insufficiency and safe backend errors", async () => {
    const user = userEvent.setup();
    const insufficient: QuestionResult = {
      ...questionResult,
      answer: "The transcript does not establish that.",
      insufficient_evidence: true,
      evidence: [],
      tool_calls: [],
    };
    const askQuestion = vi
      .fn<PodcastApi["askQuestion"]>()
      .mockResolvedValueOnce(insufficient)
      .mockRejectedValueOnce(
        new ApiError(
          502,
          "provider_failed",
          "Question answering failed safely.",
        ),
      );
    render(<App api={fakeApi({ askQuestion })} />);
    await selectEpisode(user);

    const field = screen.getByLabelText("Question");
    await user.type(field, "Unknown?");
    await user.click(screen.getByRole("button", { name: "Ask question" }));
    await user.click(
      screen.getByRole("button", { name: "I consent, continue" }),
    );
    expect(
      await screen.findByText("Insufficient evidence"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("The transcript does not establish that."),
    ).toBeInTheDocument();

    await user.clear(field);
    await user.type(field, "Retry?");
    await user.click(screen.getByRole("button", { name: "Ask question" }));
    await user.click(
      screen.getByRole("button", { name: "I consent, continue" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Question answering failed safely.",
    );
    expect(
      screen.queryByText("The transcript does not establish that."),
    ).not.toBeInTheDocument();
  });

  it("uses the backend-provided question limit", async () => {
    const user = userEvent.setup();
    render(
      <App
        api={fakeApi({
          getEpisode: vi.fn(async () => ({
            ...detail,
            max_question_chars: 12,
          })),
        })}
      />,
    );
    await selectEpisode(user);

    const field = screen.getByLabelText("Question");
    await user.type(field, "123456789012345");

    expect(field).toHaveValue("123456789012");
    expect(screen.getByText("12/12")).toBeInTheDocument();
  });

  it("rejects an empty question before consent", async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<App api={api} />);
    await selectEpisode(user);

    await user.click(screen.getByRole("button", { name: "Ask question" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter a question before continuing.",
    );
    expect(api.askQuestion).not.toHaveBeenCalled();
  });

  it("surfaces list and detail failures without exposing error internals", async () => {
    const listApi = fakeApi({
      listEpisodes: vi.fn(async () => {
        throw new Error("SENSITIVE INTERNAL");
      }),
    });
    const { unmount } = render(<App api={listApi} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The local application could not complete that request.",
    );
    expect(screen.queryByText("SENSITIVE INTERNAL")).not.toBeInTheDocument();
    unmount();

    const detailApi = fakeApi({
      getEpisode: vi.fn(async () => {
        throw new ApiError(
          404,
          "episode_not_found",
          "The selected transcript was not found.",
        );
      }),
    });
    const user = userEvent.setup();
    render(<App api={detailApi} />);
    await user.click(
      await screen.findByRole("button", { name: /synthetic episode/i }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The selected transcript was not found.",
    );
  });

  it("prevents duplicate submissions while a question is active", async () => {
    const user = userEvent.setup();
    let resolveQuestion: ((value: QuestionResult) => void) | undefined;
    const askQuestion = vi.fn(
      () =>
        new Promise<QuestionResult>((resolve) => {
          resolveQuestion = resolve;
        }),
    );
    render(<App api={fakeApi({ askQuestion })} />);
    await selectEpisode(user);
    await user.type(screen.getByLabelText("Question"), "Pending?");
    await user.click(screen.getByRole("button", { name: "Ask question" }));
    await user.click(
      screen.getByRole("button", { name: "I consent, continue" }),
    );

    expect(screen.getByRole("button", { name: "Ask question" })).toBeDisabled();
    expect(askQuestion).toHaveBeenCalledTimes(1);
    resolveQuestion?.(questionResult);
    await waitFor(() =>
      expect(screen.getByText("Supported answer.")).toBeInTheDocument(),
    );
  });
});
