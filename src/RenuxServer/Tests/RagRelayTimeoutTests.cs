using System.Net;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Http;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;
using RenuxServer.Apis.Chat;
using RenuxServer.DbContexts;
using RenuxServer.Models;

internal static class RagRelayTimeoutTests
{
    private const string SourceRef = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    private const string SourceDocument = "notice:document-123";
    private const string SourceUrl = "https://www.dongguk.edu/article/123";
    private const string Text = "{\"type\":\"text\",\"content\":\"partial answer\"}";

    public static async Task RunAsync(Action<bool, string> check)
    {
        IConfiguration configured = Configuration(new RagRelayTimeoutSettings(
            TimeSpan.FromMilliseconds(500), TimeSpan.FromMilliseconds(120),
            TimeSpan.FromMilliseconds(120), TimeSpan.FromSeconds(1)));
        var shortLimits = RagRelayTimeoutSettings.FromConfiguration(
            configured, "Stream", RagRelayTimeoutSettings.StreamDefaults);
        check(shortLimits.FirstByte == TimeSpan.FromMilliseconds(120)
              && shortLimits.Inactivity == TimeSpan.FromMilliseconds(120)
              && RagRelayTimeoutSettings.FromConfiguration(configured, "Followups",
                  RagRelayTimeoutSettings.FollowupDefaults) == RagRelayTimeoutSettings.FollowupDefaults,
            "Relay timeouts must bind per phase and preserve followup defaults.");

        await VerifyAsync("connect", shortLimits with { Connect = TimeSpan.FromMilliseconds(120) },
            _ => [], DownstreamMode.Normal, expectPartial: false, check,
            headerDelay: TimeSpan.FromMilliseconds(350));

        await VerifyAsync("first_byte", shortLimits,
            _ => [(TimeSpan.FromMilliseconds(350), Event(Text))],
            DownstreamMode.Normal, expectPartial: false, check);

        await VerifyAsync("inactivity", shortLimits,
            id => [(TimeSpan.Zero, Event(Text)), (TimeSpan.FromMilliseconds(350), Event(Completion(id)))],
            DownstreamMode.Normal, expectPartial: true, check);

        // The next upstream chunk is already available after a slow client write.
        // The idle budget must still have elapsed since the previous upstream byte.
        await VerifyAsync("inactivity", shortLimits,
            id => [(TimeSpan.Zero, Event(Text)), (TimeSpan.Zero, Event(Completion(id))),
                   (TimeSpan.Zero, Event(Done(id)))],
            DownstreamMode.SlowFirstWrite, expectPartial: true, check);

        var totalLimits = new RagRelayTimeoutSettings(
            TimeSpan.FromMilliseconds(500), TimeSpan.FromMilliseconds(500),
            TimeSpan.FromMilliseconds(500), TimeSpan.FromMilliseconds(250));
        await VerifyAsync("total", totalLimits,
            id => [(TimeSpan.FromMilliseconds(30), Event(Text)),
                   (TimeSpan.FromMilliseconds(70), Event(Completion(id))),
                   (TimeSpan.FromMilliseconds(300), Event(Done(id)))],
            DownstreamMode.Normal, expectPartial: true, check);

        // A client that stops reading must not leave the relay or persistence pending.
        await VerifyAsync("total", totalLimits,
            id => [(TimeSpan.Zero, Event(Text)), (TimeSpan.Zero, Event(Completion(id))),
                   (TimeSpan.Zero, Event(Done(id)))],
            DownstreamMode.BlockFirstWrite, expectPartial: true, check);

        // EOF alone is not completion: the buffered terminal frames still need
        // to reach the client before persistence is allowed.
        await VerifyAsync("total", totalLimits,
            id => [(TimeSpan.Zero, Event(Text)), (TimeSpan.Zero, Event(Completion(id))),
                   (TimeSpan.Zero, Event(Done(id)))],
            DownstreamMode.BlockTerminalWrite, expectPartial: true, check);

        await VerifyAsync("total", totalLimits,
            id => [(TimeSpan.Zero, Event(Text)), (TimeSpan.Zero, Event(Completion(id))),
                   (TimeSpan.Zero, Event(Done(id)))],
            DownstreamMode.BlockTerminalFlush, expectPartial: true, check);

        // Even a complete terminal sequence cannot persist until the upstream EOF.
        await VerifyAsync("total", totalLimits,
            id => [(TimeSpan.Zero, Event(Text)), (TimeSpan.FromMilliseconds(20), Event(Completion(id))),
                   (TimeSpan.FromMilliseconds(20), Event(Done(id))),
                   (TimeSpan.FromMilliseconds(350), string.Empty)],
            DownstreamMode.Normal, expectPartial: true, check);

        await VerifyAsync(null, shortLimits,
            id => [(TimeSpan.Zero, Event(Text)), (TimeSpan.FromMilliseconds(20), Event(Completion(id))),
                   (TimeSpan.FromMilliseconds(20), Event(Done(id)))],
            DownstreamMode.Normal, expectPartial: true, check);

        await VerifyCompletionMetadataAsync(shortLimits, check);
        await VerifyRetrievalMetadataAsync(shortLimits, check);

        await VerifyIncompleteEofAsync(shortLimits, check);
        await VerifyUnterminatedDataFallbackAsync(shortLimits, check);
        await VerifySlowPersistenceAsync(totalLimits, check);
        await VerifyFollowupSlowWorkAsync(check);
        await VerifyIncompleteTerminalFrameAsync("text/completion separator", shortLimits,
            id => $"data: {Text}\n" + Event(Completion(id)) + Event(Done(id)), check);
        await VerifyIncompleteTerminalFrameAsync("completion separator", shortLimits,
            id => Event(Text) + $"data: {Completion(id)}\n" + Event(Done(id)), check);
        await VerifyIncompleteTerminalFrameAsync("done separator", shortLimits,
            id => Event(Text) + Event(Completion(id)) + $"data: {Done(id)}\n", check);

        var rollingIdleLimits = new RagRelayTimeoutSettings(
            TimeSpan.FromMilliseconds(500), TimeSpan.FromMilliseconds(500),
            TimeSpan.FromMilliseconds(250), TimeSpan.FromSeconds(2));
        await VerifyAsync(null, rollingIdleLimits,
            id => [(TimeSpan.Zero, Event(TextChunk("a"))),
                   (TimeSpan.FromMilliseconds(100), Event(TextChunk("b"))),
                   (TimeSpan.FromMilliseconds(100), Event(TextChunk("c"))),
                   (TimeSpan.FromMilliseconds(100), Event(Completion(id))),
                   (TimeSpan.FromMilliseconds(100), Event(Done(id)))],
            DownstreamMode.Normal, expectPartial: true, check, expectedAnswer: "abc");

        using var delayedHandler = new FakeUpstreamHandler(
            TimeSpan.FromMilliseconds(350), _ => []);
        using var delayedClient = new HttpClient(delayedHandler) { Timeout = Timeout.InfiniteTimeSpan };
        using var connectTimeouts = new RagRelayTimeouts(
            shortLimits with { Connect = TimeSpan.FromMilliseconds(120) }, CancellationToken.None);
        using var request = new HttpRequestMessage(HttpMethod.Post, "http://rag.test/ask/stream");
        string? connectPhase = null;
        try { using var response = await connectTimeouts.SendAsync(delayedClient, request); }
        catch (RagRelayTimeoutException exception) { connectPhase = exception.Phase; }
        await delayedHandler.RequestCompleted.WaitAsync(TimeSpan.FromSeconds(3));
        check(connectPhase == "connect" && delayedHandler.RequestTokenWasCancelled,
            "A connect timeout must cancel the upstream request and identify the connect phase.");
    }

