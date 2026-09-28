using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Http;
using Microsoft.EntityFrameworkCore.Migrations.Operations;
using System.Text.Json;

using RenuxServer.Apis;
using RenuxServer.Apis.Chat;
using RenuxServer.DbContexts;
using RenuxServer.Dtos.ChatDtos;
using RenuxServer.Models;
using RenuxServer.Services;

var failures = new List<string>();

void Check(bool condition, string message)
{
    if (!condition) failures.Add(message);
}

JsonElement JsonEvent(string json) => JsonDocument.Parse(json).RootElement.Clone();

string CompletionJson(string requestId) => $$"""
    {"type":"completion","request_id":"{{requestId}}","sources":[{"source_ref":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}],"suggested_questions":["후속 질문"],"suggested_question_details":[{"question":"후속 질문","source_refs":["sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]}],"resolved_intents":["notices"],"grounded":true,"grounding_score":0.9,"fallback_reason":null}
    """;

var firstConfig = new ConfigurationBuilder()
    .AddInMemoryCollection(new Dictionary<string, string?>
    {
        ["Telemetry:HmacKey"] = Convert.ToBase64String("telemetry-test-key-with-enough-entropy"u8.ToArray()),
        ["Telemetry:KeyId"] = "2026a",
    })
    .Build();
var rotatedConfig = new ConfigurationBuilder()
    .AddInMemoryCollection(new Dictionary<string, string?>
    {
        ["Telemetry:HmacKey"] = Convert.ToBase64String("rotated-telemetry-test-secret-value"u8.ToArray()),
        ["Telemetry:KeyId"] = "2026b",
    })
    .Build();

string rawUserId = "student-direct-identifier";
string firstKey = ProductTelemetry.BuildPseudonymousKey(firstConfig, "subject:user", rawUserId);
string repeatKey = ProductTelemetry.BuildPseudonymousKey(firstConfig, "subject:user", rawUserId);
string rotatedKey = ProductTelemetry.BuildPseudonymousKey(rotatedConfig, "subject:user", rawUserId);
Check(firstKey == repeatKey, "Pseudonymous keys must be deterministic within one key version.");
Check(!firstKey.Contains(rawUserId, StringComparison.Ordinal), "Pseudonymous keys must not contain raw identifiers.");
Check(firstKey.StartsWith("2026a.", StringComparison.Ordinal), "Pseudonymous keys must carry their rotation key id.");
Check(rotatedKey.StartsWith("2026b.", StringComparison.Ordinal) && rotatedKey != firstKey,
    "Rotating the server secret/key id must rotate pseudonymous keys.");

Check(ProductTelemetry.IsValidEventData(new ProductEventData(
    ProductEventTypes.AnswerCompleted,
    "request-1",
    SuggestionCount: 3,
    SourceCount: 5,
    IsFallback: false,
    Grounded: true)), "The allowlisted answer completion shape should be valid.");
Check(!ProductTelemetry.IsValidEventData(new ProductEventData(
    "arbitrary_event",
    "request-1")), "Unknown event types must be rejected.");
Check(!ProductTelemetry.IsValidEventData(new ProductEventData(
    ProductEventTypes.SuggestionClicked,
    "request-1",
    SuggestionIndex: 99)), "Out-of-range suggestion properties must be rejected.");
Check(SuggestionIntegrity.Matches(ProductEventTypes.SuggestionShown, 3, 3, null),
    "A shown event must match the stored suggestion count.");
Check(!SuggestionIntegrity.Matches(ProductEventTypes.SuggestionShown, 3, 10, null),
    "A client must not inflate a stored suggestion count of three to ten.");
Check(!SuggestionIntegrity.Matches(ProductEventTypes.SuggestionClicked, 3, null, 9),
    "A click outside the stored suggestion range must be rejected.");
Check(FeedbackPolicy.Decide(null, 1) == FeedbackDecision.Accept,
    "The first feedback rating should be accepted.");
Check(FeedbackPolicy.Decide(1, 1) == FeedbackDecision.Duplicate,
    "Replaying the same rating should be idempotent.");
