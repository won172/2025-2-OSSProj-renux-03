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
            DateTime time = DateTime.Now.ToUniversalTime();

            Guid id = Guid.NewGuid();

            while (await db.Chats.AnyAsync(c => c.Id == id))
                id = Guid.NewGuid();
            
            ActiveChat chat = new()
            {
                Id = id,
                UserId = Guid.Parse(context.User.FindFirstValue(ClaimTypes.NameIdentifier)!),
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
                CreatedTime = DateTime.Now.ToUniversalTime()
            };

            await db.ChatMessages.AddAsync(apply);
            await db.SaveChangesAsync();

            ChatMessageDto applyDto = mapper.Map<ChatMessageDto>(apply);

            return Results.Ok(applyDto);
        });

        app.MapPost("/startguest", async (ServerDbContext db, ChatMessageDto askDto, IMapper mapper) =>
        {

        });

        app.MapPost("/load", async (ServerDbContext db, IMapper mapper, LoadChat load) =>
        {
            List<ChatMessageDto> chatMessages = await MessagesToList(db, mapper, load.LastTime, load.ChatId);

            return Results.Ok(chatMessages);
        }).RequireAuthorization();
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
