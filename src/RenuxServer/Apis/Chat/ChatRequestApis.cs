using Microsoft.EntityFrameworkCore;
using Microsoft.AspNetCore.DataProtection;
using System.Net.Http.Json;
using System.Security.Claims;
using System.Text.Json;
using System.Text.Json.Serialization;

using RenuxServer.DbContexts;
using RenuxServer.Dtos.ChatDtos;
using RenuxServer.Models;
using RenuxServer.Dtos.EtcDtos;
using RenuxServer.Services;

namespace RenuxServer.Apis.Chat;

public record StartChat(OrganizationDto Org, string Title);
public record LoadChat(Guid ChatId, DateTime LastTime);
public record RenameChat(string? Title);

/// <summary>게스트 대화 이관 요청. 게스트 기록은 서버에 없어 클라이언트가 본문으로 보낸다.</summary>
public record ClaimGuestChats(List<ClaimGuestChat>? Chats);
public record ClaimGuestChat(Guid OrganizationId, string? Title, List<ClaimGuestMessage>? Messages);
public record ClaimGuestMessage(bool IsAsk, string? Content, DateTime? CreatedTime);
public record RagSource(
    string? Source,
    [property: JsonPropertyName("chunk_id")] string? ChunkId,
    string? Title,
    string? Url,
    [property: JsonPropertyName("published_at")] string? PublishedAt,
    string? Snippet,
    [property: JsonPropertyName("vector_score")] double? VectorScore,
    [property: JsonPropertyName("sparse_score")] double? SparseScore,
    [property: JsonPropertyName("hybrid_score")] double? HybridScore,
    [property: JsonPropertyName("recency_score")] double? RecencyScore,
    [property: JsonPropertyName("final_score")] double? FinalScore
);
public record FeedbackDto(string RequestId, int Rating, string? Reason, string? Comment);
public record FollowupRequestDto(string RequestId);
public record RagFollowupResponse(
    [property: JsonPropertyName("request_id")] string? RequestId,
    List<string>? Questions);

static public class ChatRequestApis
{
    private const string DefaultRagFailureMessage = "죄송합니다. 지금은 학교 정보 검색 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    static private CookieOptions BuildGuestCookieOptions(IConfiguration configuration)
    {
        bool secure = configuration.GetValue<bool?>("GuestCookie:Secure")
            ?? configuration.GetValue<bool?>("GUEST_COOKIE_SECURE")
            ?? true;

        string sameSiteRaw =
            configuration["GuestCookie:SameSite"]
            ?? configuration["GUEST_COOKIE_SAMESITE"]
            ?? "None";

        SameSiteMode sameSite = sameSiteRaw.ToLowerInvariant() switch
        {
            "strict" => SameSiteMode.Strict,
            "lax" => SameSiteMode.Lax,
            "none" => SameSiteMode.None,
            _ => SameSiteMode.None,
        };

        return new CookieOptions
        {
            HttpOnly = true,
            Secure = secure,
            SameSite = sameSite,
            IsEssential = true,
            Path = "/"
        };
    }

    // 채팅 메시지 최대 길이(자). 초과 시 RAG로 전달하지 않아 토큰 비용·OOM을 방어한다.
    private const int MaxChatContentLength = 2000;

    private const int MaxChatTitleLength = 80;

    // 게스트 대화 이관 상한. 클라이언트가 보낸 기록을 그대로 적재하므로 남용을 막는다.
    private const int MaxClaimChats = 20;
    private const int MaxClaimMessagesPerChat = 100;

    /// <summary>이관된 메시지 시각을 UTC로 정규화한다. 미래 시각은 지금으로 눌러 순서를 지킨다.</summary>
    static private DateTime NormalizeClaimedTime(DateTime? value, DateTime now)
    {
        if (value is null || value == default(DateTime))
        {
            return now;
        }

        DateTime utc = value.Value.Kind switch
        {
            DateTimeKind.Utc => value.Value,
            DateTimeKind.Local => value.Value.ToUniversalTime(),
            _ => DateTime.SpecifyKind(value.Value, DateTimeKind.Utc),
        };

        return utc > now ? now : utc;
    }