Check(FeedbackPolicy.Decide(1, -1) == FeedbackDecision.Conflict,
    "Flipping an accepted rating should be rejected.");

var successfulTerminal = new RagTerminalStateMachine("backend-request");
Check(successfulTerminal.Observe(JsonEvent(CompletionJson("backend-request")), out _), "A valid completion should be accepted.");
Check(successfulTerminal.Observe(JsonEvent("{\"type\":\"done\",\"request_id\":\"backend-request\"}"), out _), "A matching done should be accepted.");
Check(successfulTerminal.IsSuccessful, "Exactly one completion followed by one done should succeed.");

var duplicateCompletion = new RagTerminalStateMachine("backend-request");
duplicateCompletion.Observe(JsonEvent(CompletionJson("backend-request")), out _);
duplicateCompletion.Observe(JsonEvent(CompletionJson("backend-request")), out _);
duplicateCompletion.Observe(JsonEvent("{\"type\":\"done\",\"request_id\":\"backend-request\"}"), out _);
Check(!duplicateCompletion.IsSuccessful, "completion -> completion -> done must fail.");

var doneBeforeCompletion = new RagTerminalStateMachine("backend-request");
doneBeforeCompletion.Observe(JsonEvent("{\"type\":\"done\",\"request_id\":\"backend-request\"}"), out _);
doneBeforeCompletion.Observe(JsonEvent(CompletionJson("backend-request")), out _);
Check(!doneBeforeCompletion.IsSuccessful, "done -> completion must fail.");

var wrongTerminalId = new RagTerminalStateMachine("backend-request");
wrongTerminalId.Observe(JsonEvent(CompletionJson("attacker-request")), out _);
wrongTerminalId.Observe(JsonEvent("{\"type\":\"done\",\"request_id\":\"backend-request\"}"), out _);
Check(!wrongTerminalId.IsSuccessful, "A terminal request id must exactly match the backend-issued id.");

var contentAfterCompletion = new RagTerminalStateMachine("backend-request");
contentAfterCompletion.Observe(JsonEvent(CompletionJson("backend-request")), out _);
contentAfterCompletion.Observe(JsonEvent("{\"type\":\"text\",\"content\":\"late\"}"), out _);
contentAfterCompletion.Observe(JsonEvent("{\"type\":\"done\",\"request_id\":\"backend-request\"}"), out _);
Check(!contentAfterCompletion.IsSuccessful, "Text or metadata after completion must fail.");

var contentAfterDone = new RagTerminalStateMachine("backend-request");
contentAfterDone.Observe(JsonEvent(CompletionJson("backend-request")), out _);
contentAfterDone.Observe(JsonEvent("{\"type\":\"done\",\"request_id\":\"backend-request\"}"), out _);
contentAfterDone.Observe(JsonEvent("{\"type\":\"metadata\",\"sources\":[]}"), out _);
Check(!contentAfterDone.IsSuccessful, "Any data event after done must invalidate success.");

var malformedTerminal = new RagTerminalStateMachine("backend-request");
malformedTerminal.ObserveMalformedData();
Check(!malformedTerminal.IsSuccessful && malformedTerminal.Stage == RagTerminalStage.Invalid,
    "Malformed terminal JSON must be fatal.");

var malformedCompletion = new RagTerminalStateMachine("backend-request");
malformedCompletion.Observe(
    JsonEvent("{\"type\":\"completion\",\"request_id\":\"backend-request\",\"sources\":[]}"),
    out _);
Check(!malformedCompletion.IsSuccessful,
    "A completion missing the required terminal metadata must fail.");

var forgedSourceLineage = new RagTerminalStateMachine("backend-request");
forgedSourceLineage.Observe(
    JsonEvent("{\"type\":\"completion\",\"request_id\":\"backend-request\",\"sources\":[{\"source_ref\":\"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}],\"suggested_questions\":[\"후속 질문\"],\"suggested_question_details\":[{\"question\":\"후속 질문\",\"source_refs\":[\"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"]}],\"resolved_intents\":[\"notices\"],\"grounded\":true,\"grounding_score\":0.9,\"fallback_reason\":null}"),
    out _);
