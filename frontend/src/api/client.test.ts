import { describe, expect, it, vi } from "vitest";
import { ApiError, createPodcastApi } from "./client";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("Podcast API client", () => {
  it("uses relative same-origin routes for all public operations", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetchImplementation = vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      calls.push({
        url: url.pathname,
        init: { method: request.method, body: await request.text() },
      });
      if (url.pathname === "/api/episodes")
        return jsonResponse({ episodes: [] });
      if (url.pathname.endsWith("/analysis")) {
        return jsonResponse({
          cache_status: "cached",
          analysis_run_id: 1,
          response_id: "resp",
          model: "model",
          created_at: "2026-08-20T00:00:00Z",
          usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 },
          analysis: {
            summary: { text: "Summary", evidence: [] },
            topics: [],
            people: [],
            claims: [],
            actionable_insights: [],
            limitations: [],
          },
        });
      }
      if (url.pathname.endsWith("/questions")) {
        return jsonResponse({
          cache_status: "not_persisted",
          response_id: "resp",
          response_ids: ["resp"],
          model: "model",
          answer: "Answer",
          insufficient_evidence: false,
          evidence: [],
          usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 },
          tool_calls: [],
        });
      }
      return jsonResponse({
        episode: {
          transcript_run_id: 7,
          transcript_id: 11,
          episode_id: 13,
          feed_url: "https://example.test/feed.xml",
          rss_guid: "guid",
          spotify_episode_id: null,
          title: "Episode",
          transcription_model: "gpt-transcribe",
          created_at: "2026-08-20T00:00:00Z",
        },
        analysis: null,
      });
    }) as unknown as typeof fetch;
    const api = createPodcastApi(fetchImplementation);

    await api.listEpisodes();
    await api.getEpisode(7);
    await api.runAnalysis(7, true);
    await api.askQuestion(7, "What happened?");

    expect(calls.map((call) => call.url)).toEqual([
      "/api/episodes",
      "/api/episodes/7",
      "/api/episodes/7/analysis",
      "/api/episodes/7/questions",
    ]);
    expect(JSON.parse(String(calls[2].init?.body))).toEqual({
      consent: true,
      refresh: true,
    });
    expect(JSON.parse(String(calls[3].init?.body))).toEqual({
      consent: true,
      question: "What happened?",
    });
  });

  it("returns the safe backend error envelope", async () => {
    const api = createPodcastApi(
      vi.fn(async () =>
        jsonResponse(
          {
            error: {
              code: "provider_failed",
              message: "Question answering failed safely.",
            },
          },
          502,
        ),
      ) as unknown as typeof fetch,
    );

    await expect(api.askQuestion(7, "Question")).rejects.toEqual(
      new ApiError(502, "provider_failed", "Question answering failed safely."),
    );
  });

  it("fails closed for malformed error responses", async () => {
    const api = createPodcastApi(
      vi.fn(async () =>
        jsonResponse({ detail: "SENSITIVE" }, 500),
      ) as unknown as typeof fetch,
    );

    await expect(api.listEpisodes()).rejects.toEqual(
      new ApiError(
        500,
        "unexpected_response",
        "The local service returned an unexpected response.",
      ),
    );
  });
});
