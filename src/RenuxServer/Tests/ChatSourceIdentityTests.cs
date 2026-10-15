using System.Net;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using RenuxServer.Apis.Chat;
using RenuxServer.Models;

internal static class ChatSourceIdentityTests
{
    private const string FirstRef = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private const string SecondRef = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    private const string SharedUrl = "https://www.dongguk.edu/article/123";
    private static readonly JsonSerializerOptions WebJson = new(JsonSerializerDefaults.Web);
    private const string Sources = $$"""
        [{"source":"notices","chunk_id":"notice-123:9","source_ref":"{{FirstRef}}","citation_number":9,"url":"{{SharedUrl}}","snippet":"First cited passage"},
         {"source":"notices","chunk_id":"notice-123:2","source_ref":"{{SecondRef}}","citation_number":2,"url":"{{SharedUrl}}","snippet":"Second cited passage"}]
        """;
    private const string OptionalCitations = $$"""
        [{"source":"notices","chunk_id":"notice-123:9","source_ref":"{{FirstRef}}","url":"{{SharedUrl}}","snippet":"First cited passage"},
         {"source":"notices","chunk_id":"notice-123:2","source_ref":"{{SecondRef}}","citation_number":null,"url":"{{SharedUrl}}","snippet":"Second cited passage"}]
        """;
    private const string LegacySources = $$"""
        [{"source":"notices","chunk_id":"notice-123:9","url":"{{SharedUrl}}","snippet":"First cited passage"},
         {"source":"notices","chunk_id":"notice-123:2","source_ref":null,"citation_number":null,"url":"{{SharedUrl}}","snippet":"Second cited passage"}]
        """;

    public static async Task RunAsync(Action<bool, string> check)
    {
        int assertions = 0;
        int failures = 0;
        void Verify(bool condition, string message)
        {
            assertions++;
            if (!condition) failures++;
            check(condition, message);
        }

        // A partial stream exposes the metadata mapping without a completion
        // overwriting it; it must still remain ineligible for persistence.
        await VerifyRelayAsync("metadata", Sources, null, true, true, Verify);
        await VerifyRelayAsync("direct stream", Sources, Sources, true, true, Verify);
        await VerifyRelayAsync("completion replay", null, Sources, true, true, Verify);
        await VerifyRelayAsync("completion replaces metadata", LegacySources, Sources, true, true, Verify);
        await VerifyRelayAsync("optional citation numbers", OptionalCitations, OptionalCitations, true, false, Verify);
        await VerifyRelayAsync("legacy metadata", LegacySources, null, false, false, Verify);

        // These are historical camelCase rows, independent of the new writer.
        const string legacyHistory = $$"""
            [{"source":"notices","chunkId":"notice-123:9","url":"{{SharedUrl}}","snippet":"First cited passage"},
             {"source":"notices","chunkId":"notice-123:2","sourceRef":null,"citationNumber":null,"url":"{{SharedUrl}}","snippet":"Second cited passage"}]
            """;
        var history = ChatRequestApis.ToDto(new ChatMessage { SourcesJson = legacyHistory });
        VerifySources(JsonSerializer.SerializeToElement(history, WebJson).GetProperty("sources"),
            "legacy history", false, false, Verify);

        foreach (string? empty in new[] { null, "null", "[]" })
        {
            var emptyHistory = ChatRequestApis.ToDto(new ChatMessage { SourcesJson = empty });
            Verify(empty == "[]" ? emptyHistory.Sources?.Count == 0 : emptyHistory.Sources is null,
                $"Empty historical sources ({empty ?? "SQL null"}) must retain their existing shape.");
        }

        if (failures == 0)
            Console.WriteLine($"Chat source identity contracts passed (8 scenarios, {assertions} assertions).");
    }