Check(!forgedSourceLineage.IsSuccessful && forgedSourceLineage.Stage == RagTerminalStage.Invalid,
    "A follow-up reference that is absent from transported sources must fail.");

var incompleteEof = new RagTerminalStateMachine("backend-request");
incompleteEof.Observe(JsonEvent(CompletionJson("backend-request")), out _);
incompleteEof.ObserveEndOfStream();
Check(!incompleteEof.IsSuccessful, "EOF after completion without done must fail.");

var cancelledAfterTerminal = new RagTerminalStateMachine("backend-request");
cancelledAfterTerminal.Observe(JsonEvent(CompletionJson("backend-request")), out _);
cancelledAfterTerminal.Observe(JsonEvent("{\"type\":\"done\",\"request_id\":\"backend-request\"}"), out _);
cancelledAfterTerminal.ObserveCancellation();
Check(!cancelledAfterTerminal.IsSuccessful,
    "Cancellation must be fatal even if terminal events arrived first.");

var failedAfterTerminal = new RagTerminalStateMachine("backend-request");
failedAfterTerminal.Observe(JsonEvent(CompletionJson("backend-request")), out _);
failedAfterTerminal.Observe(JsonEvent("{\"type\":\"done\",\"request_id\":\"backend-request\"}"), out _);
failedAfterTerminal.ObserveTransportFailure();
Check(!failedAfterTerminal.IsSuccessful,
    "A transport exception must be fatal even if terminal events arrived first.");

const string fallbackBackendRequestId = "fallback-backend-request";
IReadOnlyList<string> fallbackPayloads = RagStreamContract.CreateGracefulFallbackPayloads(
    fallbackBackendRequestId,
    "rag_stream_http_error",
    "fallback answer");
JsonElement[] fallbackEvents = fallbackPayloads.Select(JsonEvent).ToArray();
string[] expectedFallbackTypes = ["metadata", "text", "completion", "done"];
Check(
    fallbackEvents.Length == expectedFallbackTypes.Length
    && fallbackEvents.Select(payload => payload.GetProperty("type").GetString())
        .SequenceEqual(expectedFallbackTypes),
    "The graceful fallback must emit exactly metadata -> text -> completion -> done.");
Check(
    fallbackEvents.All(payload =>
        payload.TryGetProperty("request_id", out JsonElement requestId)
        && requestId.ValueKind == JsonValueKind.String
        && requestId.GetString() == fallbackBackendRequestId),
    "Every graceful fallback payload must carry the backend request id.");

var fallbackTerminal = new RagTerminalStateMachine(fallbackBackendRequestId);
foreach (JsonElement payload in fallbackEvents)
{
    fallbackTerminal.Observe(payload, out _);
}
fallbackTerminal.ObserveEndOfStream();
Check(fallbackTerminal.IsSuccessful,
    "The graceful fallback sequence must satisfy the strict completion -> done terminal contract.");

await RagRelayTimeoutTests.RunAsync(Check);

