from typing import cast
from unittest.mock import Mock

import pytest
from openai import OpenAI, OpenAIError
from openai.types.responses import (
    Response,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningItem,
)

from podcast_intelligence.intelligence_models import TranscriptSegment
from podcast_intelligence.persistence import StoredTranscript
from podcast_intelligence.responses_client import (
    PodcastResponsesClient,
    ResponsesContextBudgetError,
    ResponsesIncompleteError,
    ResponsesOutputValidationError,
    ResponsesProviderRequestError,
    ResponsesToolLoopError,
    TextResponse,
)
from podcast_intelligence.retrieval import (
    RetrievalToolError,
    ToolExecution,
    TranscriptTools,
)
from podcast_intelligence.settings import Settings


def test_create_text_response_uses_configured_request_contract() -> None:
    sdk_response = Mock(spec=Response)
    sdk_response.id = "resp_test"
    sdk_response.model = "gpt-5.6-sol"
    sdk_response.output_text = "podcast-intelligence-ok"
    sdk_client = Mock(spec=OpenAI)
    sdk_client.responses.create.return_value = sdk_response
    settings = Settings.model_validate({"openai_api_key": "test-key"})
    client = PodcastResponsesClient(settings, client=cast(OpenAI, sdk_client))

    result = client.create_text_response("  test the client  ")

    assert result == TextResponse(
        response_id="resp_test",
        model="gpt-5.6-sol",
        text="podcast-intelligence-ok",
    )
    sdk_client.responses.create.assert_called_once_with(
        model="gpt-5.6-sol",
        input="test the client",
        reasoning={"effort": "medium"},
        store=False,
    )


def test_create_text_response_rejects_empty_prompt_without_api_call() -> None:
    sdk_client = Mock(spec=OpenAI)
    settings = Settings.model_validate({"openai_api_key": "test-key"})
    client = PodcastResponsesClient(settings, client=cast(OpenAI, sdk_client))

    with pytest.raises(ValueError, match="prompt must not be empty"):
        client.create_text_response("  ")

    sdk_client.responses.create.assert_not_called()


def _response(
    response_id: str,
    output: list[object],
    *,
    output_text: str = "",
    status: str = "completed",
) -> Mock:
    response = Mock(spec=Response)
    response.id = response_id
    response.model = "gpt-5.6-sol"
    response.status = status
    response.output = output
    response.output_text = output_text
    response.usage = None
    return response


def _call(call_id: str, name: str, arguments: str) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        type="function_call",
        call_id=call_id,
        name=name,
        arguments=arguments,
    )


def _reasoning(item_id: str) -> ResponseReasoningItem:
    return ResponseReasoningItem(
        id=item_id,
        type="reasoning",
        summary=[],
        encrypted_content="opaque-encrypted-reasoning",
    )


def _message(item_id: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id=item_id,
        type="message",
        role="assistant",
        status="completed",
        content=[
            ResponseOutputText(
                type="output_text",
                text="structured answer",
                annotations=[],
            )
        ],
    )