    private static async Task VerifyRelayAsync(
        string scenario, string? metadataSources, string? completionSources,
        bool hasReferences, bool hasCitations, Action<bool, string> check)
    {
        using var handler = new ReplayHandler(id =>
        {
            string metadata = metadataSources is null ? "" : Frame(
                $$"""{"type":"metadata","request_id":"{{id}}","sources":{{metadataSources}},"fallback_triggered":false}""");
            string text = Frame("""{"type":"text","content":"First [문서9], then second [문서2]."}""");
            string terminal = completionSources is null ? "" : Frame($$"""
                {"type":"completion","request_id":"{{id}}","sources":{{completionSources}},"suggested_questions":[],"suggested_question_details":[],"resolved_intents":["notices"],"grounded":null,"grounding_score":null,"fallback_reason":null}
                """) + Frame($$"""{"type":"done","request_id":"{{id}}"}""");
            return metadata + text + terminal;
        });
        using var client = new HttpClient(handler);
        using var downstream = new MemoryStream();
        var context = new DefaultHttpContext();
        context.Response.Body = downstream;
        IConfiguration configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(new Dictionary<string, string?> { ["RagServiceUrl"] = "http://rag.test" })
            .Build();
        ChatMessage? saved = null;
        int persistenceCalls = 0;
        int completionCalls = 0;
        var result = await ChatRequestApis.RelayRagStreamAsync(
            context, configuration, client, NullLogger.Instance, "question", "session", null,
            completed =>
            {
                persistenceCalls++;
                DateTime now = DateTime.UtcNow;
                saved = ChatRequestApis.BuildReplyVersion(
                    new ChatMessage { Id = Guid.NewGuid(), ChatId = Guid.NewGuid(), IsAsk = true },
                    completed, Guid.NewGuid(), now, now, 1);
                return Task.FromResult(true);
            },
            _ => { completionCalls++; return Task.CompletedTask; })
            .WaitAsync(TimeSpan.FromSeconds(5));

        bool completed = completionSources is not null;
        check(result.CompletedVersionReady == completed
              && persistenceCalls == (completed ? 1 : 0)
              && completionCalls == (completed ? 1 : 0),
            $"{scenario}: only a valid terminal stream may persist an answer and record completion.");
        check(Encoding.UTF8.GetString(downstream.ToArray()) == handler.Payload,
            $"{scenario}: forwarded SSE must preserve the exact upstream source payload and order.");
        check(result.Answer == "First [문서9], then second [문서2].",
            $"{scenario}: source mapping must not rewrite answer citations.");
        VerifySources(JsonSerializer.SerializeToElement(result.Sources, WebJson),
            $"{scenario} relay", hasReferences, hasCitations, check);

        if (!completed) return;
        check(saved?.SourcesJson is not null, $"{scenario}: the persistence builder must write SourcesJson.");
        if (saved?.SourcesJson is null) return;
        using var storedJson = JsonDocument.Parse(saved.SourcesJson);
        VerifySources(storedJson.RootElement, $"{scenario} SourcesJson", hasReferences, hasCitations, check);

        // Reload only the serialized field to exercise the real history reader,
        // without a database or sharing the relay's in-memory source objects.
        var history = ChatRequestApis.ToDto(new ChatMessage { SourcesJson = saved.SourcesJson });
        VerifySources(JsonSerializer.SerializeToElement(history, WebJson).GetProperty("sources"),
            $"{scenario} history", hasReferences, hasCitations, check);
    }

    private static void VerifySources(
        JsonElement sources, string stage, bool hasReferences, bool hasCitations,
        Action<bool, string> check)
    {
        check(sources.ValueKind == JsonValueKind.Array && sources.GetArrayLength() == 2,
            $"{stage}: distinct chunks sharing one URL must remain two sources.");
        if (sources.ValueKind != JsonValueKind.Array || sources.GetArrayLength() != 2) return;
        string[] chunks = ["notice-123:9", "notice-123:2"];
        string[] references = [FirstRef, SecondRef];
        int[] citations = [9, 2];
        string[] snippets = ["First cited passage", "Second cited passage"];
        for (int index = 0; index < 2; index++)
        {
            JsonElement source = sources[index];
            check(source.GetProperty("chunkId").GetString() == chunks[index]
                  && source.GetProperty("url").GetString() == SharedUrl
                  && source.GetProperty("source").GetString() == "notices"
                  && source.GetProperty("snippet").GetString() == snippets[index],
                $"{stage}[{index}]: preserve source order, chunk, URL and passage.");
            check(source.TryGetProperty("sourceRef", out var reference)
                  && (hasReferences ? reference.GetString() == references[index] : reference.ValueKind == JsonValueKind.Null),
                $"{stage}[{index}]: preserve the optional stable sourceRef in camelCase.");
            check(source.TryGetProperty("citationNumber", out var citation)
                  && (hasCitations ? citation.GetInt32() == citations[index] : citation.ValueKind == JsonValueKind.Null),
                $"{stage}[{index}]: preserve optional, nonsequential citationNumber without renumbering.");
            check(!source.TryGetProperty("source_ref", out _) && !source.TryGetProperty("citation_number", out _),
                $"{stage}[{index}]: persistence and history must use camelCase, not the RAG wire names.");
        }
    }

    private static string Frame(string json)
        => $"data: {JsonSerializer.Serialize(JsonSerializer.Deserialize<JsonElement>(json))}\n\n";

    private sealed class ReplayHandler(Func<string, string> buildPayload) : HttpMessageHandler
    {
        public string? Payload { get; private set; }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Payload = buildPayload(request.Headers.GetValues("X-Request-ID").Single());
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(Payload, Encoding.UTF8, "text/event-stream")
            });
        }
    }
}
