import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type EpisodeSummary = components["schemas"]["EpisodeSummaryView"];
export type EpisodeDetail = components["schemas"]["EpisodeDetailResponse"];
export type AnalysisResult = components["schemas"]["AnalysisResultView"];
export type QuestionResult = components["schemas"]["QuestionResultView"];

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export interface PodcastApi {
  listEpisodes(signal?: AbortSignal): Promise<EpisodeSummary[]>;
  getEpisode(
    transcriptRunId: number,
    signal?: AbortSignal,
  ): Promise<EpisodeDetail>;
  runAnalysis(
    transcriptRunId: number,
    refresh: boolean,
    signal?: AbortSignal,
  ): Promise<AnalysisResult>;
  askQuestion(
    transcriptRunId: number,
    question: string,
    signal?: AbortSignal,
  ): Promise<QuestionResult>;
}

export function createPodcastApi(
  fetchImplementation: typeof fetch = fetch,
): PodcastApi {
  const baseUrl =
    typeof window === "undefined" ? "http://localhost" : window.location.origin;
  const client = createClient<paths>({ baseUrl, fetch: fetchImplementation });

  return {
    async listEpisodes(signal) {
      const { data, error, response } = await client.GET("/api/episodes", {
        signal,
      });
      if (!data) throw toApiError(response, error);
      return data.episodes;
    },

    async getEpisode(transcriptRunId, signal) {
      const { data, error, response } = await client.GET(
        "/api/episodes/{transcript_run_id}",
        {
          params: { path: { transcript_run_id: transcriptRunId } },
          signal,
        },
      );
      if (!data) throw toApiError(response, error);
      return data;
    },

    async runAnalysis(transcriptRunId, refresh, signal) {
      const { data, error, response } = await client.POST(
        "/api/episodes/{transcript_run_id}/analysis",
        {
          params: { path: { transcript_run_id: transcriptRunId } },
          body: { consent: true, refresh },
          signal,
        },
      );
      if (!data) throw toApiError(response, error);
      return data;
    },

    async askQuestion(transcriptRunId, question, signal) {
      const { data, error, response } = await client.POST(
        "/api/episodes/{transcript_run_id}/questions",
        {
          params: { path: { transcript_run_id: transcriptRunId } },
          body: { consent: true, question },
          signal,
        },
      );
      if (!data) throw toApiError(response, error);
      return data;
    },
  };
}

function toApiError(response: Response, payload: unknown): ApiError {
  if (isErrorEnvelope(payload)) {
    return new ApiError(
      response.status,
      payload.error.code,
      payload.error.message,
    );
  }
  return new ApiError(
    response.status,
    "unexpected_response",
    "The local service returned an unexpected response.",
  );
}

function isErrorEnvelope(
  value: unknown,
): value is components["schemas"]["ErrorEnvelope"] {
  if (typeof value !== "object" || value === null || !("error" in value))
    return false;
  const error = value.error;
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    typeof error.code === "string" &&
    "message" in error &&
    typeof error.message === "string"
  );
}

export const podcastApi = createPodcastApi();