string keyDirectory = Path.Combine(Path.GetTempPath(), $"dongttok-dp-{Guid.NewGuid():N}");
string otherKeyDirectory = Path.Combine(Path.GetTempPath(), $"dongttok-dp-other-{Guid.NewGuid():N}");
Directory.CreateDirectory(keyDirectory);
Directory.CreateDirectory(otherKeyDirectory);
try
{
    IDataProtectionProvider firstProvider = DataProtectionProvider.Create(new DirectoryInfo(keyDirectory), builder => builder.SetApplicationName("Dongttok"));
    string protectedGuest = GuestIdentity.Issue(firstProvider);
    var headerGuestContext = new DefaultHttpContext();
    headerGuestContext.Request.Headers[GuestIdentity.HeaderName] = protectedGuest;
    Check(GuestIdentity.TryValidate(
              headerGuestContext.Request,
              firstProvider,
              out string headerGuestId,
              out string validatedHeaderToken)
          && headerGuestId.Length == 32
          && validatedHeaderToken == protectedGuest,
        "A server-issued protected guest header token should validate and be reusable.");

    var firstGuestContext = new DefaultHttpContext();
    firstGuestContext.Request.Headers.Cookie = $"{GuestIdentity.CookieName}={Uri.EscapeDataString(protectedGuest)}";
    Check(GuestIdentity.TryValidate(
              firstGuestContext.Request,
              firstProvider,
              out string firstGuestId,
              out string validatedCookieToken)
          && firstGuestId == headerGuestId
          && validatedCookieToken == protectedGuest,
        "The guest cookie fallback should validate and expose the same reusable token.");

    IDataProtectionProvider restartedProvider = DataProtectionProvider.Create(new DirectoryInfo(keyDirectory), builder => builder.SetApplicationName("Dongttok"));
    Check(GuestIdentity.TryValidate(firstGuestContext.Request, restartedProvider, out string restartedGuestId)
          && restartedGuestId == firstGuestId,
        "The same persisted key ring must validate a cookie after provider restart.");

    var forgedContext = new DefaultHttpContext();
    forgedContext.Request.Headers.Cookie = $"{GuestIdentity.CookieName}={Uri.EscapeDataString(protectedGuest + "forged")}";
    Check(!GuestIdentity.TryValidate(forgedContext.Request, restartedProvider, out _),
        "A forged guest cookie must be rejected.");
    var blankContext = new DefaultHttpContext();
    blankContext.Request.Headers.Cookie = $"{GuestIdentity.CookieName}=";
    Check(!GuestIdentity.TryValidate(blankContext.Request, restartedProvider, out _),
        "A blank guest cookie must be rejected.");

    IDataProtectionProvider unrelatedProvider = DataProtectionProvider.Create(new DirectoryInfo(otherKeyDirectory), builder => builder.SetApplicationName("Dongttok"));
    Check(!GuestIdentity.TryValidate(headerGuestContext.Request, unrelatedProvider, out _),
        "A header token must not validate under an unrelated key ring.");
    Check(!GuestIdentity.TryValidate(firstGuestContext.Request, unrelatedProvider, out _),
        "A cookie must not validate under an unrelated key ring.");
}
finally
{
    Directory.Delete(keyDirectory, recursive: true);
    Directory.Delete(otherKeyDirectory, recursive: true);
}

var webJsonOptions = new JsonSerializerOptions(JsonSerializerDefaults.Web);
using JsonDocument guestChatJson = JsonDocument.Parse(JsonSerializer.Serialize(
    new ActiveChatDto { Id = Guid.NewGuid(), Title = "guest", GuestToken = "protected-token" },
    webJsonOptions));
Check(guestChatJson.RootElement.GetProperty("guestToken").GetString() == "protected-token",
    "Guest chat responses must include the protected guest token.");
using JsonDocument authenticatedChatJson = JsonDocument.Parse(JsonSerializer.Serialize(
    new ActiveChatDto { Id = Guid.NewGuid(), Title = "authenticated" },
    webJsonOptions));
Check(!authenticatedChatJson.RootElement.TryGetProperty("guestToken", out _),
    "Authenticated chat responses must omit the guest token field.");

string[] forbiddenNames = ["Question", "Answer", "Content", "Name", "StudentNumber", "Email", "IpAddress"];
var persistedPropertyNames = typeof(ProductEvent).GetProperties().Select(property => property.Name).ToHashSet(StringComparer.OrdinalIgnoreCase);
var clientPropertyNames = typeof(ProductEventRequest).GetProperties().Select(property => property.Name).ToHashSet(StringComparer.OrdinalIgnoreCase);
foreach (string forbiddenName in forbiddenNames)
{
    Check(!persistedPropertyNames.Contains(forbiddenName), $"ProductEvent must not expose {forbiddenName}.");
    Check(!clientPropertyNames.Contains(forbiddenName), $"ProductEventRequest must not accept {forbiddenName}.");
}

