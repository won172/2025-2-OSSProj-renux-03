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
            if (!context.Request.Cookies.ContainsKey("renux-server-token") && context.Request.Cookies.ContainsKey("renux-server-guest"))
            {
                return Results.Ok(new List<ActiveChatDto>());
            }

            Guid id;
            if (context.Request.Cookies.ContainsKey("renux-server-token"))
            {
                id = Guid.Parse(context.User.FindFirstValue(ClaimTypes.NameIdentifier)!);
            }
            else
            {
                id = Guid.NewGuid();
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

            while (await db.Chats.AnyAsync(c => c.Id == id))
                id = Guid.NewGuid();

            Guid userId;

            if (context.Request.Cookies.ContainsKey("renux-server-token"))
            {
                userId = Guid.Parse(context.User.FindFirstValue(ClaimTypes.NameIdentifier)!);
            }
            else
            {
                userId = Guid.NewGuid();

                CookieOptions opt = new()
                {
                    HttpOnly = true,
                    SameSite = SameSiteMode.Strict
                };

                context.Response.Cookies.Append("renux-server-guest", userId.ToString(), opt);
            }

            bool isGuest = !context.Request.Cookies.ContainsKey("renux-server-token");

            if (isGuest)
            {
                // Guest specific flow: No DB persistence, return a dummy DTO
                // Use the guest's userId as the "chat Id" for the frontend
                ActiveChatDto guestChatDto = new()
                {
                    Id = userId, // Using guest's ID as chat ID for non-persisted session
                    Organization = stch.Org, // Pass through from request
                    Title = stch.Title // Pass through from request
                };
                return Results.Ok(guestChatDto);
            }
            else
            {
                // Authenticated user flow (existing logic)
                ActiveChat chat = new()
                {
                    Id = id, // Note: This `id` is a *new* GUID for the chat
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
                    Content = $"안녕하세요. {stch.Org.Major.Majorname} 전문 동똑이입니다. 무엇을 도와드릴까요?",
                    CreatedTime = time
                };

                await db.Chats.AddAsync(chat);
                await db.ChatMessages.AddAsync(startChat);
                await db.SaveChangesAsync();

                ActiveChatDto chatDto = mapper.Map<ActiveChatDto>(chat);

                return Results.Ok(chatDto);
            }
        });

        

        app.MapPost("/msg", async (ServerDbContext db, HttpContext context, ChatMessageDto askDto, IMapper mapper, ILogger<Program> logger) =>
        {
            bool isGuest = !context.Request.Cookies.ContainsKey("renux-server-token");

            if (isGuest)
            {
                // Guest flow: Don't save to DB
                // Only interact with RAG and return the reply
                ToRag toRag = new(askDto.ChatId.ToString(), askDto.Content);

                HttpClient client = new();
                var res = await client.PostAsJsonAsync("http://rag-service:8000/ask", toRag);

                string replyContent = "대답이여";

                if (res.IsSuccessStatusCode)
                {
                    replyContent = (await res.Content.ReadFromJsonAsync<Reply>())!.Answer;
                }
                logger.LogInformation("Guest AI Reply: {ReplyContent}", replyContent);

                // Create a ChatMessageDto for the reply, but don't save to DB
                ChatMessageDto replyDto = new()
                {
                    ChatId = askDto.ChatId,
                    Content = replyContent,
                    IsAsk = false,
                    CreatedTime = DateTime.Now.ToUniversalTime()
                };

                return Results.Ok(replyDto);
            }
            else
            {
                // Authenticated user flow (existing logic)
                ChatMessage ask = mapper.Map<ChatMessage>(askDto);

                await db.ChatMessages.AddAsync(ask);

                ToRag toRag = new(askDto.ChatId.ToString(), askDto.Content);

                HttpClient client = new();

                var res = await client.PostAsJsonAsync("http://rag-service:8000/ask", toRag);

                string reply = "대답이여";

                if (res.IsSuccessStatusCode)
                {
                    reply = (await res.Content.ReadFromJsonAsync<Reply>())!.Answer;
                }
                logger.LogInformation("Authenticated AI Reply: {Reply}", reply);

                ChatMessage apply = new()
                {
                    ChatId = ask.ChatId,
                    Content = reply,
                    IsAsk = false,
                    CreatedTime = DateTime.Now.ToUniversalTime()
                };

                await db.ChatMessages.AddAsync(apply);
                await db.SaveChangesAsync();

                ChatMessageDto applyDto = mapper.Map<ChatMessageDto>(apply);

                return Results.Ok(applyDto);
            }
        });

        app.MapPost("/load", async (ServerDbContext db, HttpContext context, IMapper mapper, LoadChat load) =>
        {
            // For guests, return an empty list as their chats are not persisted
            if (!context.Request.Cookies.ContainsKey("renux-server-token") && context.Request.Cookies.ContainsKey("renux-server-guest"))
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
                var userId = Guid.Parse(context.User.FindFirstValue(ClaimTypes.NameIdentifier)!);

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
