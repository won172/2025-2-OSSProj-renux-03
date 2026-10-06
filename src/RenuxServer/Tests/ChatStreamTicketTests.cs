using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using System.Security.Claims;
using System.Net;
using RenuxServer.Dtos.ChatDtos;
using RenuxServer.Services;

internal static class ChatStreamTicketTests
{
    private sealed class ManualClock : TimeProvider
    {
        public DateTimeOffset Now { get; set; } = DateTimeOffset.Parse("2026-10-06T14:00:00Z");
        public override DateTimeOffset GetUtcNow() => Now;
    }

    private static ChatMessageDto Body(Guid chatId, Guid? id = null, string content = "동국대학교 학식 메뉴",
        DateTime? created = null, bool isAsk = true) => new()
    {
        Id = id ?? Guid.NewGuid(), ChatId = chatId, IsAsk = isAsk,
        Content = content, CreatedTime = created ?? DateTime.Parse("2026-10-06T14:00:00Z").ToUniversalTime()
    };

    public static async Task RunAsync(Action<bool, string> check)
    {
        var provider = new EphemeralDataProtectionProvider();
        var clock = new ManualClock();
        var service = new ChatStreamTicketService(provider, clock);
        Guid userId = Guid.NewGuid(), chatId = Guid.NewGuid();
        var body = Body(chatId);
        check(service.TryIssue(userId, body, out var issued) && issued is not null,
            "A valid authenticated scoped request can issue a stream ticket.");
        check(issued!.ExpiresAt - clock.Now == TimeSpan.FromSeconds(45)
              && ChatStreamTicketService.Lifetime <= TimeSpan.FromSeconds(60),
            "Stream tickets must expire within sixty seconds (configured forty-five).");
        check(!issued.Ticket.Contains(userId.ToString(), StringComparison.Ordinal)
              && !issued.Ticket.Contains(body.Content, StringComparison.Ordinal),
            "Protected tickets must not expose identifiers or question content.");

        check(!service.TryConsume(issued.Ticket, Body(Guid.NewGuid(), body.Id), null, out _),
            "A ticket cannot authorize another chat.");
        check(!service.TryConsume(issued.Ticket, Body(chatId), null, out _),
            "A ticket cannot authorize another question id.");
        check(!service.TryConsume(issued.Ticket, Body(chatId, body.Id, body.Content + " "), null, out _),
            "Content binding is exact and includes trailing whitespace.");
        check(!service.TryConsume(issued.Ticket, Body(chatId, body.Id, body.Content,
                body.CreatedTime.AddMilliseconds(1)), null, out _),
            "The original request creation timestamp is bound to the ticket.");
        check(!service.TryConsume(issued.Ticket, Body(chatId, body.Id, body.Content, isAsk: false), null, out _),
            "A question ticket cannot be used as an answer payload.");
        check(!service.TryConsume(issued.Ticket, body, Guid.NewGuid(), out _),
            "An authenticated cookie for another user cannot borrow a ticket identity.");
        check(service.TryConsume(issued.Ticket, body, null, out Guid granted) && granted == userId,
            "A missing browser cookie can consume the exact ticket and resolve its protected user.");
        check(!service.TryConsume(issued.Ticket, body, userId, out _),
            "A consumed ticket cannot replay even when a matching cookie is authenticated.");

        check(service.TryIssue(userId, body, out var sameCookieTicket)
              && service.TryConsume(sameCookieTicket!.Ticket, body, userId, out _),
            "A matching authenticated cookie still uses the ticket's one-use semantics.");
        check(service.TryIssue(userId, body, out var expiring), "Expiry test ticket issuance succeeds.");
        clock.Now = clock.Now.AddSeconds(45);
        check(!service.TryConsume(expiring!.Ticket, body, null, out _),
            "A ticket is rejected at its expiration boundary.");
        check(!service.TryConsume("not-a-ticket", body, null, out _), "Malformed protected values must fail closed.");
        string guestPurpose = GuestIdentity.Issue(provider);
        check(!service.TryConsume(guestPurpose, body, null, out _),
            "The guest protector purpose cannot authenticate a stream user ticket.");
        var foreignProvider = new ChatStreamTicketService(new EphemeralDataProtectionProvider(), clock);
        check(service.TryIssue(userId, body, out var foreignKeyTicket)
              && !foreignProvider.TryConsume(foreignKeyTicket!.Ticket, body, null, out _),
            "A different data-protection key ring cannot validate a ticket.");

        var concurrent = new ChatStreamTicketService(provider, clock);
        concurrent.TryIssue(userId, body, out var raceTicket);
        int successes = 0;
        await Task.WhenAll(Enumerable.Range(0, 32).Select(index => Task.Run(() =>
        {
            if (concurrent.TryConsume(raceTicket!.Ticket, body, null, out _))
                Interlocked.Increment(ref successes);
        })));
        check(successes == 1, "Exactly one of thirty-two racing requests may consume a nonce.");

        var bounded = new ChatStreamTicketService(provider, clock);
        var capacityTickets = new List<(ChatMessageDto Body, ChatStreamTicketService.IssuedTicket Ticket)>();
        for (int index = 0; index < ChatStreamTicketService.MaxPendingPerUser; index++)
        {
            var pendingBody = Body(chatId);
            check(bounded.TryIssue(userId, pendingBody, out var ticket), "Pending tickets fit the explicit per-user bound.");
            capacityTickets.Add((pendingBody, ticket!));
        }
        check(!bounded.TryIssue(userId, Body(chatId), out _), "Pending tickets must not exceed the per-user bound.");
        bounded.TryConsume(capacityTickets[0].Ticket.Ticket, capacityTickets[0].Body, null, out _);
        check(bounded.TryIssue(userId, Body(chatId), out _), "Consuming a ticket immediately frees capacity.");
        clock.Now = clock.Now.AddSeconds(46);
        check(bounded.TryIssue(userId, Body(chatId), out _), "Expired nonces are removed before checking capacity.");
        check(!service.TryIssue(Guid.Empty, body, out _), "An empty user identity cannot issue a capability.");
        check(!service.TryIssue(userId, Body(Guid.Empty), out _), "An empty chat id cannot issue a capability.");
        check(!service.TryIssue(userId, Body(chatId, Guid.Empty), out _), "An empty question id cannot issue a capability.");
        check(!service.TryIssue(userId, Body(chatId, content: new string('x', 2001)), out _),
            "Stream ticket issuance respects the existing two-thousand-character question limit.");

        var rateService = new ChatStreamTicketService(provider, clock);
        using var services = new ServiceCollection().AddSingleton(rateService).BuildServiceProvider();
        var firstNative = new DefaultHttpContext { RequestServices = services };
        firstNative.Connection.RemoteIpAddress = IPAddress.Parse("192.0.2.10");
        var secondNative = new DefaultHttpContext { RequestServices = services };
        secondNative.Connection.RemoteIpAddress = firstNative.Connection.RemoteIpAddress;
        rateService.TryIssue(userId, body, out var firstNativeTicket);
        Guid secondUser = Guid.NewGuid();
        rateService.TryIssue(secondUser, body, out var secondNativeTicket);
        firstNative.Request.Headers[ChatStreamTicketService.HeaderName] = firstNativeTicket!.Ticket;
        secondNative.Request.Headers[ChatStreamTicketService.HeaderName] = secondNativeTicket!.Ticket;
        check(ChatStreamTicketService.ResolveRateLimitPartition(firstNative) == $"user:{userId}"
              && ChatStreamTicketService.ResolveRateLimitPartition(secondNative) == $"user:{secondUser}",
            "Different native users on one campus IP receive independent trusted user rate partitions.");
        check(firstNative.User.Identity?.IsAuthenticated != true
              && rateService.TryPeekPendingUser(firstNativeTicket.Ticket, out Guid hintedUser) && hintedUser == userId,
            "A rate hint must not authenticate the HTTP principal or consume the nonce.");
        check(rateService.TryConsume(firstNativeTicket.Ticket, body, null, out _),
            "A pending rate hint leaves body-bound consumption available exactly once.");
        check(ChatStreamTicketService.ResolveRateLimitPartition(firstNative) == "ip:192.0.2.10",
            "A replayed ticket returns to the unchanged anonymous/IP rate bucket.");
        secondNative.User = new ClaimsPrincipal(new ClaimsIdentity([new Claim("sub", userId.ToString())], "cookie"));
        check(ChatStreamTicketService.ResolveRateLimitPartition(secondNative) == $"user:{userId}",
            "An authenticated cookie always retains its original trusted user partition.");
        secondNative.User = new ClaimsPrincipal(new ClaimsIdentity());
        secondNative.Request.Headers[ChatStreamTicketService.HeaderName] = new[] { secondNativeTicket.Ticket, secondNativeTicket.Ticket };
        check(ChatStreamTicketService.ResolveRateLimitPartition(secondNative) == "ip:192.0.2.10",
            "Multiple ticket headers never supply a user rate hint.");
        secondNative.Request.Headers[ChatStreamTicketService.HeaderName] = new string('x', 2049);
        check(ChatStreamTicketService.ResolveRateLimitPartition(secondNative) == "ip:192.0.2.10",
            "Oversized tickets stay in the existing IP rate bucket.");
        secondNative.Request.Headers[ChatStreamTicketService.HeaderName] = "invalid";
        check(ChatStreamTicketService.ResolveRateLimitPartition(secondNative) == "ip:192.0.2.10",
            "Malformed tickets stay in the existing IP rate bucket.");
        secondNative.Request.Headers[ChatStreamTicketService.HeaderName] = secondNativeTicket.Ticket;
        clock.Now = clock.Now.AddSeconds(46);
        check(ChatStreamTicketService.ResolveRateLimitPartition(secondNative) == "ip:192.0.2.10",
            "Expired pending capabilities never supply user rate hints.");
    }
}