var dbOptions = new DbContextOptionsBuilder<ServerDbContext>()
    .UseNpgsql("Host=localhost;Database=model_only;Username=model_only;Password=model_only")
    .Options;
await using var db = new ServerDbContext(dbOptions);
var eventEntity = db.Model.FindEntityType(typeof(ProductEvent));
var messageEntity = db.Model.FindEntityType(typeof(ChatMessage));
Check(eventEntity?.GetIndexes().Any(index =>
        index.IsUnique && index.Properties.Select(property => property.Name).SequenceEqual([nameof(ProductEvent.IdempotencyKey)])) == true,
    "Product events must have a unique idempotency key.");
Check(messageEntity?.GetIndexes().Any(index =>
        index.IsUnique && index.Properties.Select(property => property.Name).SequenceEqual([nameof(ChatMessage.RequestId)])) == true,
    "Completed answer request ids must be unique.");
Check(messageEntity?.FindProperty(nameof(ChatMessage.VerificationStatus))?.IsNullable == true
      && messageEntity.FindProperty(nameof(ChatMessage.RelevanceScore))?.IsNullable == true,
    "Chat answer verification status and relevance score must map as nullable columns.");
Check(db.Database.GetMigrations().Contains("20260928000000_AddChatVerificationStatusAndRelevanceScore"),
    "The additive chat verification migration must be discoverable by EF Core.");
var verificationOperations = new RenuxServer.Migrations.AddChatVerificationStatusAndRelevanceScore()
    .UpOperations;
var verificationColumns = verificationOperations.OfType<AddColumnOperation>().ToArray();
Check(verificationOperations.Count == 2 && verificationColumns.Length == 2
      && verificationColumns.All(column => column.Table == "chat_messages" && column.IsNullable)
      && verificationColumns.Any(column => column.Name == "verification_status" && column.ColumnType == "text")
      && verificationColumns.Any(column => column.Name == "relevance_score" && column.ColumnType == "double precision"),
    "Chat verification migration must add exactly two nullable answer columns.");