    private static IConfiguration Configuration(RagRelayTimeoutSettings limits) => new ConfigurationBuilder()
        .AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["RagServiceUrl"] = "http://rag.test",
            ["RagRelayTimeouts:Stream:ConnectSeconds"] = limits.Connect.TotalSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture),
            ["RagRelayTimeouts:Stream:FirstByteSeconds"] = limits.FirstByte.TotalSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture),
            ["RagRelayTimeouts:Stream:InactivitySeconds"] = limits.Inactivity.TotalSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture),
            ["RagRelayTimeouts:Stream:TotalSeconds"] = limits.Total.TotalSeconds.ToString(System.Globalization.CultureInfo.InvariantCulture),
        }).Build();

    private static string Event(string json) => $"data: {json}\n\n";
    private static string TextChunk(string content) =>
        System.Text.Json.JsonSerializer.Serialize(new { type = "text", content });
    private static string Completion(string requestId) =>
        $$"""{"type":"completion","request_id":"{{requestId}}","sources":[{"source_ref":"{{SourceRef}}","source":"{{SourceDocument}}","chunk_id":"{{SourceRef}}","url":"{{SourceUrl}}"}],"suggested_questions":["후속 질문"],"suggested_question_details":[{"question":"후속 질문","source_refs":["{{SourceRef}}"]}],"resolved_intents":["notices"],"grounded":true,"grounding_score":0.9,"fallback_reason":null}""";
    private static string Done(string requestId) => $$"""{"type":"done","request_id":"{{requestId}}"}""";

    private static async Task VerifyCompletionMetadataAsync(
        RagRelayTimeoutSettings limits, Action<bool, string> check)
    {
        foreach (var (statusJson, expectedStatus, scoreJson, expectedScore) in new[]
        {
            ("\"passed\"", "passed", "0.8", (double?)0.8),
            ("\"failed\"", "failed", "0.2", (double?)0.2),
            ("\"unavailable\"", "unavailable", "null", (double?)null),
            ("\"not_required\"", "not_required", "null", (double?)null),
            ("\"unknown\"", (string?)null, "1.2", (double?)null),
            ("null", (string?)null, "null", (double?)null),
        })
        {
            using var handler = new FakeUpstreamHandler(TimeSpan.Zero, id =>
            {
                string completion = Completion(id).Replace(
                    "\"grounding_score\":0.9",
                    $"\"grounding_score\":0.9,\"verification_status\":{statusJson},\"relevance_score\":{scoreJson}",
                    StringComparison.Ordinal);
                return [(TimeSpan.Zero, Event(Text)), (TimeSpan.Zero, Event(completion)),
                    (TimeSpan.Zero, Event(Done(id)))];
            });
            using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
            using var downstream = new ControlledDownstream(DownstreamMode.Normal);
            var context = new DefaultHttpContext();
            context.Response.Body = downstream;
            RagStreamRelayResult? persisted = null;
            RagStreamRelayResult result = await ChatRequestApis.RelayRagStreamAsync(
                context, Configuration(limits), client, new RecordingLogger(), "question", "session", null,
                completed =>
                {
                    persisted = completed;
                    return Task.FromResult(true);
                },
                _ => Task.CompletedTask).WaitAsync(TimeSpan.FromSeconds(3));

            check(result.CompletedVersionReady && persisted is not null
                  && persisted.VerificationStatus == expectedStatus
                  && persisted.RelevanceScore == expectedScore,
                $"Completion metadata {statusJson}: relay must pass validated values to persistence.");

            if (persisted is not null)
            {
                var question = new ChatMessage { Id = Guid.NewGuid(), ChatId = Guid.NewGuid(), IsAsk = true };
                DateTime createdTime = DateTime.UtcNow;
                ChatMessage reply = ChatRequestApis.BuildReplyVersion(
                    question, persisted, Guid.NewGuid(), createdTime, createdTime, 1);
                var history = ChatRequestApis.ToDto(reply);
                JsonElement historyJson = JsonSerializer.SerializeToElement(
                    history, new JsonSerializerOptions(JsonSerializerDefaults.Web));
                JsonElement statusProperty = historyJson.GetProperty("verificationStatus");
                JsonElement scoreProperty = historyJson.GetProperty("relevanceScore");
                check(reply.Grounded == true && reply.GroundingScore == 0.9
                      && reply.VerificationStatus == expectedStatus
                      && reply.RelevanceScore == expectedScore
                      && history.Grounded == reply.Grounded
                      && history.GroundingScore == reply.GroundingScore
                      && history.VerificationStatus == expectedStatus
                      && history.RelevanceScore == expectedScore
                      && (expectedStatus is null
                          ? statusProperty.ValueKind == JsonValueKind.Null
                          : statusProperty.GetString() == expectedStatus)
                      && (expectedScore is null
                          ? scoreProperty.ValueKind == JsonValueKind.Null
                          : scoreProperty.GetDouble() == expectedScore),
                    $"Completion metadata {statusJson}: persistence builder and history DTO must retain validated values.");
            }
        }

        using var missingHandler = new FakeUpstreamHandler(TimeSpan.Zero, id =>
            [(TimeSpan.Zero, Event(Text)), (TimeSpan.Zero, Event(Completion(id))),
             (TimeSpan.Zero, Event(Done(id)))]);
        using var missingClient = new HttpClient(missingHandler) { Timeout = Timeout.InfiniteTimeSpan };
        using var missingDownstream = new ControlledDownstream(DownstreamMode.Normal);
        var missingContext = new DefaultHttpContext();
        missingContext.Response.Body = missingDownstream;
        RagStreamRelayResult missingResult = await ChatRequestApis.RelayRagStreamAsync(
            missingContext, Configuration(limits), missingClient, new RecordingLogger(),
            "question", "session", null, _ => Task.FromResult(true),
            _ => Task.CompletedTask).WaitAsync(TimeSpan.FromSeconds(3));
        check(missingResult.CompletedVersionReady && missingResult.VerificationStatus is null
              && missingResult.RelevanceScore is null,
            "Completion metadata absent: status and relevance must stay null.");
    }

    private static async Task VerifyRetrievalMetadataAsync(
        RagRelayTimeoutSettings limits, Action<bool, string> check)
    {
        foreach (var (modeJson, datasetsJson, expectedMode, expectedDatasets) in
            new (string ModeJson, string DatasetsJson, string? ExpectedMode, string[]? ExpectedDatasets)[]
        {
            ("\"hybrid\"", "[]", "hybrid", Array.Empty<string>()),
            ("\"sparse_degraded\"", "[\"notices\",\"courses\"]", "sparse_degraded", new[] { "notices", "courses" }),
            ("\"sparse_only\"", "[]", "sparse_only", Array.Empty<string>()),
            ("\"unknown\"", "[\"bad name\",\"courses\",\"courses\",42,\"../bad\",\"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\"]",
                (string?)null, new[] { "courses" }),
            ("null", "[]", (string?)null, Array.Empty<string>()),
            ("\"hybrid\"", "\"not-an-array\"", "hybrid", null),
        })
        {
            using var handler = new FakeUpstreamHandler(TimeSpan.Zero, id =>
            {
                string completion = Completion(id).Replace(
                    "\"grounding_score\":0.9",
                    $"\"grounding_score\":0.9,\"retrieval_mode\":{modeJson},\"degraded_datasets\":{datasetsJson}",
                    StringComparison.Ordinal);
                return [(TimeSpan.Zero, Event(Text)), (TimeSpan.Zero, Event(completion)),
                    (TimeSpan.Zero, Event(Done(id)))];
            });
            using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
            using var downstream = new ControlledDownstream(DownstreamMode.Normal);
            var context = new DefaultHttpContext();
            context.Response.Body = downstream;
            RagStreamRelayResult? persisted = null;
            RagStreamRelayResult result = await ChatRequestApis.RelayRagStreamAsync(
                context, Configuration(limits), client, new RecordingLogger(), "question", "session", null,
                completed =>
                {
                    persisted = completed;
                    return Task.FromResult(true);
                },
                _ => Task.CompletedTask).WaitAsync(TimeSpan.FromSeconds(3));

            check(result.CompletedVersionReady && persisted is not null
                  && persisted.RetrievalMode == expectedMode
                  && (persisted.DegradedDatasets is null) == (expectedDatasets is null)
                  && (persisted.DegradedDatasets ?? []).SequenceEqual(expectedDatasets ?? []),
                $"Retrieval metadata {modeJson}: relay must validate values before persistence.");

            if (persisted is not null)
            {
                var question = new ChatMessage { Id = Guid.NewGuid(), ChatId = Guid.NewGuid(), IsAsk = true };
                DateTime createdTime = DateTime.UtcNow;
                ChatMessage reply = ChatRequestApis.BuildReplyVersion(
                    question, persisted, Guid.NewGuid(), createdTime, createdTime, 1);
                var history = ChatRequestApis.ToDto(reply);
                JsonElement historyJson = JsonSerializer.SerializeToElement(
                    history, new JsonSerializerOptions(JsonSerializerDefaults.Web));
                check(reply.RetrievalMode == expectedMode
                      && history.RetrievalMode == expectedMode
                      && (history.DegradedDatasets is null) == (expectedDatasets is null)
                      && (history.DegradedDatasets ?? []).SequenceEqual(expectedDatasets ?? [])
                      && historyJson.GetProperty("retrievalMode").GetString() == expectedMode
                      && (expectedDatasets is null
                          ? historyJson.GetProperty("degradedDatasets").ValueKind == JsonValueKind.Null
                          : historyJson.GetProperty("degradedDatasets").GetArrayLength() == expectedDatasets.Length),
                    $"Retrieval metadata {modeJson}: builder and history DTO must retain validated values.");
            }
        }

        using var missingHandler = new FakeUpstreamHandler(TimeSpan.Zero, id =>
            [(TimeSpan.Zero, Event(Text)), (TimeSpan.Zero, Event(Completion(id))),
             (TimeSpan.Zero, Event(Done(id)))]);
        using var missingClient = new HttpClient(missingHandler) { Timeout = Timeout.InfiniteTimeSpan };
        using var missingDownstream = new ControlledDownstream(DownstreamMode.Normal);
        var missingContext = new DefaultHttpContext();
        missingContext.Response.Body = missingDownstream;
        RagStreamRelayResult missing = await ChatRequestApis.RelayRagStreamAsync(
            missingContext, Configuration(limits), missingClient, new RecordingLogger(),
            "question", "session", null, _ => Task.FromResult(true),
            _ => Task.CompletedTask).WaitAsync(TimeSpan.FromSeconds(3));
        check(missing.CompletedVersionReady && missing.RetrievalMode is null
              && missing.DegradedDatasets is null,
            "Older completion events must leave retrieval fields null.");
    }

    public static async Task RunPersistenceAsync(
        DbContextOptions<ServerDbContext> options, Action<bool, string> check)
    {
        Guid chatId = Guid.NewGuid();
        Guid userId = Guid.NewGuid();
        Guid organizationId = Guid.NewGuid();
        Guid majorId = Guid.NewGuid();
        try
        {
            await using (var setup = new ServerDbContext(options))
            {
                Guid roleId = await setup.Roles.Select(role => role.Id).FirstAsync();
                setup.Majors.Add(new Major
                {
                    Id = majorId,
                    Majorname = $"contract-{majorId:N}"
                });
                setup.Organizations.Add(new Organization { Id = organizationId, MajorId = majorId });
                setup.Users.Add(new User
                {
                    Id = userId, MajorId = majorId, RoleId = roleId,
                    UserId = $"contract-{userId:N}", Username = "contract user",
                    HashPassword = "contract-test-only"
                });
                setup.Chats.Add(new ActiveChat
                {
                    Id = chatId, UserId = userId, OrganizationId = organizationId,
                    Title = "verification persistence contract"
                });
                await setup.SaveChangesAsync();
            }

            var limits = new RagRelayTimeoutSettings(
                TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(2),
                TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(10));
            foreach (var (statusJson, expectedStatus, scoreJson, expectedScore) in new[]
            {
                ("\"passed\"", "passed", "0.8", (double?)0.8),
                ("\"unavailable\"", "unavailable", "0.4", (double?)0.4),
                ("\"unknown\"", (string?)null, "0.3", (double?)0.3),
            })
            {
                Guid questionId = Guid.NewGuid();
                await using var writer = new ServerDbContext(options);
                var question = new ChatMessage
                {
                    Id = questionId, ChatId = chatId, IsAsk = true,
                    Content = $"question {questionId:N}"
                };
                writer.ChatMessages.Add(question);
                await writer.SaveChangesAsync();

                using var handler = new FakeUpstreamHandler(TimeSpan.Zero, id =>
                {
                    string completion = Completion(id).Replace(
                        "\"grounding_score\":0.9",
                        $"\"grounding_score\":0.9,\"verification_status\":{statusJson},\"relevance_score\":{scoreJson}",
                        StringComparison.Ordinal);
                    return [(TimeSpan.Zero, Event(Text)), (TimeSpan.Zero, Event(completion)),
                        (TimeSpan.Zero, Event(Done(id)))];
                });
                using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
                using var downstream = new MemoryStream();
                var context = new DefaultHttpContext();
                context.Response.Body = downstream;
                RagStreamRelayResult relay = await ChatRequestApis.RelayRagStreamAsync(
                    context, Configuration(limits), client, new RecordingLogger(),
                    question.Content, chatId.ToString(), null,
                    result => ChatRequestApis.PersistRelayedReplyAsync(writer, question, result),
                    _ => Task.CompletedTask).WaitAsync(TimeSpan.FromSeconds(15));

                await using var reader = new ServerDbContext(options);
                ChatMessage? stored = await reader.ChatMessages.AsNoTracking()
                    .SingleOrDefaultAsync(message => message.ParentQuestionId == questionId && !message.IsAsk);
                List<RenuxServer.Dtos.ChatDtos.ChatMessageDto> history =
                    await ChatRequestApis.MessagesToList(reader, DateTime.UtcNow.AddMinutes(1), chatId);
                var reloaded = history.SingleOrDefault(message => message.Id == stored?.Id);
                JsonElement historyJson = JsonSerializer.SerializeToElement(
                    reloaded, new JsonSerializerOptions(JsonSerializerDefaults.Web));

                check(relay.CompletedVersionReady
                      && stored?.VerificationStatus == expectedStatus
                      && stored?.RelevanceScore == expectedScore
                      && reloaded?.VerificationStatus == expectedStatus
                      && reloaded?.RelevanceScore == expectedScore
                      && historyJson.GetProperty("verificationStatus").GetString() == expectedStatus
                      && historyJson.GetProperty("relevanceScore").GetDouble() == expectedScore,
                    $"Relay persistence/history must round-trip {statusJson} and relevance score.");
            }
        }
        finally
        {
            await using var cleanup = new ServerDbContext(options);
            await cleanup.ChatMessages.Where(message => message.ChatId == chatId && !message.IsAsk)
                .ExecuteDeleteAsync();
            await cleanup.ChatMessages.Where(message => message.ChatId == chatId)
                .ExecuteDeleteAsync();
            await cleanup.Chats.Where(chat => chat.Id == chatId).ExecuteDeleteAsync();
            await cleanup.Users.Where(user => user.Id == userId).ExecuteDeleteAsync();
            await cleanup.Organizations.Where(organization => organization.Id == organizationId)
                .ExecuteDeleteAsync();
            await cleanup.Majors.Where(major => major.Id == majorId).ExecuteDeleteAsync();
        }
    }

    private static async Task VerifyAsync(
        string? expectedPhase, RagRelayTimeoutSettings limits,
        Func<string, (TimeSpan Delay, string Data)[]> chunks,
        DownstreamMode downstreamMode, bool expectPartial, Action<bool, string> check,
        TimeSpan headerDelay = default, string expectedAnswer = "partial answer")
    {
        using var handler = new FakeUpstreamHandler(headerDelay, chunks);
        using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
        using var downstream = new ControlledDownstream(downstreamMode);
        var context = new DefaultHttpContext();
        context.Response.Body = downstream;
        var logger = new RecordingLogger();
        int persistedAnswers = 0;
        int completedEvents = 0;
        RagStreamRelayResult? persistedAnswer = null;
        RagStreamRelayResult result = await ChatRequestApis.RelayRagStreamAsync(
            context, Configuration(limits), client, logger, "question", "session", null,
            completed =>
            {
                persistedAnswers++;
                persistedAnswer = completed;
                return Task.FromResult(true);
            },
            _ => { completedEvents++; return Task.CompletedTask; }).WaitAsync(TimeSpan.FromSeconds(3));

        string label = $"Relay {expectedPhase ?? "normal"}/{downstreamMode}";
        bool expectFallback = expectedPhase is "connect" or "first_byte";
        bool expectSaved = expectedPhase is null;
        check(result.CompletedVersionReady == expectSaved,
            $"{label}: completed answer gate was wrong.");
        check(persistedAnswers == (expectSaved ? 1 : 0),
            $"{label}: persistence callback invocation count was wrong.");
        check(completedEvents == (expectSaved ? 1 : 0),
            $"{label}: AnswerCompleted callback invocation count was wrong.");
        check((result.Answer == expectedAnswer) == expectPartial,
            $"{label}: partial answer state was wrong.");
        if (expectedPhase == "connect")
            await handler.RequestCompleted.WaitAsync(TimeSpan.FromSeconds(3));
        check((expectedPhase == "connect"
                  ? handler.RequestTokenWasCancelled
                  : handler.BodyTokenWasCancelled) == (expectedPhase is not null),
            $"{label}: upstream cancellation token state was wrong.");
        check(expectedPhase is null
                ? result.FallbackReason is null && result.Sources.Count == 1
                  && result.SuggestedQuestions.SequenceEqual(["후속 질문"])
                : result.FallbackReason == $"rag_stream_{expectedPhase}_timeout"
                  && logger.Messages.Any(message => message.Contains($"Phase={expectedPhase}", StringComparison.Ordinal)),
            $"{label}: terminal lineage or timeout phase log was wrong.");
        string forwarded = Encoding.UTF8.GetString(downstream.ToArray());
        // Split at SSE frame boundaries. A data line without its following
        // blank line is not a client-visible event.
        string[] frameParts = forwarded.Split("\n\n", StringSplitOptions.None);
        string[] completeFrames = frameParts[..^1];
        JsonElement[] forwardedEvents = completeFrames
            .Where(frame => frame.StartsWith("data: ", StringComparison.Ordinal))
            .Select(frame => JsonSerializer.Deserialize<JsonElement>(frame[6..]))
            .ToArray();
        string forwardedAnswer = string.Concat(forwardedEvents
            .Where(evt => evt.GetProperty("type").GetString() == "text")
            .Select(evt => evt.GetProperty("content").GetString()));
        if (expectSaved || expectFallback)
        {
            check(frameParts[^1].Length == 0
                  && completeFrames.Length == forwardedEvents.Length
                  && completeFrames.All(frame => frame.StartsWith("data: ", StringComparison.Ordinal)
                                                 && !frame.Contains('\n')),
                $"{label}: every delivered SSE event must have a complete data frame and blank separator.");
            string?[] deliveredTypes = forwardedEvents
                .Select(evt => evt.GetProperty("type").GetString()).ToArray();
            check(expectFallback
                    ? deliveredTypes.SequenceEqual(["metadata", "text", "completion", "done"])
                    : deliveredTypes.Length >= 3
                      && deliveredTypes[..^2].All(type => type == "text")
                      && deliveredTypes[^2] == "completion"
                      && deliveredTypes[^1] == "done",
                $"{label}: complete SSE frames must arrive in the expected order " +
                $"(actual={string.Join(",", deliveredTypes)}).");
            JsonElement[] terminalEvents = forwardedEvents
                .Where(evt => evt.GetProperty("type").GetString() is "completion" or "done")
                .ToArray();
            check(terminalEvents.Length == 2
                  && terminalEvents[0].GetProperty("type").GetString() == "completion"
                  && terminalEvents[1].GetProperty("type").GetString() == "done"
                  && terminalEvents.All(evt => evt.GetProperty("request_id").GetString() == result.RequestId),
                $"{label}: completion -> done and request id must agree with persistence.");
            if (expectFallback)
                check(forwardedEvents.Any(evt => evt.GetProperty("type").GetString() == "text"
                           && evt.GetProperty("content").GetString() == result.Answer)
                      && terminalEvents[0].GetProperty("fallback_reason").GetString() == result.FallbackReason
                      && terminalEvents[0].GetProperty("sources").GetArrayLength() == 0
                      && persistedAnswer is null,
                    $"{label}: graceful fallback must reach the client without persistence.");
            else
                check(terminalEvents[0].GetProperty("sources").GetArrayLength() == 1
                      && terminalEvents[0].GetProperty("sources")[0].GetProperty("source").GetString() == SourceDocument
                      && terminalEvents[0].GetProperty("sources")[0].GetProperty("chunk_id").GetString() == SourceRef
                      && terminalEvents[0].GetProperty("sources")[0].GetProperty("url").GetString() == SourceUrl
                      && persistedAnswer?.Sources.Count == 1
                      && persistedAnswer.Sources[0].Source == SourceDocument
                      && persistedAnswer.Sources[0].ChunkId == SourceRef
                      && persistedAnswer.Sources[0].Url == SourceUrl
                      && persistedAnswer.Answer == forwardedAnswer
                      && persistedAnswer.Answer == expectedAnswer
                      && !persistedAnswer.FallbackTriggered
                      && persistedAnswer.FallbackReason is null,
                    $"{label}: completed answer and source lineage must match the client events.");
        }
        else
        {
            if (downstreamMode == DownstreamMode.BlockTerminalFlush)
                check(forwarded.Contains("\"type\":\"completion\"", StringComparison.Ordinal)
                      && forwarded.Contains("\"type\":\"done\"", StringComparison.Ordinal)
                      && persistedAnswer is null,
                    $"{label}: terminal bytes whose flush stalled must not be persisted.");
            else
                check(!forwarded.Contains("\"type\":\"completion\"", StringComparison.Ordinal)
                      && !forwarded.Contains("\"type\":\"done\"", StringComparison.Ordinal)
                      && (downstreamMode == DownstreamMode.BlockFirstWrite
                          ? forwarded.Length == 0
                          : expectPartial
                              ? forwarded.Contains($"data: {Text}", StringComparison.Ordinal)
                              : forwarded.Length == 0)
                      && persistedAnswer is null,
                    $"{label}: incomplete output must not appear completed or persisted.");
        }
        if (downstreamMode is DownstreamMode.BlockFirstWrite or DownstreamMode.BlockTerminalWrite or DownstreamMode.BlockTerminalFlush)
            check(downstream.WasCancelled, "Blocked downstream terminal delivery must receive total cancellation.");
    }

    private static async Task VerifyIncompleteEofAsync(
        RagRelayTimeoutSettings limits, Action<bool, string> check)
    {
        using var handler = new FakeUpstreamHandler(TimeSpan.Zero, _ => []);
        using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
        using var downstream = new MemoryStream();
        var context = new DefaultHttpContext();
        context.Response.Body = downstream;
        int persistedAnswers = 0;
        int completedEvents = 0;
        RagStreamRelayResult result = await ChatRequestApis.RelayRagStreamAsync(
            context, Configuration(limits), client, new RecordingLogger(), "question", "session", null,
            _ => { persistedAnswers++; return Task.FromResult(true); },
            _ => { completedEvents++; return Task.CompletedTask; }).WaitAsync(TimeSpan.FromSeconds(3));

        string[] frames = Encoding.UTF8.GetString(downstream.ToArray())
            .Split("\n\n", StringSplitOptions.RemoveEmptyEntries);
        JsonElement[] events = frames.Select(frame => JsonSerializer.Deserialize<JsonElement>(frame[6..])).ToArray();
        check(!result.CompletedVersionReady && persistedAnswers == 0 && completedEvents == 0
              && result.FallbackReason == "rag_stream_incomplete"
              && frames.All(frame => frame.StartsWith("data: ", StringComparison.Ordinal) && !frame.Contains('\n'))
              && events.Select(evt => evt.GetProperty("type").GetString())
                  .SequenceEqual(["metadata", "text", "completion", "done"])
              && events[1].GetProperty("content").GetString() == result.Answer
              && events[2].GetProperty("fallback_reason").GetString() == result.FallbackReason
              && events[2].GetProperty("request_id").GetString() == result.RequestId
              && events[3].GetProperty("request_id").GetString() == result.RequestId,
            "A clean incomplete EOF must deliver a complete fallback without persistence or AnswerCompleted.");
    }

    private static async Task VerifyUnterminatedDataFallbackAsync(
        RagRelayTimeoutSettings limits, Action<bool, string> check)
    {
        using var handler = new FakeUpstreamHandler(TimeSpan.Zero,
            id => [(TimeSpan.Zero, $$"""data: {"type":"metadata","request_id":"{{id}}","sources":[]}""")]);
        using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
        using var downstream = new MemoryStream();
        var context = new DefaultHttpContext();
        context.Response.Body = downstream;
        int persistedAnswers = 0;
        int completedEvents = 0;
        RagStreamRelayResult result = await ChatRequestApis.RelayRagStreamAsync(
            context, Configuration(limits), client, new RecordingLogger(), "question", "session", null,
            _ => { persistedAnswers++; return Task.FromResult(true); },
            _ => { completedEvents++; return Task.CompletedTask; }).WaitAsync(TimeSpan.FromSeconds(3));

        string forwarded = Encoding.UTF8.GetString(downstream.ToArray());
        string[] parts = forwarded.Split("\n\n", StringSplitOptions.None);
        string[] frames = parts[..^1];
        JsonElement[] events = frames.Select(frame => JsonSerializer.Deserialize<JsonElement>(frame[6..])).ToArray();
        check(!result.CompletedVersionReady && persistedAnswers == 0 && completedEvents == 0
              && parts[^1].Length == 0
              && frames.Length == 5
              && frames.All(frame => frame.StartsWith("data: ", StringComparison.Ordinal) && !frame.Contains('\n'))
              && events.Select(evt => evt.GetProperty("type").GetString())
                  .SequenceEqual(["metadata", "metadata", "text", "completion", "done"])
              && events[^2].GetProperty("request_id").GetString() == result.RequestId
              && events[^1].GetProperty("request_id").GetString() == result.RequestId,
            "An unterminated upstream data line must be closed before distinct fallback SSE frames.");
    }

    private static async Task VerifySlowPersistenceAsync(
        RagRelayTimeoutSettings limits, Action<bool, string> check)
    {
        using var handler = new FakeUpstreamHandler(TimeSpan.Zero,
            id => [(TimeSpan.Zero, Event(Text)), (TimeSpan.Zero, Event(Completion(id))),
                   (TimeSpan.Zero, Event(Done(id)))]);
        using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
        using var downstream = new MemoryStream();
        var context = new DefaultHttpContext();
        context.Response.Body = downstream;
        int persistedAnswers = 0;
        int completedEvents = 0;
        bool cancellationObservedDuringPersistence = false;
        bool cancellationObservedDuringTelemetry = false;
        RagStreamRelayResult result = await ChatRequestApis.RelayRagStreamAsync(
            context, Configuration(limits), client, new RecordingLogger(), "question", "session", null,
            async _ =>
            {
                persistedAnswers++;
                await Task.Delay(limits.Total + TimeSpan.FromMilliseconds(150));
                cancellationObservedDuringPersistence = context.RequestAborted.IsCancellationRequested
                    || handler.RequestTokenWasCancelled || handler.BodyTokenWasCancelled;
                return true;
            },
            _ =>
            {
                completedEvents++;
                cancellationObservedDuringTelemetry = context.RequestAborted.IsCancellationRequested
                    || handler.RequestTokenWasCancelled || handler.BodyTokenWasCancelled;
                return Task.CompletedTask;
            }).WaitAsync(TimeSpan.FromSeconds(3));

        check(result.CompletedVersionReady && persistedAnswers == 1 && completedEvents == 1
              && !cancellationObservedDuringPersistence
              && !cancellationObservedDuringTelemetry
              && !handler.RequestTokenWasCancelled && !handler.BodyTokenWasCancelled,
            "A completed upstream stream must stop relay timers before slow persistence and telemetry.");
    }

    private static async Task VerifyFollowupSlowWorkAsync(Action<bool, string> check)
    {
        var limits = new RagRelayTimeoutSettings(
            TimeSpan.FromMilliseconds(500), TimeSpan.FromMilliseconds(500),
            TimeSpan.FromMilliseconds(500), TimeSpan.FromMilliseconds(500));
        using var handler = new FakeUpstreamHandler(TimeSpan.Zero,
            id => [(TimeSpan.Zero, $$"""{"request_id":"{{id}}","questions":["followup"]}""")]);
        using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
        using var relayTimeouts = new RagRelayTimeouts(limits, CancellationToken.None);
        using var request = new HttpRequestMessage(HttpMethod.Post, "http://rag.test/followups");
        request.Headers.TryAddWithoutValidation("X-Request-ID", "followup-request");
        using var response = await relayTimeouts.SendAsync(client, request);
        string json = await relayTimeouts.ReadBodyToEndAsync(response);
        RagFollowupResponse? payload = JsonSerializer.Deserialize<RagFollowupResponse>(
            json, new JsonSerializerOptions(JsonSerializerDefaults.Web));

        // Model the endpoint's persistence and completion telemetry after the
        // fully consumed upstream body, beyond the upstream total deadline.
        await Task.Delay(limits.Total + TimeSpan.FromMilliseconds(150));
        bool cancellationDuringPersistence = handler.RequestTokenWasCancelled || handler.BodyTokenWasCancelled;
        await Task.Delay(20);
        bool cancellationDuringTelemetry = handler.RequestTokenWasCancelled || handler.BodyTokenWasCancelled;
        check(payload?.RequestId == "followup-request"
              && payload.Questions?.SequenceEqual(["followup"]) == true
              && !cancellationDuringPersistence && !cancellationDuringTelemetry,
            "Followup relay timers must stop after body consumption and before slow persistence/telemetry.");
    }

    private static async Task VerifyIncompleteTerminalFrameAsync(
        string label, RagRelayTimeoutSettings limits, Func<string, string> body,
        Action<bool, string> check)
    {
        using var handler = new FakeUpstreamHandler(TimeSpan.Zero,
            id => [(TimeSpan.Zero, body(id))]);
        using var client = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
        using var downstream = new MemoryStream();
        var context = new DefaultHttpContext();
        context.Response.Body = downstream;
        int persistedAnswers = 0;
        int completedEvents = 0;
        RagStreamRelayResult result = await ChatRequestApis.RelayRagStreamAsync(
            context, Configuration(limits), client, new RecordingLogger(), "question", "session", null,
            _ =>
            {
                persistedAnswers++;
                return Task.FromResult(true);
            },
            _ => { completedEvents++; return Task.CompletedTask; }).WaitAsync(TimeSpan.FromSeconds(3));

        string forwarded = Encoding.UTF8.GetString(downstream.ToArray());
        check(!result.CompletedVersionReady && persistedAnswers == 0 && completedEvents == 0
              && !forwarded.Contains("\"type\":\"completion\"", StringComparison.Ordinal)
              && !forwarded.Contains("\"type\":\"done\"", StringComparison.Ordinal),
            $"A missing {label} must not deliver terminal events or persist an answer.");
    }

    private enum DownstreamMode { Normal, SlowFirstWrite, BlockFirstWrite, BlockTerminalWrite, BlockTerminalFlush }

    private sealed class ControlledDownstream(DownstreamMode mode) : MemoryStream
    {
        private bool _firstWrite = true;
        private bool _terminalBlocked;
        private bool _terminalWritten;
        public bool WasCancelled { get; private set; }

        private async Task WaitForWriteAsync(ReadOnlyMemory<byte> buffer, CancellationToken cancellationToken)
        {
            bool firstWrite = _firstWrite;
            _firstWrite = false;
            bool terminalFrame = Encoding.UTF8.GetString(buffer.Span)
                .Contains("\"type\":\"completion\"", StringComparison.Ordinal);
            if (mode == DownstreamMode.BlockTerminalFlush && terminalFrame) _terminalWritten = true;
            bool terminalWrite = mode == DownstreamMode.BlockTerminalWrite && !_terminalBlocked && terminalFrame;
            if (terminalWrite) _terminalBlocked = true;
            if (firstWrite || terminalWrite)
            {
                try
                {
                    if (firstWrite && mode == DownstreamMode.SlowFirstWrite)
                        await Task.Delay(170, cancellationToken);
                    else if (firstWrite && mode == DownstreamMode.BlockFirstWrite || terminalWrite)
                        await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
                }
                catch (OperationCanceledException)
                {
                    WasCancelled = true;
                    throw;
                }
            }
        }

        public override async ValueTask WriteAsync(ReadOnlyMemory<byte> buffer, CancellationToken cancellationToken = default)
        {
            await WaitForWriteAsync(buffer, cancellationToken);
            base.Write(buffer.Span);
        }

        public override async Task WriteAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
        {
            await WaitForWriteAsync(buffer.AsMemory(offset, count), cancellationToken);
            base.Write(buffer, offset, count);
        }

        public override async Task FlushAsync(CancellationToken cancellationToken)
        {
            if (mode == DownstreamMode.BlockTerminalFlush && _terminalWritten)
            {
                try { await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken); }
                catch (OperationCanceledException) { WasCancelled = true; throw; }
            }
            await base.FlushAsync(cancellationToken);
        }
    }

    private sealed class RecordingLogger : ILogger
    {
        public List<string> Messages { get; } = [];
        public IDisposable BeginScope<TState>(TState state) where TState : notnull => NullScope.Instance;
        public bool IsEnabled(LogLevel logLevel) => true;
        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state,
            Exception? exception, Func<TState, Exception?, string> formatter)
            => Messages.Add(formatter(state, exception));
        private sealed class NullScope : IDisposable
        {
            public static readonly NullScope Instance = new();
            public void Dispose() { }
        }
    }

    private sealed class FakeUpstreamHandler(
        TimeSpan headerDelay, Func<string, (TimeSpan Delay, string Data)[]> chunks) : HttpMessageHandler
    {
        private ChunkStream? _body;
        private int _cancelled;
        private ObservableContent? _content;
        private CancellationTokenRegistration _cancellationRegistration;
        private readonly TaskCompletionSource _requestCompleted =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
        public bool RequestTokenWasCancelled => Volatile.Read(ref _cancelled) != 0;
        public bool BodyTokenWasCancelled => _content?.TokenWasCancelled == true;
        public Task RequestCompleted => _requestCompleted.Task;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            try
            {
                _cancellationRegistration = cancellationToken.Register(() => Interlocked.Exchange(ref _cancelled, 1));
                await Task.Delay(headerDelay, cancellationToken);
                string requestId = request.Headers.GetValues("X-Request-ID").Single();
                _body = new ChunkStream(chunks(requestId));
                _content = new ObservableContent(_body);
                return new HttpResponseMessage(HttpStatusCode.OK) { Content = _content };
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                // The token's Register callback can still be pending when the
                // cancelled delay resumes here, so record it before signalling.
                Interlocked.Exchange(ref _cancelled, 1);
                throw;
            }
            finally
            {
                _requestCompleted.TrySetResult();
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) _cancellationRegistration.Dispose();
            base.Dispose(disposing);
        }
    }

    private sealed class ObservableContent(Stream body) : HttpContent
    {
        private int _cancelled;
        private CancellationTokenRegistration _registration;
        public bool TokenWasCancelled => Volatile.Read(ref _cancelled) != 0;

        protected override Task<Stream> CreateContentReadStreamAsync(CancellationToken cancellationToken)
        {
            _registration = cancellationToken.Register(() => Interlocked.Exchange(ref _cancelled, 1));
            return Task.FromResult(body);
        }

        protected override Task<Stream> CreateContentReadStreamAsync() => Task.FromResult(body);
        protected override Task SerializeToStreamAsync(Stream stream, TransportContext? context)
            => throw new NotSupportedException();
        protected override bool TryComputeLength(out long length)
        {
            length = 0;
            return false;
        }
        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                _registration.Dispose();
                body.Dispose();
            }
            base.Dispose(disposing);
        }
    }

    private sealed class ChunkStream((TimeSpan Delay, string Data)[] chunks) : Stream
    {
        private int _index;
        private bool _eof;
        public bool WasCancelled { get; private set; }
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override void Flush() => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override int Read(byte[] buffer, int offset, int count) =>
            ReadAsync(buffer.AsMemory(offset, count)).AsTask().GetAwaiter().GetResult();
        public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        {
            if (_index == chunks.Length)
            {
                _eof = true;
                return 0;
            }
            try { await Task.Delay(chunks[_index].Delay, cancellationToken); }
            catch (OperationCanceledException) { WasCancelled = true; throw; }
            byte[] data = Encoding.UTF8.GetBytes(chunks[_index++].Data);
            if (data.Length > buffer.Length) throw new InvalidOperationException("Test chunk exceeds read buffer.");
            data.CopyTo(buffer);
            return data.Length;
        }
        public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
            => ReadAsync(buffer.AsMemory(offset, count), cancellationToken).AsTask();
        protected override void Dispose(bool disposing)
        {
            if (disposing && !_eof) WasCancelled = true;
            base.Dispose(disposing);
        }
    }
}