def _mock_tools() -> Mock:
    tools = Mock(spec=TranscriptTools)
    tools.transcript = Mock(spec=StoredTranscript)
    tools.transcript.transcript_id = 1
    tools.transcript.episode_id = 1
    tools.transcript.episode_title = "Synthetic episode"
    tools.segments = (
        TranscriptSegment(
            segment_id="a" * 64,
            transcript_id=1,
            episode_id=1,
            ordinal=0,
            char_start=0,
            char_end=23,
            text="Use a versioned corpus.",
            text_hash="b" * 64,
            transcript_content_hash="c" * 64,
            segmenter_version="bounded-text-v1",
        ),
    )
    tools.tool_definitions.return_value = (
        {
            "type": "function",
            "name": "search_transcript",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    )
    tools.execute.return_value = ToolExecution(
        name="search_transcript",
        output_json='{"results":[]}',
        segment_ids=("a" * 64,),
    )
    return tools


def test_stateless_tool_loop_replays_complete_output_and_matching_call_id() -> None:
    first = _response(
        "resp_1",
        [
            _reasoning("reason_1"),
            _call(
                "call_1",
                "search_transcript",
                '{"episode_id":1,"query":"evaluation","limit":3}',
            ),
        ],
    )
    answer_json = (
        '{"answer":"Use a versioned corpus.","evidence":'
        '[{"segment_id":"' + ("a" * 64) + '","quote":"versioned corpus"}],'
        '"insufficient_evidence":false}'
    )
    final = _response("resp_2", [_message("msg_1")], output_text=answer_json)
    sdk_client = Mock(spec=OpenAI)
    sdk_client.responses.create.side_effect = [first, final]
    settings = Settings.model_validate({"openai_api_key": "test-key"})
    tools = _mock_tools()
    client = PodcastResponsesClient(settings, client=cast(OpenAI, sdk_client))

    result = client.answer_question("What evaluation approach is recommended?", tools)

    assert result.response_ids == ("resp_1", "resp_2")
    assert result.output_item_types == (("reasoning", "function_call"), ("message",))
    assert result.tool_calls[0].call_id == "call_1"
    assert result.tool_calls[0].result_segment_ids == ("a" * 64,)
    assert result.answer.answer == "Use a versioned corpus."
    assert tools.validate_answer.call_count == 1
    assert sdk_client.responses.create.call_count == 2
    first_request = sdk_client.responses.create.call_args_list[0].kwargs
    second_request = sdk_client.responses.create.call_args_list[1].kwargs
    assert first_request["store"] is False
    assert first_request["parallel_tool_calls"] is False
    assert first_request["include"] == ["reasoning.encrypted_content"]
    assert "character-for-character substring" in first_request["instructions"]
    assert "run ID is a different identifier" in first_request["instructions"]
    assert "previous_response_id" not in second_request
    replay = second_request["input"]
    assert [item["type"] for item in replay[1:]] == [
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert replay[-1]["call_id"] == "call_1"


def test_direct_abstention_requires_no_tool_call() -> None:
    final = _response(
        "resp_direct",
        [_message("msg_direct")],
        output_text=(
            '{"answer":"Evidence is insufficient.","evidence":[],"insufficient_evidence":true}'
        ),
    )
    sdk_client = Mock(spec=OpenAI)
    sdk_client.responses.create.return_value = final
    tools = _mock_tools()
    client = PodcastResponsesClient(
        Settings.model_validate({"openai_api_key": "test-key"}),
        client=cast(OpenAI, sdk_client),
    )

    result = client.answer_question("What is not discussed?", tools)

    assert result.answer.insufficient_evidence is True
    assert result.tool_calls == ()
    tools.execute.assert_not_called()


def test_multiple_sequential_tool_calls_preserve_each_response_lineage() -> None:
    first = _response(
        "resp_1",
        [_call("call_meta", "get_episode_metadata", '{"episode_id":1}')],
    )
    second = _response(
        "resp_2",
        [
            _reasoning("reason_2"),
            _call(
                "call_search",
                "search_transcript",
                '{"episode_id":1,"query":"retrieval","limit":2}',
            ),
        ],
    )
    final = _response(
        "resp_3",
        [_message("msg_3")],
        output_text=(
            '{"answer":"Evidence is insufficient.","evidence":[],"insufficient_evidence":true}'
        ),
    )
    sdk_client = Mock(spec=OpenAI)
    sdk_client.responses.create.side_effect = [first, second, final]
    tools = _mock_tools()
    client = PodcastResponsesClient(
        Settings.model_validate({"openai_api_key": "test-key"}),
        client=cast(OpenAI, sdk_client),
    )

    result = client.answer_question("Summarize the retrieval guidance.", tools)

    assert result.response_ids == ("resp_1", "resp_2", "resp_3")
    assert [trace.call_id for trace in result.tool_calls] == ["call_meta", "call_search"]
    third_input = sdk_client.responses.create.call_args_list[2].kwargs["input"]
    assert [
        item["call_id"] for item in third_input if item.get("type") == "function_call_output"
    ] == ["call_meta", "call_search"]


def test_invalid_tool_call_is_an_application_owned_failure() -> None:
    response = _response(
        "resp_bad",
        [_call("call_bad", "search_transcript", "not-json")],
    )
    sdk_client = Mock(spec=OpenAI)
    sdk_client.responses.create.return_value = response
    tools = _mock_tools()
    tools.execute.side_effect = RetrievalToolError("tool arguments are not valid JSON")
    client = PodcastResponsesClient(
        Settings.model_validate({"openai_api_key": "test-key"}),
        client=cast(OpenAI, sdk_client),
    )

    with pytest.raises(
        ResponsesToolLoopError,
        match=r"invalid retrieval.*arguments are not valid JSON",
    ):
        client.answer_question("Question", tools)


def test_parallel_duplicate_and_exhausted_tool_calls_fail_closed() -> None:
    parallel = _response(
        "resp_parallel",
        [
            _call("call_1", "search_transcript", "{}"),
            _call("call_2", "search_transcript", "{}"),
        ],
    )
    duplicate_first = _response(
        "resp_dup_1",
        [_call("same_call", "search_transcript", "{}")],
    )
    duplicate_second = _response(
        "resp_dup_2",
        [_call("same_call", "search_transcript", "{}")],
    )
    tools = _mock_tools()

    parallel_sdk = Mock(spec=OpenAI)
    parallel_sdk.responses.create.return_value = parallel
    with pytest.raises(ResponsesToolLoopError, match="parallel"):
        PodcastResponsesClient(
            Settings.model_validate({"openai_api_key": "test-key"}),
            client=cast(OpenAI, parallel_sdk),
        ).answer_question("Question", tools)

    duplicate_sdk = Mock(spec=OpenAI)
    duplicate_sdk.responses.create.side_effect = [duplicate_first, duplicate_second]
    with pytest.raises(ResponsesToolLoopError, match="duplicate"):
        PodcastResponsesClient(
            Settings.model_validate(
                {"openai_api_key": "test-key", "intelligence_max_tool_calls": 2}
            ),
            client=cast(OpenAI, duplicate_sdk),
        ).answer_question("Question", tools)

    exhausted_sdk = Mock(spec=OpenAI)
    exhausted_sdk.responses.create.side_effect = [duplicate_first, duplicate_second]
    with pytest.raises(ResponsesToolLoopError, match="exhausted"):
        PodcastResponsesClient(
            Settings.model_validate(
                {"openai_api_key": "test-key", "intelligence_max_tool_calls": 1}
            ),
            client=cast(OpenAI, exhausted_sdk),
        ).answer_question("Question", tools)


def test_incomplete_or_schema_invalid_final_response_fails_closed() -> None:
    sdk_client = Mock(spec=OpenAI)
    sdk_client.responses.create.return_value = _response(
        "resp_incomplete",
        [],
        status="incomplete",
    )
    client = PodcastResponsesClient(
        Settings.model_validate({"openai_api_key": "test-key"}),
        client=cast(OpenAI, sdk_client),
    )
    with pytest.raises(ResponsesIncompleteError, match="incomplete") as incomplete:
        client.answer_question("Question", _mock_tools())
    assert incomplete.value.response_id == "resp_incomplete"

    sdk_client.responses.create.return_value = _response(
        "resp_invalid",
        [_message("msg_invalid")],
        output_text='{"answer":"missing fields"}',
    )
    with pytest.raises(ResponsesOutputValidationError, match="evidence validation"):
        client.answer_question("Question", _mock_tools())


def test_episode_analysis_classifies_provider_request_failure() -> None:
    sdk_client = Mock(spec=OpenAI)
    sdk_client.responses.create.side_effect = OpenAIError("SENSITIVE PROVIDER DETAIL")
    client = PodcastResponsesClient(
        Settings.model_validate({"openai_api_key": "test-key"}),
        client=cast(OpenAI, sdk_client),
    )
    tools = _mock_tools()

    with pytest.raises(ResponsesProviderRequestError, match="provider request failed"):
        client.create_episode_analysis(tools.transcript, tools.segments)


def test_structured_episode_analysis_uses_strict_schema_and_exact_evidence() -> None:
    segment_id = "b" * 64
    segment = TranscriptSegment(
        segment_id=segment_id,
        transcript_id=7,
        episode_id=3,
        ordinal=0,
        char_start=0,
        char_end=51,
        text="A versioned corpus makes prompt changes measurable.",
        text_hash="c" * 64,
        transcript_content_hash="d" * 64,
        segmenter_version="bounded-text-v1",
    )
    transcript = Mock(spec=StoredTranscript)
    transcript.transcript_id = 7
    transcript.episode_id = 3
    transcript.episode_title = "Evaluation episode"
    evidence = f'{{"segment_id":"{segment_id}","quote":"VERSIONED CORPUS"}}'
    item = f'{{"text":"Use a versioned corpus.","evidence":[{evidence}]}}'
    analysis_json = (
        f'{{"summary":{item},"topics":[{item}],"people":[],"claims":[{item}],'
        f'"actionable_insights":[{item}],"limitations":["No speaker timing."]}}'
    )
    sdk_client = Mock(spec=OpenAI)
    sdk_client.responses.create.return_value = _response(
        "resp_analysis",
        [_message("msg_analysis")],
        output_text=analysis_json,
    )
    client = PodcastResponsesClient(
        Settings.model_validate({"openai_api_key": "test-key"}),
        client=cast(OpenAI, sdk_client),
    )

    result = client.create_episode_analysis(transcript, (segment,))

    assert result.response_id == "resp_analysis"
    assert result.analysis.summary.text == "Use a versioned corpus."
    assert result.analysis.summary.evidence[0].quote == "versioned corpus"
    request = sdk_client.responses.create.call_args.kwargs
    assert request["store"] is False
    assert request["text"]["format"]["type"] == "json_schema"
    assert request["text"]["format"]["strict"] is True
    assert "character-for-character substring" in request["instructions"]
    quote_schema = request["text"]["format"]["schema"]["$defs"]["AnalysisEvidence"]["properties"][
        "quote"
    ]
    assert "character-for-character substring" in quote_schema["description"]
    assert segment_id in request["input"][0]["content"]


def test_structured_episode_analysis_rejects_inexact_evidence_and_context_overflow() -> None:
    segment = TranscriptSegment(
        segment_id="b" * 64,
        transcript_id=7,
        episode_id=3,
        ordinal=0,
        char_start=0,
        char_end=12,
        text="Exact quote.",
        text_hash="c" * 64,
        transcript_content_hash="d" * 64,
        segmenter_version="bounded-text-v1",
    )
    transcript = Mock(spec=StoredTranscript)
    transcript.transcript_id = 7
    transcript.episode_id = 3
    transcript.episode_title = "Evaluation episode"
    evidence = f'{{"segment_id":"{segment.segment_id}","quote":"invented"}}'
    item = f'{{"text":"Unsupported.","evidence":[{evidence}]}}'
    invalid_json = (
        f'{{"summary":{item},"topics":[],"people":[],"claims":[],'
        f'"actionable_insights":[],"limitations":[]}}'
    )
    sdk_client = Mock(spec=OpenAI)
    sdk_client.responses.create.return_value = _response(
        "resp_invalid_analysis",
        [_message("msg_invalid_analysis")],
        output_text=invalid_json,
    )
    client = PodcastResponsesClient(
        Settings.model_validate({"openai_api_key": "test-key"}),
        client=cast(OpenAI, sdk_client),
    )
    with pytest.raises(ResponsesOutputValidationError, match="evidence validation") as raised:
        client.create_episode_analysis(transcript, (segment,))
    assert raised.value.response_id == "resp_invalid_analysis"
    assert raised.value.usage.total_tokens == 0

    bounded_client = PodcastResponsesClient(
        Settings.model_validate(
            {"openai_api_key": "test-key", "intelligence_max_analysis_chars": 10}
        ),
        client=cast(OpenAI, sdk_client),
    )
    with pytest.raises(ResponsesContextBudgetError, match="context budget"):
        bounded_client.create_episode_analysis(transcript, (segment,))
