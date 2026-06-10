using AutoMapper;
using Microsoft.EntityFrameworkCore;
using System.Net.Http.Json;
using System.Security.Claims;
using System.Text.Json;
using System.Text.Json.Serialization;

using RenuxServer.DbContexts;
using RenuxServer.Dtos.ChatDtos;
using RenuxServer.Models;
using RenuxServer.Dtos.EtcDtos;

namespace RenuxServer.Apis.Chat;

public record StartChat(OrganizationDto Org, string Title);
public record LoadChat(Guid ChatId, DateTime LastTime);
public record ToRag(string SessionId, string Question, string? Major = null);
public record Reply(
    string Answer,
    List<RagSource>? Sources,
    [property: JsonPropertyName("fallback_triggered")] bool FallbackTriggered,
    [property: JsonPropertyName("fallback_reason")] string? FallbackReason
);
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
public record RagCallResult(string Answer, string RequestId, bool Succeeded, List<ChatSourceDto> Sources, bool FallbackTriggered, string? FallbackReason);

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

    static public void AddChatApis(this WebApplication application)
    {
        var app = application.MapGroup("/chat");

        app.MapGet("/active", async (ServerDbContext db, HttpContext context, IMapper mapper) =>
        {
            // For guests, return an empty list as their chats are not persisted
            if (!context.Request.Cookies.ContainsKey("renux-server-token"))
            {
                return Results.Ok(new List<ActiveChatDto>());
            }

            Guid id;
            var userIdStr = context.User.FindFirstValue(Microsoft.IdentityModel.JsonWebTokens.JwtRegisteredClaimNames.Sub);
            if (userIdStr == null || !Guid.TryParse(userIdStr, out id))
            {
                return Results.Unauthorized();
            }

            List<ActiveChatDto> chats =
            mapper.Map<List<ActiveChatDto>>(
                await db.Chats
                .Include(ch => ch.Organization)
                .Where(ch => Equals(ch.UserId, id))
                .ToListAsync()
                );

            return Results.Ok(chats);
        });

        app.MapPost("/start", async (ServerDbContext db, HttpContext context, StartChat stch, IMapper mapper, IConfiguration configuration) =>
        {
            DateTime time = DateTime.UtcNow;
            Guid id = Guid.NewGuid();

            // Authenticated user check
            bool isAuthenticated = context.Request.Cookies.ContainsKey("renux-server-token");

            if (!isAuthenticated)
            {
                // Guest flow: Do NOT save to DB
                // Create a temporary ID for the frontend session
                Guid guestChatId = Guid.NewGuid();
                
                // Ensure the guest cookie exists for session consistency (optional but good practice)
                if (!context.Request.Cookies.ContainsKey("renux-server-guest"))
                {
                    CookieOptions opt = BuildGuestCookieOptions(configuration);
                    context.Response.Cookies.Append("renux-server-guest", Guid.NewGuid().ToString(), opt);
                }

                ActiveChatDto guestChatDto = new()
                {
                    Id = guestChatId,
                    Organization = stch.Org,
                    Title = stch.Title
                };
                return Results.Ok(guestChatDto);
            }

            // Authenticated User Flow
            while (await db.Chats.AnyAsync(c => c.Id == id))
                id = Guid.NewGuid();

            var userIdStr = context.User.FindFirstValue(Microsoft.IdentityModel.JsonWebTokens.JwtRegisteredClaimNames.Sub);
            if (userIdStr == null || !Guid.TryParse(userIdStr, out Guid userId))
            {
                return Results.Unauthorized();
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

            ActiveChatDto chatDto = mapper.Map<ActiveChatDto>(chat);

            return Results.Ok(chatDto);
        });

        app.MapPost("/msg", async (ServerDbContext db, HttpContext context, ChatMessageDto askDto, IMapper mapper, ILogger<Program> logger, IConfiguration configuration, IHttpClientFactory httpClientFactory) =>
        {
            bool isAuthenticated = context.Request.Cookies.ContainsKey("renux-server-token");

            if (!isAuthenticated)
            {
                // Guest Flow: Do NOT save to DB
                ToRag toRag = new(askDto.ChatId.ToString(), askDto.Content);
                var ragResult = await CallRagAsync(toRag, context, configuration, httpClientFactory, logger);
                string replyContent = ragResult.Answer;
                logger.LogInformation(
                    "Guest AI reply generated. RequestId={RequestId}, Succeeded={Succeeded}",
                    ragResult.RequestId,
                    ragResult.Succeeded
                );

                ChatMessageDto replyDto = new()
                {
                    ChatId = askDto.ChatId,
                    Content = replyContent,
                    IsAsk = false,
                    CreatedTime = DateTime.UtcNow,
                    Sources = ragResult.Sources,
                    IsFallback = ragResult.FallbackTriggered,
                    FallbackReason = ragResult.FallbackReason
                };
                return Results.Ok(replyDto);
            }

            // Authenticated Flow
            if (!TryGetUserId(context, out Guid msgUserId))
            {
                return Results.Unauthorized();
            }
            if (!await UserOwnsChatAsync(db, askDto.ChatId, msgUserId))
            {
                return Results.Forbid();
            }

            ChatMessage ask = mapper.Map<ChatMessage>(askDto);
            await db.ChatMessages.AddAsync(ask);

            ToRag authToRag = new(askDto.ChatId.ToString(), askDto.Content, ResolveMajor(context));
            var authRagResult = await CallRagAsync(authToRag, context, configuration, httpClientFactory, logger);
            string authReply = authRagResult.Answer;
            logger.LogInformation(
                "Authenticated AI reply generated. RequestId={RequestId}, Succeeded={Succeeded}",
                authRagResult.RequestId,
                authRagResult.Succeeded
            );

            ChatMessage apply = new()
            {
                ChatId = ask.ChatId,
                Content = authReply,
                IsAsk = false,
                CreatedTime = DateTime.UtcNow,
                SourcesJson = SerializeSources(authRagResult.Sources),
                IsFallback = authRagResult.FallbackTriggered,
                FallbackReason = authRagResult.FallbackReason
            };

            await db.ChatMessages.AddAsync(apply);
            await db.SaveChangesAsync();

            ChatMessageDto applyDto = ToDto(apply);
            return Results.Ok(applyDto);
        });

        app.MapPost("/stream", async (ServerDbContext db, HttpContext context, ChatMessageDto askDto, IMapper mapper, ILogger<Program> logger, IConfiguration configuration, IHttpClientFactory httpClientFactory) =>
        {
            bool isAuthenticated = context.Request.Cookies.ContainsKey("renux-server-token");
            string sessionId = askDto.ChatId.ToString();
            string question = askDto.Content;
            string? major = isAuthenticated ? ResolveMajor(context) : null;

            // Authenticated: verify chat ownership (IDOR 방지), then save User's question first so it is never lost.
            ChatMessage? ask = null;
            if (isAuthenticated)
            {
                if (!TryGetUserId(context, out Guid streamUserId))
                {
                    context.Response.StatusCode = StatusCodes.Status401Unauthorized;
                    return;
                }
                if (!await UserOwnsChatAsync(db, askDto.ChatId, streamUserId))
                {
                    context.Response.StatusCode = StatusCodes.Status403Forbidden;
                    return;
                }

                ask = mapper.Map<ChatMessage>(askDto);
                await db.ChatMessages.AddAsync(ask);
                await db.SaveChangesAsync();
            }

            context.Response.ContentType = "text/event-stream";
            context.Response.Headers.CacheControl = "no-cache";
            context.Response.Headers.Connection = "keep-alive";

            var fullAnswer = new System.Text.StringBuilder();
            List<ChatSourceDto> sources = [];
            bool fallbackTriggered = false;
            string? fallbackReason = null;

            try
            {
                var ragUrl = configuration["RagServiceUrl"] ?? configuration["RAG_SERVICE_URL"] ?? "http://rag-service:8000";
                var client = httpClientFactory.CreateClient();
                client.Timeout = TimeSpan.FromMinutes(5); // Streaming needs longer timeout

                using var request = new HttpRequestMessage(HttpMethod.Post, $"{ragUrl}/ask/stream")
                {
                    Content = JsonContent.Create(new { question, sessionId, major })
                };
                request.Headers.TryAddWithoutValidation("X-Request-ID", context.TraceIdentifier);

                using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, context.RequestAborted);

                if (!response.IsSuccessStatusCode)
                {
                    var errorBody = await response.Content.ReadAsStringAsync(context.RequestAborted);
                    logger.LogWarning(
                        "RAG stream request failed. RequestId={RequestId}, StatusCode={StatusCode}, Body={Body}",
                        context.TraceIdentifier, (int)response.StatusCode, errorBody);
                }
                else
                {
                    using var reader = new StreamReader(await response.Content.ReadAsStreamAsync(context.RequestAborted));

                    while (await reader.ReadLineAsync(context.RequestAborted) is { } line)
                    {
                        // Re-emit verbatim, preserving blank lines so SSE event framing (\n\n) survives the proxy.
                        await context.Response.WriteAsync($"{line}\n", context.RequestAborted);
                        await context.Response.Body.FlushAsync(context.RequestAborted);

                        if (line.Length == 0 || !line.StartsWith("data: ")) continue;

                        try
                        {
                            var json = line.Substring(6);
                            var chunk = JsonSerializer.Deserialize<JsonElement>(json);
                            if (chunk.TryGetProperty("type", out var typeProp))
                            {
                                var type = typeProp.GetString();
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
                                else if (type == "text")
                                {
                                    if (chunk.TryGetProperty("content", out var contentProp))
                                    {
                                        fullAnswer.Append(contentProp.GetString());
                                    }
                                }
                            }
                        }
                        catch (Exception ex)
                        {
                            logger.LogWarning(ex, "Failed to parse streaming chunk: {Line}", line);
                        }
                    }
                }
            }
            catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested)
            {
                // Client disconnected mid-stream: stop streaming but still persist what we have below.
                logger.LogInformation("Chat stream cancelled by client. RequestId={RequestId}", context.TraceIdentifier);
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "RAG stream error. RequestId={RequestId}", context.TraceIdentifier);
            }

            // RAG failed or produced nothing: send a graceful fallback to the client and use it as the saved answer.
            if (fullAnswer.Length == 0 && !context.RequestAborted.IsCancellationRequested)
            {
                fallbackTriggered = true;
                fullAnswer.Append(DefaultRagFailureMessage);
                try
                {
                    var meta = JsonSerializer.Serialize(
                        new { type = "metadata", sources = Array.Empty<object>(), fallback_triggered = true, fallback_reason = (string?)null },
                        JsonOptions);
                    var text = JsonSerializer.Serialize(new { type = "text", content = DefaultRagFailureMessage }, JsonOptions);
                    await context.Response.WriteAsync($"data: {meta}\n\n", context.RequestAborted);
                    await context.Response.WriteAsync($"data: {text}\n\n", context.RequestAborted);
                    await context.Response.Body.FlushAsync(context.RequestAborted);
                }
                catch (Exception ex)
                {
                    logger.LogWarning(ex, "Failed to emit fallback stream message. RequestId={RequestId}", context.TraceIdentifier);
                }
            }

            // Always persist a reply for authenticated users so the saved question is never orphaned.
            if (isAuthenticated && ask != null)
            {
                ChatMessage reply = new()
                {
                    ChatId = ask.ChatId,
                    Content = fullAnswer.ToString(),
                    IsAsk = false,
                    CreatedTime = DateTime.UtcNow,
                    SourcesJson = SerializeSources(sources),
                    IsFallback = fallbackTriggered,
                    FallbackReason = fallbackReason
                };
                await db.ChatMessages.AddAsync(reply);
                await db.SaveChangesAsync();
            }
        });

        app.MapPost("/load", async (ServerDbContext db, HttpContext context, LoadChat load) =>
        {
            // For guests, return empty list (no persistence)
            if (!context.Request.Cookies.ContainsKey("renux-server-token"))
            {
                return Results.Ok(new List<ChatMessageDto>());
            }

            // 인증 사용자는 본인 소유 채팅방만 열람할 수 있다(IDOR 방지).
            if (!TryGetUserId(context, out Guid loadUserId))
            {
                return Results.Unauthorized();
            }
            if (!await UserOwnsChatAsync(db, load.ChatId, loadUserId))
            {
                return Results.Forbid();
            }

            List<ChatMessageDto> chatMessages = await MessagesToList(db, load.LastTime, load.ChatId);
            return Results.Ok(chatMessages);
        });

        app.MapDelete("/{chatId}", async (ServerDbContext db, HttpContext context, Guid chatId) =>
        {
            if (context.Request.Cookies.ContainsKey("renux-server-token"))
            {
                var userIdStr = context.User.FindFirstValue(Microsoft.IdentityModel.JsonWebTokens.JwtRegisteredClaimNames.Sub);
                if (userIdStr == null || !Guid.TryParse(userIdStr, out var userId))
                {
                    return Results.Unauthorized();
                }

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

    // JWT의 sub 클레임에서 사용자 GUID를 추출한다.
    static private bool TryGetUserId(HttpContext context, out Guid userId)
    {
        userId = Guid.Empty;
        var userIdStr = context.User.FindFirstValue(Microsoft.IdentityModel.JsonWebTokens.JwtRegisteredClaimNames.Sub);
        return userIdStr != null && Guid.TryParse(userIdStr, out userId);
    }

    // 인증 사용자가 해당 채팅방의 소유자인지 확인한다(IDOR 방지).
    static private async Task<bool> UserOwnsChatAsync(ServerDbContext db, Guid chatId, Guid userId)
        => await db.Chats.AnyAsync(c => c.Id == chatId && c.UserId == userId);

    static public async Task<List<ChatMessageDto>> MessagesToList(ServerDbContext db, DateTime lastTime, Guid chatId)
    {
        var messages = await db.ChatMessages.Where(cm => Equals(cm.ChatId, chatId) && cm.CreatedTime < lastTime)
                .OrderByDescending(cm => cm.CreatedTime)
                .Take(20)
                .ToListAsync();

        return messages.Select(ToDto).ToList();
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

    static private string? SerializeSources(List<ChatSourceDto> sources)
    {
        return sources.Count == 0 ? null : JsonSerializer.Serialize(sources, JsonOptions);
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

    static private async Task<RagCallResult> CallRagAsync(
        ToRag toRag,
        HttpContext context,
        IConfiguration configuration,
        IHttpClientFactory httpClientFactory,
        ILogger logger)
    {
        string requestId = context.TraceIdentifier;
        var ragUrl = configuration["RagServiceUrl"] ?? configuration["RAG_SERVICE_URL"] ?? "http://rag-service:8000";
        var timeoutSeconds = configuration.GetValue<int?>("RagServiceTimeoutSeconds") ?? 30;

        using var timeoutCts = new CancellationTokenSource(TimeSpan.FromSeconds(timeoutSeconds));
        using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(context.RequestAborted, timeoutCts.Token);

        var client = httpClientFactory.CreateClient();
        using var request = new HttpRequestMessage(HttpMethod.Post, $"{ragUrl}/ask")
        {
            Content = JsonContent.Create(toRag)
        };
        request.Headers.TryAddWithoutValidation("X-Request-ID", requestId);

        try
        {
            var response = await client.SendAsync(request, linkedCts.Token);
            var ragRequestId = response.Headers.TryGetValues("X-Request-ID", out var values)
                ? values.FirstOrDefault() ?? requestId
                : requestId;

            if (!response.IsSuccessStatusCode)
            {
                var errorBody = await response.Content.ReadAsStringAsync(linkedCts.Token);
                logger.LogWarning(
                    "RAG request failed. RequestId={RequestId}, RagRequestId={RagRequestId}, StatusCode={StatusCode}, Retry={Retry}, Body={Body}",
                    requestId,
                    ragRequestId,
                    (int)response.StatusCode,
                    false,
                    errorBody
                );
                return new RagCallResult(DefaultRagFailureMessage, ragRequestId, false, [], false, null);
            }

            var replyObj = await response.Content.ReadFromJsonAsync<Reply>(cancellationToken: linkedCts.Token);
            return new RagCallResult(
                replyObj?.Answer ?? DefaultRagFailureMessage,
                ragRequestId,
                replyObj != null,
                MapSources(replyObj?.Sources),
                replyObj?.FallbackTriggered ?? false,
                replyObj?.FallbackReason
            );
        }
        catch (OperationCanceledException) when (timeoutCts.IsCancellationRequested && !context.RequestAborted.IsCancellationRequested)
        {
            logger.LogWarning(
                "RAG request timed out. RequestId={RequestId}, TimeoutSeconds={TimeoutSeconds}, Retry={Retry}",
                requestId,
                timeoutSeconds,
                false
            );
            return new RagCallResult(DefaultRagFailureMessage, requestId, false, [], false, null);
        }
        catch (HttpRequestException ex)
        {
            logger.LogError(
                ex,
                "RAG request transport error. RequestId={RequestId}, Retry={Retry}",
                requestId,
                false
            );
            return new RagCallResult(DefaultRagFailureMessage, requestId, false, [], false, null);
        }
    }
}