var answerWithVerification = new ChatMessage
{
    Id = Guid.NewGuid(),
    ChatId = Guid.NewGuid(),
    IsAsk = false,
    Content = "answer",
    VerificationStatus = "unavailable",
    RelevanceScore = 0.4,
};
var toChatDto = typeof(ChatRequestApis).GetMethod("ToDto",
    System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
var historyDto = toChatDto?.Invoke(null, [answerWithVerification]) as ChatMessageDto;
var historyJson = historyDto is null
    ? default
    : JsonSerializer.SerializeToElement(historyDto, webJsonOptions);
Check(historyDto?.VerificationStatus == "unavailable"
      && historyDto.RelevanceScore == 0.4
      && historyJson.GetProperty("verificationStatus").GetString() == "unavailable"
      && historyJson.GetProperty("relevanceScore").GetDouble() == 0.4,
    "Chat history DTO must return persisted verification status and relevance score.");
answerWithVerification.VerificationStatus = null;
answerWithVerification.RelevanceScore = null;
var legacyHistoryDto = toChatDto?.Invoke(null, [answerWithVerification]) as ChatMessageDto;
Check(legacyHistoryDto?.VerificationStatus is null && legacyHistoryDto?.RelevanceScore is null,
    "Older answer rows must retain nullable verification fields in chat history.");

DateTime cohortFrom = new(2026, 7, 1, 0, 0, 0, DateTimeKind.Utc);
DateTime cohortTo = new(2026, 7, 20, 0, 0, 0, DateTimeKind.Utc);
var kpiEvents = new List<ProductEvent>
{
    new() { EventType = ProductEventTypes.AnswerCompleted, IdempotencyKey = "1", AnswerKey = "a", SubjectKey = "s1", IsFallback = false, Grounded = true, OccurredTime = cohortFrom },
    new() { EventType = ProductEventTypes.FeedbackSubmitted, IdempotencyKey = "2", AnswerKey = "a", SubjectKey = "s1", Rating = 1, OccurredTime = cohortFrom.AddHours(1) },
    new() { EventType = ProductEventTypes.AnswerCompleted, IdempotencyKey = "3", AnswerKey = "b", SubjectKey = "s2", IsFallback = false, Grounded = true, OccurredTime = cohortFrom },
    new() { EventType = ProductEventTypes.FeedbackSubmitted, IdempotencyKey = "4", AnswerKey = "b", SubjectKey = "s2", Rating = -1, OccurredTime = cohortFrom.AddHours(1) },
    new() { EventType = ProductEventTypes.AnswerCompleted, IdempotencyKey = "5", AnswerKey = "c", SubjectKey = "s1", IsFallback = false, Grounded = true, OccurredTime = cohortFrom.AddDays(3) },
    new() { EventType = ProductEventTypes.AnswerCompleted, IdempotencyKey = "6", AnswerKey = "excluded", SubjectKey = "test", IsExcluded = true, IsFallback = false, Grounded = true, OccurredTime = cohortFrom },
    new() { EventType = ProductEventTypes.AnswerCompleted, IdempotencyKey = "7", AnswerKey = "fallback", SubjectKey = "s3", IsFallback = true, Grounded = true, OccurredTime = cohortFrom },
    new() { EventType = ProductEventTypes.AnswerCompleted, IdempotencyKey = "8", AnswerKey = "ungrounded", SubjectKey = "s4", IsFallback = false, Grounded = false, OccurredTime = cohortFrom },
    new() { EventType = ProductEventTypes.FeedbackSubmitted, IdempotencyKey = "9", AnswerKey = "a", SubjectKey = "other-subject", Rating = 1, OccurredTime = cohortFrom.AddHours(2) },
    new() { EventType = ProductEventTypes.AnswerCompleted, IdempotencyKey = "10", AnswerKey = "anonymous", SubjectKey = null, IsFallback = false, Grounded = true, OccurredTime = cohortFrom },
    new() { EventType = ProductEventTypes.FeedbackSubmitted, IdempotencyKey = "11", AnswerKey = "fallback", SubjectKey = "s3", Rating = 1, OccurredTime = cohortFrom.AddHours(2) },
    new() { EventType = ProductEventTypes.FeedbackSubmitted, IdempotencyKey = "12", AnswerKey = "ungrounded", SubjectKey = "s4", Rating = 1, OccurredTime = cohortFrom.AddHours(2) },
};
ProductKpiRatio helpful = ProductKpiMath.HelpfulAnswerRate(kpiEvents);
ProductKpiRatio reuse = ProductKpiMath.SevenDayValidReuseRate(kpiEvents, cohortFrom, cohortTo);
Check(helpful is { Numerator: 1, Denominator: 3 } && Math.Abs(helpful.Rate!.Value - (1.0 / 3.0)) < 0.0001,
    "Helpful-answer KPI must deduplicate answers, include unanswered completions in the denominator and exclude test traffic.");
Check(reuse is { Numerator: 1, Denominator: 2 } && Math.Abs(reuse.Rate!.Value - 0.5) < 0.0001,
    "Seven-day reuse KPI must require a different-day return and exclude test traffic.");

var seoulBoundaryEvents = new List<ProductEvent>
{
    new() { EventType = ProductEventTypes.AnswerCompleted, IdempotencyKey = "tz1a", AnswerKey = "tz1a", SubjectKey = "tz1", IsFallback = false, Grounded = true, OccurredTime = new DateTime(2026, 7, 1, 14, 30, 0, DateTimeKind.Utc) },
    new() { EventType = ProductEventTypes.AnswerCompleted, IdempotencyKey = "tz1b", AnswerKey = "tz1b", SubjectKey = "tz1", IsFallback = false, Grounded = true, OccurredTime = new DateTime(2026, 7, 1, 15, 30, 0, DateTimeKind.Utc) },
    new() { EventType = ProductEventTypes.AnswerCompleted, IdempotencyKey = "tz2a", AnswerKey = "tz2a", SubjectKey = "tz2", IsFallback = false, Grounded = true, OccurredTime = new DateTime(2026, 7, 1, 15, 30, 0, DateTimeKind.Utc) },
    new() { EventType = ProductEventTypes.AnswerCompleted, IdempotencyKey = "tz2b", AnswerKey = "tz2b", SubjectKey = "tz2", IsFallback = false, Grounded = true, OccurredTime = new DateTime(2026, 7, 2, 14, 0, 0, DateTimeKind.Utc) },
};
ProductKpiRatio seoulReuse = ProductKpiMath.SevenDayValidReuseRate(
    seoulBoundaryEvents,
    new DateTime(2026, 7, 1, 0, 0, 0, DateTimeKind.Utc),
    new DateTime(2026, 7, 20, 0, 0, 0, DateTimeKind.Utc));
Check(seoulReuse is { Numerator: 1, Denominator: 2 },
    "A return must use Asia/Seoul day boundaries around UTC 15:00.");

string? integrationConnection = Environment.GetEnvironmentVariable("DONGTTOK_TEST_CONNECTION");
if (!string.IsNullOrWhiteSpace(integrationConnection))
{
    string integrationRequestId = $"contract-{Guid.NewGuid():N}";
    string integrationSubject = $"contract-subject-{Guid.NewGuid():N}";
    string integrationAnswerKey = ProductTelemetry.BuildPseudonymousKey(
        firstConfig,
        "answer",
        integrationRequestId);
    var integrationOptions = new DbContextOptionsBuilder<ServerDbContext>()
        .UseNpgsql(integrationConnection)
        .Options;

    try
    {
        await using var firstWriter = new ServerDbContext(integrationOptions);
        await firstWriter.Database.MigrateAsync();
        await using var secondWriter = new ServerDbContext(integrationOptions);
        var integrationContext = new ProductEventContext(
            integrationSubject,
            SessionKey: null,
            IsExcluded: true,
            ExclusionReason: "contract_test");
        var integrationData = new ProductEventData(
            ProductEventTypes.AnswerCompleted,
            integrationRequestId,
            SuggestionCount: 3,
            IsFallback: false,
            Grounded: true,
            SourceCount: 5);

        bool[] writes = await Task.WhenAll(
            ProductTelemetry.RecordAsync(firstWriter, firstConfig, integrationContext, integrationData),
            ProductTelemetry.RecordAsync(secondWriter, firstConfig, integrationContext, integrationData));

        await using var verifier = new ServerDbContext(integrationOptions);
        int persistedCount = await verifier.ProductEvents.CountAsync(productEvent =>
            productEvent.EventType == ProductEventTypes.AnswerCompleted
            && productEvent.AnswerKey == integrationAnswerKey);
        Check(writes.Count(inserted => inserted) == 1 && persistedCount == 1,
            "PostgreSQL must enforce one idempotent event under concurrent writes.");

        ProductEvent? ownedCompletion = await ProductTelemetry.FindAnswerCompletionEventAsync(
            verifier,
            firstConfig,
            integrationContext,
            integrationRequestId);
        ProductEvent? foreignCompletion = await ProductTelemetry.FindAnswerCompletionEventAsync(
            verifier,
            firstConfig,
            integrationContext with { SubjectKey = "different-contract-subject" },
            integrationRequestId);
        Check(ownedCompletion?.SuggestionCount == 3 && foreignCompletion is null,
            "Completion lookup must bind the answer to the exact pseudonymous subject.");

        await RagRelayTimeoutTests.RunPersistenceAsync(integrationOptions, Check);
    }
    catch (Exception exception)
    {
        failures.Add($"PostgreSQL integration contract failed: {exception.GetType().Name}: {exception.Message}");
    }
    finally
    {
        await using var cleanup = new ServerDbContext(integrationOptions);
        await cleanup.ProductEvents
            .Where(productEvent => productEvent.AnswerKey == integrationAnswerKey)
            .ExecuteDeleteAsync();
    }
}

if (failures.Count > 0)
{
    foreach (string failure in failures) Console.Error.WriteLine($"FAIL: {failure}");
    return 1;
}

Console.WriteLine("Backend contract tests passed.");
return 0;