    static public void AddChatApis(this WebApplication application)
    {
        // IP 단위 레이트리밋(LLM 비용·남용 방어) 적용.
        var app = application.MapGroup("/chat").RequireRateLimiting("chat");

        app.MapGet("/active", async (ServerDbContext db, HttpContext context) =>
        {
            // For guests, return an empty list as their chats are not persisted
            if (!TryGetUserId(context, out Guid id))
            {
                return Results.Ok(new List<ActiveChatDto>());
            }

            var chatEntities = await db.Chats
                .Include(ch => ch.Organization)
                .ThenInclude(org => org!.Major)
                .Where(ch => Equals(ch.UserId, id))
                .ToListAsync();

            // 마지막 활동 시각·미리보기는 ActiveChat.UpdatedTime이 아니라 최근 메시지에서 구한다.
            // UpdatedTime은 생성 시에만 기록되어 대화를 이어가도 갱신되지 않기 때문이다.
            var chatIds = chatEntities.Select(ch => ch.Id).ToList();
            var lastMessages = chatIds.Count == 0
                ? []
                : await db.ChatMessages
                    .Where(m => chatIds.Contains(m.ChatId))
                    .GroupBy(m => m.ChatId)
                    .Select(g => g
                        .OrderByDescending(m => m.CreatedTime)
                        .Select(m => new { m.ChatId, m.Content, m.CreatedTime })
                        .First())
                    .ToListAsync();

            var lastMessageByChat = lastMessages.ToDictionary(m => m.ChatId);

            List<ActiveChatDto> chats = chatEntities
                .Select(chat =>
                {
                    var dto = ToActiveChatDto(chat);
                    if (lastMessageByChat.TryGetValue(chat.Id, out var last))
                    {
                        dto.UpdatedTime = last.CreatedTime;
                        dto.LastMessage = BuildMessagePreview(last.Content);
                    }
                    else
                    {
                        dto.UpdatedTime = chat.CreatedTime;
                    }
                    return dto;
                })
                .OrderByDescending(dto => dto.UpdatedTime)
                .ToList();

            return Results.Ok(chats);
        });

        app.MapPost("/start", async (ServerDbContext db, HttpContext context, StartChat stch, IConfiguration configuration, IDataProtectionProvider dataProtectionProvider) =>
        {
            DateTime time = DateTime.UtcNow;
            Guid id = Guid.NewGuid();

            // Authenticated user check
            bool isAuthenticated = TryGetUserId(context, out Guid authenticatedUserId);

            if (!isAuthenticated)
            {
                // Guest flow: Do NOT save to DB
                // Create a temporary ID for the frontend session
                Guid guestChatId = Guid.NewGuid();

                string guestToken;
                if (GuestIdentity.TryValidate(
                    context.Request,
                    dataProtectionProvider,
                    out _,
                    out string validatedGuestToken))
                {
                    guestToken = validatedGuestToken;
                }
                else
                {
                    guestToken = GuestIdentity.Issue(dataProtectionProvider);
                    CookieOptions opt = BuildGuestCookieOptions(configuration);
                    context.Response.Cookies.Append(GuestIdentity.CookieName, guestToken, opt);
                }

                ActiveChatDto guestChatDto = new()
                {
                    Id = guestChatId,
                    Organization = stch.Org,
                    Title = stch.Title,
                    GuestToken = guestToken
                };
                return Results.Ok(guestChatDto);
            }

            // Authenticated User Flow
            while (await db.Chats.AnyAsync(c => c.Id == id))
                id = Guid.NewGuid();

            Guid userId = authenticatedUserId;

            // 프론트가 보낸 OrganizationId가 실제 존재하는지 검증 (FK 위반/고아 데이터 방지)
            if (!await db.Organizations.AnyAsync(o => o.Id == stch.Org.Id))
            {
                return Results.BadRequest("유효하지 않은 조직입니다.");
            }

            ActiveChat chat = new()
            {
                Id = id,
                UserId = userId,
                OrganizationId = stch.Org.Id,
                Title = stch.Title,
                CreatedTime = time,
                UpdatedTime = time
            };

            ChatMessage startChat = new()
            {
                ChatId = chat.Id,
                IsAsk = false,
                                            Content = "안녕하세요. 동똑이입니다. 무엇을 도와드릴까요?",                CreatedTime = time
            };

            await db.Chats.AddAsync(chat);
            await db.ChatMessages.AddAsync(startChat);
            await db.SaveChangesAsync();

            ActiveChatDto chatDto = new()
            {
                Id = chat.Id,
                Organization = stch.Org,
                Title = chat.Title
            };

            return Results.Ok(chatDto);
        });

        app.MapPost("/stream", async (ServerDbContext db, HttpContext context, ChatMessageDto askDto, ILogger<Program> logger, IConfiguration configuration, IHttpClientFactory httpClientFactory, IDataProtectionProvider dataProtectionProvider) =>
        {
            if (string.IsNullOrWhiteSpace(askDto.Content))
            {
                context.Response.StatusCode = StatusCodes.Status400BadRequest;
                await context.Response.WriteAsJsonAsync(new { message = "메시지를 입력해주세요." });
                return;
            }
            var content = askDto.Content;
            if ((askDto.Content?.Length ?? 0) > MaxChatContentLength)
            {
                context.Response.StatusCode = StatusCodes.Status400BadRequest;
                await context.Response.WriteAsJsonAsync(new { message = $"메시지가 너무 깁니다(최대 {MaxChatContentLength}자)." });
                return;
            }

            bool isAuthenticated = TryGetUserId(context, out Guid authenticatedStreamUserId);
            string? validatedGuestSubjectId = null;
            if (!isAuthenticated && !GuestIdentity.TryValidate(context.Request, dataProtectionProvider, out validatedGuestSubjectId))
            {
                context.Response.StatusCode = StatusCodes.Status401Unauthorized;
                await context.Response.WriteAsJsonAsync(new { message = "유효한 게스트 세션이 필요합니다." });
                return;
            }
            string sessionId = askDto.ChatId.ToString();
            string question = content;
            string? major = isAuthenticated ? ResolveMajor(context) : null;

            // Authenticated: verify chat ownership (IDOR 방지), then save User's question first so it is never lost.
            ChatMessage? ask = null;
            if (isAuthenticated)
            {
                if (askDto.Id == Guid.Empty)
                {
                    context.Response.StatusCode = StatusCodes.Status400BadRequest;
                    await context.Response.WriteAsJsonAsync(new { message = "질문 ID가 필요합니다." });
                    return;
                }
                Guid streamUserId = authenticatedStreamUserId;
                if (!await UserOwnsChatAsync(db, askDto.ChatId, streamUserId))
                {
                    context.Response.StatusCode = StatusCodes.Status403Forbidden;
                    return;
                }

                ask = await db.ChatMessages.FindAsync([askDto.Id], context.RequestAborted);
                if (ask is null)
                {
                    ask = ToQuestionEntity(askDto, content);
                    await db.ChatMessages.AddAsync(ask);
                    await db.SaveChangesAsync(context.RequestAborted);
                }
                else
                {
                    if (ask.ChatId != askDto.ChatId)
                    {
                        context.Response.StatusCode = StatusCodes.Status403Forbidden;
                        return;
                    }
                    if (!ask.IsAsk || !string.Equals(ask.Content, content, StringComparison.Ordinal))
                    {
                        context.Response.StatusCode = StatusCodes.Status400BadRequest;
                        await context.Response.WriteAsJsonAsync(new { message = "저장된 질문과 요청 내용이 일치하지 않습니다." });
                        return;
                    }

                    // Never allow a client to mutate a persisted question during regeneration.
                    question = ask.Content;
                }
            }

            context.Response.ContentType = "text/event-stream";
            context.Response.Headers.CacheControl = "no-cache";
            context.Response.Headers.Connection = "keep-alive";
            // nginx 등 리버스 프록시가 SSE를 버퍼링하지 않도록 지시 (nginx.conf의 proxy_buffering off와 이중 안전망)
            context.Response.Headers["X-Accel-Buffering"] = "no";

            var fullAnswer = new System.Text.StringBuilder();
            List<ChatSourceDto> sources = [];
            bool fallbackTriggered = false;
            string? fallbackReason = null;
            bool ragResponseSucceeded = false;
            bool streamReportedError = false;
            bool streamCancelled = false;
            // This answer identifier is backend-owned and independent from the
            // request trace id so infrastructure logs cannot disclose it.
            string backendRequestId = Guid.NewGuid().ToString("N");
            var terminalState = new RagTerminalStateMachine(backendRequestId);
            List<string> suggestedQuestions = [];
            bool? grounded = null;
            double? groundingScore = null;

            try
            {
                var ragUrl = configuration["RagServiceUrl"] ?? configuration["RAG_SERVICE_URL"] ?? "http://rag-service:8000";
                var client = httpClientFactory.CreateClient();
                client.Timeout = TimeSpan.FromMinutes(5); // Streaming needs longer timeout

                using var request = new HttpRequestMessage(HttpMethod.Post, $"{ragUrl}/ask/stream")
                {
                    Content = JsonContent.Create(new { question, sessionId, major })
                };
                request.Headers.TryAddWithoutValidation("X-Request-ID", backendRequestId);

                using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, context.RequestAborted);

                if (!response.IsSuccessStatusCode)
                {
                    fallbackTriggered = true;
                    fallbackReason = "rag_stream_http_error";
                    logger.LogWarning(
                        "RAG stream request failed. StatusCode={StatusCode}",
                        (int)response.StatusCode);
                }
                else
                {
                    ragResponseSucceeded = true;
                    using var reader = new StreamReader(await response.Content.ReadAsStreamAsync(context.RequestAborted));

                    while (await reader.ReadLineAsync(context.RequestAborted) is { } line)
                    {
                        if (line.Length == 0 || !line.StartsWith("data: "))
                        {
                            // Preserve blank lines and non-data SSE fields. Data
                            // fields are forwarded only after contract validation.
                            await context.Response.WriteAsync($"{line}\n", context.RequestAborted);
                            await context.Response.Body.FlushAsync(context.RequestAborted);
                            continue;
                        }

                        try
                        {
                            var json = line.Substring(6);
                            var chunk = JsonSerializer.Deserialize<JsonElement>(json);
                            if (!terminalState.Observe(chunk, out string? type))
                            {
                                streamReportedError = true;
                                continue;
                            }

                            await context.Response.WriteAsync($"{line}\n", context.RequestAborted);
                            await context.Response.Body.FlushAsync(context.RequestAborted);
                            if (type is not null)
                            {
                                if (type == "metadata")
                                {
                                    if (chunk.TryGetProperty("sources", out var sourcesProp))
                                    {
                                        var ragSources = JsonSerializer.Deserialize<List<RagSource>>(sourcesProp.GetRawText());
                                        sources = MapSources(ragSources);
                                    }
                                    if (chunk.TryGetProperty("fallback_triggered", out var fbProp))
                                    {
                                        fallbackTriggered = fbProp.GetBoolean();
                                    }
                                    if (chunk.TryGetProperty("fallback_reason", out var fbrProp))
                                    {
                                        fallbackReason = fbrProp.GetString();
                                    }
                                }
                                else if (type == "suggestions")
                                {
                                    suggestedQuestions = ReadSuggestedQuestions(chunk);
                                }
                                else if (type == "grounding")
                                {
                                    if (chunk.TryGetProperty("grounded", out var groundedProp)
                                        && groundedProp.ValueKind is JsonValueKind.True or JsonValueKind.False)
                                    {
                                        grounded = groundedProp.GetBoolean();
                                    }
                                    groundingScore = ReadGroundingScore(chunk, "score") ?? groundingScore;
                                }
                                else if (type == "completion")
                                {
                                    suggestedQuestions = ReadSuggestedQuestions(chunk, "suggested_questions", suggestedQuestions);
                                    grounded = ReadNullableBoolean(chunk, "grounded") ?? grounded;
                                    groundingScore = ReadGroundingScore(chunk, "grounding_score") ?? groundingScore;
                                    if (chunk.TryGetProperty("sources", out var completionSourcesProp))
                                    {
                                        var completionSources = JsonSerializer.Deserialize<List<RagSource>>(completionSourcesProp.GetRawText());
                                        sources = MapSources(completionSources);
                                    }
                                    if (chunk.TryGetProperty("fallback_reason", out var completionFallbackReasonProp))
                                    {
                                        fallbackReason = completionFallbackReasonProp.ValueKind == JsonValueKind.String
                                            ? completionFallbackReasonProp.GetString()
                                            : null;
                                        fallbackTriggered = !string.IsNullOrWhiteSpace(fallbackReason);
                                    }
                                }
                                else if (type == "text")
                                {
                                    if (chunk.TryGetProperty("content", out var contentProp))
                                    {
                                        fullAnswer.Append(contentProp.GetString());
                                    }
                                }
                                else if (type == "error")
                                {
                                    streamReportedError = true;
                                    fallbackTriggered = true;
                                    fallbackReason = "rag_stream_error";
                                }
                                else if (type == "done")
                                {
                                    // Terminal shape/order/request id were already
                                    // validated by RagTerminalStateMachine.
                                }
                            }
                        }
                        catch (Exception ex)
                        {
                            terminalState.ObserveMalformedData();
                            streamReportedError = true;
                            logger.LogWarning(ex, "Failed to parse an upstream SSE data event.");
                        }
                    }

                    terminalState.ObserveEndOfStream();
                }
            }
            catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested)
            {
                // Client disconnected mid-stream: no answer version or completion
                // event may be committed, even if terminal data was seen first.
                terminalState.ObserveCancellation();
                streamCancelled = true;
                logger.LogInformation("Chat stream cancelled by client.");
            }
            catch (Exception ex)
            {
                terminalState.ObserveTransportFailure();
                streamReportedError = true;
                fallbackTriggered = true;
                fallbackReason = "rag_stream_transport_error";
                logger.LogError(ex, "RAG stream error.");
            }

            bool generationSucceeded = ragResponseSucceeded &&
                                       terminalState.IsSuccessful &&
                                       !streamReportedError &&
                                       !streamCancelled &&
                                       fullAnswer.Length > 0;

            if (fullAnswer.Length > 0 && !generationSucceeded)
            {
                fallbackTriggered = true;
                fallbackReason ??= streamCancelled
                    ? "rag_stream_cancelled"
                    : "rag_stream_incomplete";
            }

            // RAG failed or produced nothing: send a graceful fallback to the client and use it as the saved answer.
            if (fullAnswer.Length == 0 && !context.RequestAborted.IsCancellationRequested)
            {
                fallbackTriggered = true;
                fullAnswer.Append(DefaultRagFailureMessage);
                try
                {
                    foreach (string payload in RagStreamContract.CreateGracefulFallbackPayloads(
                                 backendRequestId,
                                 fallbackReason,
                                 DefaultRagFailureMessage))
                    {
                        await context.Response.WriteAsync($"data: {payload}\n\n", context.RequestAborted);
                    }
                    await context.Response.Body.FlushAsync(context.RequestAborted);
                }
                catch (Exception ex)
                {
                    logger.LogWarning(ex, "Failed to emit fallback stream message.");
                }
            }

            // Only the exact completion -> done terminal contract may create or
            // promote an immutable answer version. Partial, malformed, cancelled,
            // and transport-failed streams remain transient client output.
            bool completedVersionReady = generationSucceeded && !isAuthenticated;
            if (isAuthenticated && ask != null && generationSucceeded)
            {
                PersistReplyResult persisted = await PersistReplyVersionAsync(
                    db,
                    ask,
                    fullAnswer.ToString(),
                    sources,
                    fallbackTriggered,
                    fallbackReason,
                    replaceExistingCurrent: true,
                    requestId: backendRequestId,
                    suggestedQuestions: suggestedQuestions,
                    grounded: grounded,
                    groundingScore: groundingScore);

                completedVersionReady = persisted.Persisted;
            }

            // Completion is recorded only after the explicit RAG terminal event
            // and (for authenticated users) a successful immutable-version commit.
            // Telemetry failure is intentionally non-blocking for chat delivery.
            if (completedVersionReady)
            {
                try
                {
                    var eventContext = await ProductTelemetry.ResolveContextAsync(
                        db,
                        context,
                        configuration,
                        askDto.ChatId,
                        validatedGuestSubjectId,
                        CancellationToken.None);
                    await ProductTelemetry.RecordAsync(
                        db,
                        configuration,
                        eventContext,
                        new ProductEventData(
                            ProductEventTypes.AnswerCompleted,
                            backendRequestId,
                            askDto.ChatId,
                            SuggestionCount: suggestedQuestions.Count,
                            IsFallback: fallbackTriggered,
                            Grounded: grounded,
                            SourceCount: sources.Count),
                        CancellationToken.None);
                }
                catch (Exception exception)
                {
                    logger.LogWarning(exception, "Answer completion telemetry write failed.");
                }
            }
        });

        app.MapPost("/followups", async (
            FollowupRequestDto request,
            ServerDbContext db,
            HttpContext context,
            IConfiguration configuration,
            IHttpClientFactory httpClientFactory,
            ILogger<Program> logger,
            IDataProtectionProvider dataProtectionProvider) =>
        {
            if (string.IsNullOrWhiteSpace(request.RequestId) || request.RequestId.Length > 200)
            {
                return Results.BadRequest(new { message = "requestId가 필요합니다." });
            }

            ChatMessage? ownedAnswer = null;
            Guid? verifiedChatId = null;
            string? validatedGuestSubjectId = null;
            bool isAuthenticated = TryGetUserId(context, out Guid userId);
            if (isAuthenticated)
            {
                ownedAnswer = await (
                    from answer in db.ChatMessages
                    join chat in db.Chats on answer.ChatId equals chat.Id
                    where !answer.IsAsk
                          && answer.IsCurrent
                          && answer.RequestId == request.RequestId
                          && chat.UserId == userId
                    select answer)
                    .FirstOrDefaultAsync(context.RequestAborted);
                if (ownedAnswer is null) return Results.NotFound();
                verifiedChatId = ownedAnswer.ChatId;

                // 이미 생성된 값은 RAG LLM을 다시 호출하지 않고 그대로 돌려준다.
                List<string>? cached = DeserializeSuggestedQuestions(ownedAnswer.SuggestedQuestionsJson);
                if (cached is { Count: > 0 }) return Results.Ok(new { questions = cached });
            }
            else if (!GuestIdentity.TryValidate(
                         context.Request,
                         dataProtectionProvider,
                         out validatedGuestSubjectId))
            {
                return Results.Unauthorized();
            }

            ProductEventContext eventContext = await ProductTelemetry.ResolveContextAsync(
                db,
                context,
                configuration,
                verifiedChatId,
                validatedGuestSubjectId,
                context.RequestAborted);
            ProductEvent? completionEvent = await ProductTelemetry.FindAnswerCompletionEventAsync(
                db,
                configuration,
                eventContext,
                request.RequestId,
                context.RequestAborted);
            if (completionEvent is null) return Results.NotFound();

            var ragUrl = configuration["RagServiceUrl"]
                ?? configuration["RAG_SERVICE_URL"]
                ?? "http://rag-service:8000";
            var client = httpClientFactory.CreateClient();
            client.Timeout = TimeSpan.FromMinutes(2);

            try
            {
                using var response = await client.PostAsJsonAsync(
                    $"{ragUrl}/followups",
                    new { requestId = request.RequestId },
                    JsonOptions,
                    context.RequestAborted);
                if (!response.IsSuccessStatusCode)
                {
                    logger.LogWarning(
                        "RAG followup request failed. StatusCode={StatusCode}",
                        (int)response.StatusCode);
                    return Results.Ok(new { questions = Array.Empty<string>() });
                }

                RagFollowupResponse? payload = await response.Content.ReadFromJsonAsync<RagFollowupResponse>(
                    JsonOptions,
                    context.RequestAborted);
                List<string> questions = (payload?.Questions ?? [])
                    .Where(question => !string.IsNullOrWhiteSpace(question))
                    .Select(question => question.Trim())
                    .Distinct(StringComparer.Ordinal)
                    .Take(10)
                    .ToList();

                if (ownedAnswer is not null)
                {
                    // 재생성으로 현재 답변이 바뀐 경우 낡은 요청의 추천을 새 답변에 붙이지 않는다.
                    bool stillCurrent = await db.ChatMessages.AnyAsync(
                        answer => answer.Id == ownedAnswer.Id
                                  && answer.IsCurrent
                                  && answer.RequestId == request.RequestId,
                        context.RequestAborted);
                    if (!stillCurrent) return Results.Ok(new { questions = Array.Empty<string>() });
                    ownedAnswer.SuggestedQuestionsJson = SerializeSuggestedQuestions(questions);
                }
                await ProductTelemetry.UpdateAnswerSuggestionCountAsync(
                    db,
                    configuration,
                    eventContext,
                    request.RequestId,
                    questions.Count,
                    context.RequestAborted);
                if (ownedAnswer is not null) await db.SaveChangesAsync(context.RequestAborted);

                return Results.Ok(new { questions });
            }
            catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested)
            {
                return Results.Empty;
            }
            catch (Exception exception)
            {
                logger.LogWarning(exception, "RAG followup request failed.");
                return Results.Ok(new { questions = Array.Empty<string>() });
            }
        });

        app.MapPost("/feedback", async (FeedbackDto feedback, ServerDbContext db, HttpContext context, IConfiguration configuration, IHttpClientFactory httpClientFactory, ILogger<Program> logger, IDataProtectionProvider dataProtectionProvider) =>
        {
            if (string.IsNullOrWhiteSpace(feedback.RequestId) || feedback.RequestId.Length > 200)
            {
                return Results.BadRequest(new { message = "requestId가 필요합니다." });
            }
            if (feedback.Rating is not (1 or -1))
            {
                return Results.BadRequest(new { message = "rating은 1 또는 -1이어야 합니다." });
            }
            if ((feedback.Comment?.Length ?? 0) > 2000)
            {
                return Results.BadRequest(new { message = "의견은 2,000자까지 입력할 수 있습니다." });
            }

            Guid? verifiedChatId = null;
            bool isAuthenticated = TryGetUserId(context, out Guid feedbackUserId);
            string? validatedGuestSubjectId = null;
            if (isAuthenticated)
            {
                verifiedChatId = await (
                    from answer in db.ChatMessages
                    join chat in db.Chats on answer.ChatId equals chat.Id
                    where !answer.IsAsk
                          && answer.IsCurrent
                          && answer.RequestId == feedback.RequestId
                          && chat.UserId == feedbackUserId
                    select (Guid?)answer.ChatId)
                    .FirstOrDefaultAsync(context.RequestAborted);
                if (verifiedChatId is null) return Results.NotFound();
            }
            else if (!GuestIdentity.TryValidate(context.Request, dataProtectionProvider, out validatedGuestSubjectId))
            {
                return Results.BadRequest(new { message = "유효한 게스트 세션이 필요합니다." });
            }

            var eventContext = await ProductTelemetry.ResolveContextAsync(
                db,
                context,
                configuration,
                verifiedChatId,
                validatedGuestSubjectId,
                context.RequestAborted);
            ProductEvent? completionEvent = await ProductTelemetry.FindAnswerCompletionEventAsync(
                db,
                configuration,
                eventContext,
                feedback.RequestId,
                context.RequestAborted);
            if (!isAuthenticated && completionEvent is null)
            {
                return Results.NotFound(new { message = "이 게스트가 완료한 답변을 찾을 수 없습니다." });
            }

            var ragUrl = configuration["RagServiceUrl"] ?? configuration["RAG_SERVICE_URL"] ?? "http://rag-service:8000";
            var client = httpClientFactory.CreateClient();
            var payload = new
            {
                requestId = feedback.RequestId,
                rating = feedback.Rating,
                reason = feedback.Reason,
                comment = feedback.Comment,
                sessionId = (string?)null,
                major = isAuthenticated ? ResolveMajor(context) : null
            };

            try
            {
                string answerKey = ProductTelemetry.BuildPseudonymousKey(configuration, "answer", feedback.RequestId);
                var strategy = db.Database.CreateExecutionStrategy();

                // EnableRetryOnFailure requires user-initiated transactions to run inside the execution strategy.
                return await strategy.ExecuteAsync(async () =>
                {
                    await using var feedbackTransaction = await db.Database.BeginTransactionAsync(context.RequestAborted);
                    await db.Database.ExecuteSqlInterpolatedAsync(
                        $"SELECT pg_advisory_xact_lock(hashtextextended({answerKey}, 0));",
                        context.RequestAborted);
                    int? existingRating = await ProductTelemetry.FindFeedbackRatingAsync(
                        db,
                        configuration,
                        eventContext,
                        feedback.RequestId,
                        context.RequestAborted);
                    FeedbackDecision feedbackDecision = FeedbackPolicy.Decide(existingRating, feedback.Rating);
                    if (feedbackDecision is not FeedbackDecision.Accept)
                    {
                        await feedbackTransaction.CommitAsync(context.RequestAborted);
                        return feedbackDecision == FeedbackDecision.Duplicate
                            ? Results.Ok(new { ok = true, duplicate = true })
                            : Results.Conflict(new { message = "이미 반대 평가가 제출된 답변입니다." });
                    }

                    using var response = await client.PostAsJsonAsync($"{ragUrl}/feedback", payload, JsonOptions, context.RequestAborted);
                    if (response.IsSuccessStatusCode)
                    {
                        await ProductTelemetry.RecordAsync(
                            db,
                            configuration,
                            eventContext,
                            new ProductEventData(
                                ProductEventTypes.FeedbackSubmitted,
                                feedback.RequestId,
                                verifiedChatId,
                                Rating: feedback.Rating),
                            context.RequestAborted);
                        await feedbackTransaction.CommitAsync(context.RequestAborted);
                        return Results.Ok(new { ok = true });
                    }

                    logger.LogWarning(
                        "RAG feedback request failed. StatusCode={StatusCode}",
                        (int)response.StatusCode
                    );
                    return Results.StatusCode(StatusCodes.Status502BadGateway);
                });
            }
            catch (Exception ex)
            {
                logger.LogWarning(ex, "RAG feedback request failed.");
                return Results.StatusCode(StatusCodes.Status500InternalServerError);
            }
        });

        app.MapPost("/load", async (ServerDbContext db, HttpContext context, LoadChat load) =>
        {
            // For guests, return empty list (no persistence)
            if (!TryGetUserId(context, out Guid loadUserId))
            {
                return Results.Ok(new List<ChatMessageDto>());
            }

            // 인증 사용자는 본인 소유 채팅방만 열람할 수 있다(IDOR 방지).
            if (!await UserOwnsChatAsync(db, load.ChatId, loadUserId))
            {
                return Results.Forbid();
            }

            List<ChatMessageDto> chatMessages = await MessagesToList(db, load.LastTime, load.ChatId);
            return Results.Ok(chatMessages);
        });

        app.MapPatch("/{chatId}", async (ServerDbContext db, HttpContext context, Guid chatId, RenameChat body) =>
        {
            // 게스트 대화는 서버에 없으므로 클라이언트가 자체 저장소에서 이름을 바꾼다.
            if (!TryGetUserId(context, out Guid renameUserId))
            {
                return Results.Unauthorized();
            }

            string title = (body?.Title ?? string.Empty).Trim();
            if (title.Length == 0)
            {
                return Results.BadRequest(new { message = "대화 이름을 입력해주세요." });
            }
            if (title.Length > MaxChatTitleLength)
            {
                return Results.BadRequest(new { message = $"대화 이름은 {MaxChatTitleLength}자까지 입력할 수 있습니다." });
            }

            var chat = await db.Chats.FirstOrDefaultAsync(c => c.Id == chatId && c.UserId == renameUserId);
            if (chat is null)
            {
                return Results.NotFound(new { message = "대화를 찾을 수 없습니다." });
            }

            chat.Title = title;
            await db.SaveChangesAsync();
            return Results.Ok(new { id = chat.Id, title = chat.Title });
        });

        // 게스트 대화를 로그인 계정으로 옮긴다.
        // 게스트 대화는 서버에 저장되지 않으므로(브라우저 localStorage에만 존재) 클라이언트가
        // 보낸 기록을 그대로 적재한다. 유효한 게스트 토큰을 소유권 증명으로 요구하고,
        // 남의 계정을 임의 기록으로 채우지 못하도록 건수·길이 상한을 둔다.
        app.MapPost("/claim", async (
            ServerDbContext db,
            HttpContext context,
            ClaimGuestChats body,
            IDataProtectionProvider dataProtectionProvider) =>
        {
            if (!TryGetUserId(context, out Guid claimUserId))
            {
                return Results.Unauthorized();
            }

            if (!GuestIdentity.TryValidate(context.Request, dataProtectionProvider, out _))
            {
                return Results.Json(new { message = "유효한 게스트 세션이 없습니다." }, statusCode: StatusCodes.Status403Forbidden);
            }

            var incoming = body?.Chats ?? [];
            if (incoming.Count == 0)
            {
                return Results.Ok(new { claimed = 0 });
            }
            if (incoming.Count > MaxClaimChats)
            {
                return Results.BadRequest(new { message = $"한 번에 옮길 수 있는 대화는 {MaxClaimChats}개까지입니다." });
            }

            var organizationIds = incoming.Select(chat => chat.OrganizationId).Distinct().ToList();
            var knownOrganizationIds = await db.Organizations
                .Where(org => organizationIds.Contains(org.Id))
                .Select(org => org.Id)
                .ToListAsync();

            DateTime now = DateTime.UtcNow;
            int claimed = 0;

            foreach (var incomingChat in incoming)
            {
                if (!knownOrganizationIds.Contains(incomingChat.OrganizationId))
                {
                    continue;
                }

                string title = (incomingChat.Title ?? string.Empty).Trim();
                if (title.Length == 0) title = "이전 대화";
                if (title.Length > MaxChatTitleLength) title = title[..MaxChatTitleLength];

                var chat = new ActiveChat
                {
                    Id = Guid.NewGuid(),
                    UserId = claimUserId,
                    OrganizationId = incomingChat.OrganizationId,
                    Title = title,
                    CreatedTime = now,
                    UpdatedTime = now,
                };
                await db.Chats.AddAsync(chat);

                var messages = (incomingChat.Messages ?? [])
                    .Where(message => !string.IsNullOrWhiteSpace(message.Content))
                    .Take(MaxClaimMessagesPerChat)
                    .ToList();

                foreach (var message in messages)
                {
                    string content = message.Content!.Length > MaxChatContentLength
                        ? message.Content[..MaxChatContentLength]
                        : message.Content;

                    await db.ChatMessages.AddAsync(new ChatMessage
                    {
                        Id = Guid.NewGuid(),
                        ChatId = chat.Id,
                        IsAsk = message.IsAsk,
                        Content = content,
                        // 클라이언트 시각을 그대로 믿으면 순서가 뒤집힐 수 있으므로 UTC로 정규화한다.
                        CreatedTime = NormalizeClaimedTime(message.CreatedTime, now),
                    });
                }

                claimed += 1;
            }

            await db.SaveChangesAsync();
            return Results.Ok(new { claimed });
        });

        app.MapDelete("/{chatId}", async (ServerDbContext db, HttpContext context, Guid chatId) =>
        {
            if (TryGetUserId(context, out Guid userId))
            {
                var chat = await db.Chats.FirstOrDefaultAsync(c => c.Id == chatId && c.UserId == userId);

                if (chat != null)
                {
                    var messages = db.ChatMessages.Where(m => m.ChatId == chatId);
                    db.ChatMessages.RemoveRange(messages);

                    db.Chats.Remove(chat);
                    await db.SaveChangesAsync();

                    return Results.Ok();
                }
                return Results.NotFound();
            }

            return Results.Ok();
        });
    }

    /// <summary>사이드바 한 줄에 들어갈 미리보기 길이.</summary>
    private const int MessagePreviewLength = 70;

    // 줄바꿈과 마크다운 기호는 목록 한 줄에서 의미가 없으므로 단어 구분자로 취급한다.
    private static readonly char[] MessagePreviewSeparators =
        [' ', '\t', '\r', '\n', '#', '*', '_', '`', '>', '[', ']', '-'];

    static private string? BuildMessagePreview(string? content)
    {
        if (string.IsNullOrWhiteSpace(content))
        {
            return null;
        }

        var flattened = string.Join(
            ' ',
            content.Split(MessagePreviewSeparators, StringSplitOptions.RemoveEmptyEntries));

        if (flattened.Length == 0)
        {
            return null;
        }

        if (flattened.Length <= MessagePreviewLength)
        {
            return flattened;
        }

        // 대리 쌍(이모지 등) 중간에서 자르면 깨진 문자가 남으므로 한 글자 앞에서 끊는다.
        int cut = MessagePreviewLength;
        if (char.IsLowSurrogate(flattened[cut]))
        {
            cut -= 1;
        }

        return $"{flattened[..cut]}…";
    }

    static private ActiveChatDto ToActiveChatDto(ActiveChat chat)
    {
        if (chat.Organization?.Major is null)
        {
            throw new InvalidOperationException($"Chat {chat.Id} has no organization/major relationship.");
        }

        return new ActiveChatDto
        {
            Id = chat.Id,
            Title = chat.Title,
            Organization = new OrganizationDto
            {
                Id = chat.Organization.Id,
                Major = new MajorDto(chat.Organization.Major.Id, chat.Organization.Major.Majorname)
            }
        };
    }

    static private ChatMessage ToQuestionEntity(ChatMessageDto askDto, string content)
    {
        DateTime createdTime = askDto.CreatedTime == default
            ? DateTime.UtcNow
            : askDto.CreatedTime.Kind switch
            {
                DateTimeKind.Utc => askDto.CreatedTime,
                DateTimeKind.Local => askDto.CreatedTime.ToUniversalTime(),
                _ => DateTime.SpecifyKind(askDto.CreatedTime, DateTimeKind.Utc)
            };

        return new ChatMessage
        {
            Id = askDto.Id,
            ChatId = askDto.ChatId,
            IsAsk = true,
            Content = content,
            CreatedTime = createdTime,
            IsCurrent = true
        };
    }

    // PostgreSQL advisory locking serializes regenerations for one question. The
    // partial unique indexes remain the final invariant guard in case another
    // writer bypasses this application path.
    private sealed record PersistReplyResult(bool Persisted, Guid? ReplyId);

    static private async Task<PersistReplyResult> PersistReplyVersionAsync(
        ServerDbContext db,
        ChatMessage question,
        string content,
        List<ChatSourceDto>? sources,
        bool isFallback,
        string? fallbackReason,
        bool replaceExistingCurrent,
        string? requestId,
        List<string>? suggestedQuestions,
        bool? grounded,
        double? groundingScore)
    {
        var strategy = db.Database.CreateExecutionStrategy();
        Guid replyId = Guid.NewGuid();

        return await strategy.ExecuteAsync(async () =>
        {
            // A transient retry can leave entities from the rolled-back attempt in
            // the change tracker. Detach only answer-version writes owned here.
            foreach (var entry in db.ChangeTracker.Entries<ChatMessage>()
                         .Where(entry => !entry.Entity.IsAsk &&
                                         entry.Entity.ParentQuestionId == question.Id)
                         .ToList())
            {
                entry.State = EntityState.Detached;
            }

            await using var transaction = await db.Database.BeginTransactionAsync();
            await db.Database.ExecuteSqlInterpolatedAsync(
                $"SELECT pg_advisory_xact_lock(hashtextextended({question.Id.ToString()}, 0));");

            // If a transient connection failure happened after PostgreSQL committed
            // but before the client observed it, retrying with the same ID is
            // idempotent and must not create yet another answer version.
            if (await db.ChatMessages.AnyAsync(message => message.Id == replyId))
            {
                await transaction.CommitAsync();
                return new PersistReplyResult(true, replyId);
            }

            var currentAnswers = await db.ChatMessages
                .Where(message => !message.IsAsk &&
                                  message.ParentQuestionId == question.Id &&
                                  message.IsCurrent)
                .ToListAsync();

            if (currentAnswers.Count > 0 && !replaceExistingCurrent)
            {
                await transaction.CommitAsync();
                return new PersistReplyResult(false, null);
            }

            int latestVersion = await db.ChatMessages
                    .Where(message => !message.IsAsk &&
                                      message.ParentQuestionId == question.Id &&
                                      message.AnswerVersion != null)
                    .Select(message => message.AnswerVersion)
                    .MaxAsync()
                ?? 0;

            DateTime versionCreatedTime = DateTime.UtcNow;
            DateTime conversationCreatedTime = await db.ChatMessages
                    .Where(message => !message.IsAsk && message.ParentQuestionId == question.Id)
                    .Select(message => (DateTime?)message.CreatedTime)
                    .MinAsync()
                ?? versionCreatedTime;

            foreach (var currentAnswer in currentAnswers)
            {
                currentAnswer.IsCurrent = false;
            }

            // Flush the demotion before inserting the new current row. Both writes
            // are still atomic because the transaction is committed only afterward.
            if (currentAnswers.Count > 0)
            {
                await db.SaveChangesAsync();
            }

            ChatMessage reply = new()
            {
                Id = replyId,
                ChatId = question.ChatId,
                Content = content,
                IsAsk = false,
                // Preserve the original answer slot so regenerating an old answer
                // does not move it to the bottom of the conversation on reload.
                CreatedTime = conversationCreatedTime,
                ParentQuestionId = question.Id,
                AnswerVersion = latestVersion + 1,
                VersionCreatedTime = versionCreatedTime,
                IsCurrent = true,
                SourcesJson = SerializeSources(sources),
                RequestId = requestId,
                SuggestedQuestionsJson = SerializeSuggestedQuestions(suggestedQuestions),
                Grounded = grounded,
                GroundingScore = groundingScore,
                IsFallback = isFallback,
                FallbackReason = fallbackReason
            };

            await db.ChatMessages.AddAsync(reply);
            await db.SaveChangesAsync();
            await transaction.CommitAsync();
            return new PersistReplyResult(true, replyId);
        });
    }

    // JWT의 sub 클레임에서 사용자 GUID를 추출한다.
    static private bool TryGetUserId(HttpContext context, out Guid userId)
    {
        userId = Guid.Empty;
        if (context.User.Identity?.IsAuthenticated != true) return false;
        var userIdStr = context.User.FindFirstValue(Microsoft.IdentityModel.JsonWebTokens.JwtRegisteredClaimNames.Sub);
        return userIdStr != null && Guid.TryParse(userIdStr, out userId);
    }

    // 인증 사용자가 해당 채팅방의 소유자인지 확인한다(IDOR 방지).
    static private async Task<bool> UserOwnsChatAsync(ServerDbContext db, Guid chatId, Guid userId)
        => await db.Chats.AnyAsync(c => c.Id == chatId && c.UserId == userId);

    static public async Task<List<ChatMessageDto>> MessagesToList(ServerDbContext db, DateTime lastTime, Guid chatId)
    {
        var messages = await db.ChatMessages
                .Where(cm => Equals(cm.ChatId, chatId) &&
                             cm.CreatedTime < lastTime &&
                             (cm.IsAsk || cm.ParentQuestionId == null || cm.IsCurrent))
                .OrderByDescending(cm => cm.CreatedTime)
                .Take(20)
                .ToListAsync();

        return messages.Select(message => ToDto(message)).ToList();
    }

    static private ChatMessageDto ToDto(ChatMessage message)
    {
        return new ChatMessageDto
        {
            Id = message.Id,
            ChatId = message.ChatId,
            IsAsk = message.IsAsk,
            Content = message.Content,
            CreatedTime = message.CreatedTime,
            Sources = DeserializeSources(message.SourcesJson),
            RequestId = message.RequestId,
            SuggestedQuestions = DeserializeSuggestedQuestions(message.SuggestedQuestionsJson),
            Grounded = message.Grounded,
            GroundingScore = message.GroundingScore,
            IsFallback = message.IsFallback,
            FallbackReason = message.FallbackReason
        };
    }

    // JWT의 "Major" 클레임에서 학과명을 추출한다. 값이 없거나 "Unknown"이면 null을 반환해
    // RAG 서비스가 학과 필터를 적용하지 않도록 한다.
    static private string? ResolveMajor(HttpContext context)
    {
        string? major = context.User.FindFirstValue("Major");
        if (string.IsNullOrWhiteSpace(major) || major == "Unknown")
        {
            return null;
        }
        return major;
    }

    static private string? SerializeSources(List<ChatSourceDto>? sources)
    {
        return sources is null ? null : JsonSerializer.Serialize(sources, JsonOptions);
    }

    static private string? SerializeSuggestedQuestions(List<string>? questions)
        => questions is null ? null : JsonSerializer.Serialize(questions, JsonOptions);

    static private List<string>? DeserializeSuggestedQuestions(string? questionsJson)
    {
        if (string.IsNullOrWhiteSpace(questionsJson)) return null;
        try
        {
            return JsonSerializer.Deserialize<List<string>>(questionsJson, JsonOptions);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    static private List<ChatSourceDto>? DeserializeSources(string? sourcesJson)
    {
        if (string.IsNullOrWhiteSpace(sourcesJson))
        {
            return null;
        }

        try
        {
            return JsonSerializer.Deserialize<List<ChatSourceDto>>(sourcesJson, JsonOptions);
        }
        catch (JsonException)
        {
            return null;
        }
    }

    static private List<ChatSourceDto> MapSources(List<RagSource>? sources)
    {
        if (sources is null)
        {
            return [];
        }

        return sources.Select(source => new ChatSourceDto
        {
            Source = source.Source,
            ChunkId = source.ChunkId,
            Title = source.Title,
            Url = source.Url,
            PublishedAt = source.PublishedAt,
            Snippet = source.Snippet,
            VectorScore = source.VectorScore,
            SparseScore = source.SparseScore,
            HybridScore = source.HybridScore,
            RecencyScore = source.RecencyScore,
            FinalScore = source.FinalScore
        }).ToList();
    }

    static private string? ReadBoundedString(JsonElement element, string propertyName, int maxLength)
    {
        if (!element.TryGetProperty(propertyName, out var property)
            || property.ValueKind != JsonValueKind.String)
        {
            return null;
        }

        string? value = property.GetString();
        return string.IsNullOrWhiteSpace(value) || value.Length > maxLength ? null : value;
    }

    static private bool? ReadNullableBoolean(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var property)
            || property.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
        {
            return null;
        }
        return property.GetBoolean();
    }

    static private double? ReadGroundingScore(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var property)
            || property.ValueKind != JsonValueKind.Number
            || !property.TryGetDouble(out double score)
            || double.IsNaN(score)
            || double.IsInfinity(score)
            || score < 0
            || score > 1)
        {
            return null;
        }
        return score;
    }

    static private List<string> ReadSuggestedQuestions(
        JsonElement element,
        string propertyName = "questions",
        List<string>? fallback = null)
    {
        if (!element.TryGetProperty(propertyName, out var property)
            || property.ValueKind != JsonValueKind.Array)
        {
            return fallback ?? [];
        }

        return property.EnumerateArray()
            .Where(item => item.ValueKind == JsonValueKind.String)
            .Select(item => item.GetString()?.Trim())
            .Where(item => !string.IsNullOrWhiteSpace(item) && item.Length <= 300)
            .Select(item => item!)
            .Distinct(StringComparer.Ordinal)
            .Take(10)
            .ToList();
    }

}
