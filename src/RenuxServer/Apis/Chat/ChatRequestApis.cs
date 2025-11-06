using AutoMapper;
using Microsoft.EntityFrameworkCore;
using System.Security.Claims;

using RenuxServer.DbContexts;
using RenuxServer.Dtos.ChatDtos;
using RenuxServer.Models;

namespace RenuxServer.Apis.Chat;

public record StartChat(Guid OrgId, string Title);

static public class ChatRequestApis
{
    static public void AddChatApis(this WebApplication application)
    {
        var app = application.MapGroup("/chat");

        app.MapGet("/active", async (ServerDbContext db, HttpContext context, IMapper mapper) =>
        {
            Guid id = Guid.Parse(context.User.FindFirstValue(ClaimTypes.NameIdentifier)!);

            List<ActiveChatDto> chats = 
            mapper.Map<List<ActiveChatDto>>(
                await db.Chats
                .Include(ch => ch.Organization)
                .Where(ch => Equals(ch.UserId, id))
                .ToListAsync()
                );

            return Results.Ok(chats);
        }).RequireAuthorization();

        app.MapPost("/start", async (ServerDbContext db, HttpContext context, StartChat stch, IMapper mapper) =>
        {
            long time = DateTime.Now.Ticks;
            ActiveChat chat = new()
            {
                UserId = Guid.Parse(context.User.FindFirstValue(ClaimTypes.NameIdentifier)!),
                OrganizationId = stch.OrgId,
                Title = stch.Title,
                CreatedTime = time,
                UpdatedTime = time
            };

            await db.Chats.AddAsync(chat);
            await db.SaveChangesAsync();

            ActiveChatDto chatDto = mapper.Map<ActiveChatDto>(chat);

            return Results.Ok(chatDto);
        }).RequireAuthorization();

        app.MapPost("/msg", async (ServerDbContext db, ChatMessageDto askDto, IMapper mapper) =>
        {
            ChatMessage ask = mapper.Map<ChatMessage>(askDto);

            await db.ChatMessages.AddAsync(ask);

            ChatMessage apply = new()
            {
                ChatId = ask.ChatId,
                Content = "대답입니다.",
                IsAsk = false,
                CreatedTime = DateTime.Now.Ticks
            };

            await db.ChatMessages.AddAsync(apply);
            await db.SaveChangesAsync();


            
        });

        app.MapPost("/startguest", () =>
        {

        });

        app.MapPost("/load", async (ServerDbContext db, Guid chatId, IMapper mapper, long lastTime) =>
        {
            List<ChatMessageDto> chatMessages = await MessagesToList(db, mapper, lastTime, chatId);

            return Results.Ok(chatMessages);
        }).RequireAuthorization();
    }

    static public async Task<List<ChatMessageDto>> MessagesToList(ServerDbContext db, IMapper mapper, long lastTime, Guid chatId)
    {
        List<ChatMessageDto> chatMessages = mapper.Map<List<ChatMessageDto>>(
                await db.ChatMessages.Where(cm => Equals(cm.ChatId, chatId) && cm.CreatedTime < lastTime)
                .OrderByDescending(cm => cm.CreatedTime)
                .Take(20)
                .ToListAsync());

        return chatMessages;
    }
}
