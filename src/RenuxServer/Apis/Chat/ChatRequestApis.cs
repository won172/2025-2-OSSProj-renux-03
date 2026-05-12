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
public record ToRag(string SessionId, string Question);
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
            DateTime time = DateTime.Now.ToUniversalTime();
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
                    CreatedTime = DateTime.Now.ToUniversalTime(),
                    Sources = ragResult.Sources,
                    IsFallback = ragResult.FallbackTriggered,
                    FallbackReason = ragResult.FallbackReason
                };
                return Results.Ok(replyDto);
            }

            // Authenticated Flow
            ChatMessage ask = mapper.Map<ChatMessage>(askDto);
            await db.ChatMessages.AddAsync(ask);

            ToRag authToRag = new(askDto.ChatId.ToString(), askDto.Content);
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
                CreatedTime = DateTime.Now.ToUniversalTime(),
                SourcesJson = SerializeSources(authRagResult.Sources),
                IsFallback = authRagResult.FallbackTriggered,
                FallbackReason = authRagResult.FallbackReason
            };

            await db.ChatMessages.AddAsync(apply);
            await db.SaveChangesAsync();

            ChatMessageDto applyDto = ToDto(apply);
            return Results.Ok(applyDto);
        });

        app.MapPost("/load", async (ServerDbContext db, HttpContext context, LoadChat load) =>
        {
            // For guests, return empty list (no persistence)
            if (!context.Request.Cookies.ContainsKey("renux-server-token"))
            {
                return Results.Ok(new List<ChatMessageDto>());
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
