using AutoMapper;
using Microsoft.EntityFrameworkCore;
using System.Security.Claims;

using RenuxServer.DbContexts;
using RenuxServer.Dtos.ChatDtos;
using RenuxServer.Models;
using RenuxServer.Dtos.EtcDtos;

namespace RenuxServer.Apis.Chat;

public record StartChat(OrganizationDto Org, string Title);
public record LoadChat(Guid ChatId, DateTime LastTime);
public record ToRag(string SessionId, string Question);
public record Reply(string Answer);

static public class ChatRequestApis
{
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

        app.MapPost("/start", async (ServerDbContext db, HttpContext context, StartChat stch, IMapper mapper) =>
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
                    CookieOptions opt = new() { HttpOnly = true, SameSite = SameSiteMode.Strict };
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
                var client = httpClientFactory.CreateClient();
                var ragUrl = configuration["RagServiceUrl"] ?? "http://rag-service:8000";
                
                // Call RAG
                var res = await client.PostAsJsonAsync($"{ragUrl}/ask", toRag);
                string replyContent = "죄송합니다. 답변을 생성할 수 없습니다.";
                if (res.IsSuccessStatusCode)
                {
                    var replyObj = await res.Content.ReadFromJsonAsync<Reply>();
                    if (replyObj != null) replyContent = replyObj.Answer;
                }
                logger.LogInformation("Guest AI Reply: {ReplyContent}", replyContent);

                ChatMessageDto replyDto = new()
                {
                    ChatId = askDto.ChatId,
                    Content = replyContent,
                    IsAsk = false,
                    CreatedTime = DateTime.Now.ToUniversalTime()
                };
                return Results.Ok(replyDto);
            }

            // Authenticated Flow
            ChatMessage ask = mapper.Map<ChatMessage>(askDto);
            await db.ChatMessages.AddAsync(ask);

            ToRag authToRag = new(askDto.ChatId.ToString(), askDto.Content);
            var authClient = httpClientFactory.CreateClient();
            var authRagUrl = configuration["RagServiceUrl"] ?? "http://rag-service:8000";
            
            var authRes = await authClient.PostAsJsonAsync($"{authRagUrl}/ask", authToRag);
            string authReply = "죄송합니다. 답변을 생성할 수 없습니다.";
            if (authRes.IsSuccessStatusCode)
            {
                var replyObj = await authRes.Content.ReadFromJsonAsync<Reply>();
                if (replyObj != null) authReply = replyObj.Answer;
            }
            logger.LogInformation("Authenticated AI Reply: {Reply}", authReply);

            ChatMessage apply = new()
            {
                ChatId = ask.ChatId,
                Content = authReply,
                IsAsk = false,
                CreatedTime = DateTime.Now.ToUniversalTime()
            };

            await db.ChatMessages.AddAsync(apply);
            await db.SaveChangesAsync();

            ChatMessageDto applyDto = mapper.Map<ChatMessageDto>(apply);
            return Results.Ok(applyDto);
        });

        app.MapPost("/load", async (ServerDbContext db, HttpContext context, IMapper mapper, LoadChat load) =>
        {
            // For guests, return empty list (no persistence)
            if (!context.Request.Cookies.ContainsKey("renux-server-token"))
            {
                return Results.Ok(new List<ChatMessageDto>());
            }

            List<ChatMessageDto> chatMessages = await MessagesToList(db, mapper, load.LastTime, load.ChatId);
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

    static public async Task<List<ChatMessageDto>> MessagesToList(ServerDbContext db, IMapper mapper, DateTime lastTime, Guid chatId)
    {
        List<ChatMessageDto> chatMessages = mapper.Map<List<ChatMessageDto>>(
                await db.ChatMessages.Where(cm => Equals(cm.ChatId, chatId) && cm.CreatedTime < lastTime)
                .OrderByDescending(cm => cm.CreatedTime)
                .Take(20)
                .ToListAsync());

        return chatMessages;
    }
}
