using System.Security.Cryptography;
using System.Security.Claims;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Http;
using RenuxServer.Dtos.ChatDtos;

namespace RenuxServer.Services;

/// <summary>
/// A short-lived, one-question capability for the native HTTP → WKWebView SSE
/// boundary. It never exposes or replaces the user's general JWT credential.
/// Pending nonces are bounded and consumed atomically in this single API process.
/// </summary>
public sealed class ChatStreamTicketService
{
    public const string HeaderName = "X-Dongttok-Stream-Ticket";
    public const int MaxPendingTickets = 4096;
    public const int MaxPendingPerUser = 8;
    public const int MaxContentLength = 2000;
    public static readonly TimeSpan Lifetime = TimeSpan.FromSeconds(45);
    private const string Purpose = "RenuxServer.NativeChatStreamTicket.v1";
    private const int MaxTicketLength = 2048;
    private readonly IDataProtector protector;
    private readonly TimeProvider clock;
    private readonly object gate = new();
    private readonly Dictionary<string, TicketPayload> pending = new(StringComparer.Ordinal);

    private sealed record TicketPayload(
        int Version, string Nonce, Guid UserId, Guid ChatId, Guid QuestionId,
        string ContentHash, long CreatedTimeTicks, long ExpiresAtUnixMilliseconds);

    public sealed record IssuedTicket(string Ticket, DateTimeOffset ExpiresAt);

    public ChatStreamTicketService(IDataProtectionProvider provider, TimeProvider clock)
    {
        protector = provider.CreateProtector(Purpose);
        this.clock = clock;
    }

    public static bool IsValidRequest(ChatMessageDto request) =>
        request.Id != Guid.Empty && request.ChatId != Guid.Empty && request.IsAsk
        && !string.IsNullOrWhiteSpace(request.Content) && request.Content.Length <= MaxContentLength;

    public bool TryIssue(Guid userId, ChatMessageDto request, out IssuedTicket? issued)
    {
        issued = null;
        if (userId == Guid.Empty || !IsValidRequest(request)) return false;
        DateTimeOffset expiresAt = clock.GetUtcNow().Add(Lifetime);
        var payload = new TicketPayload(
            1, Convert.ToHexString(RandomNumberGenerator.GetBytes(32)), userId,
            request.ChatId, request.Id, HashContent(request.Content),
            request.CreatedTime.ToUniversalTime().Ticks, expiresAt.ToUnixTimeMilliseconds());
        string protectedValue = protector.Protect(JsonSerializer.Serialize(payload));
        lock (gate)
        {
            RemoveExpired();
            if (pending.Count >= MaxPendingTickets
                || pending.Values.Count(value => value.UserId == userId) >= MaxPendingPerUser)
                return false;
            pending.Add(payload.Nonce, payload);
        }
        issued = new IssuedTicket(protectedValue, expiresAt);
        return true;
    }

    public bool TryConsume(
        string? ticket, ChatMessageDto request, Guid? authenticatedUserId, out Guid userId)
    {
        userId = Guid.Empty;
        if (!IsValidRequest(request) || string.IsNullOrWhiteSpace(ticket)
            || ticket.Length > MaxTicketLength) return false;
        TicketPayload? payload;
        try
        {
            payload = JsonSerializer.Deserialize<TicketPayload>(protector.Unprotect(ticket));
        }
        catch (Exception exception) when (exception is CryptographicException or FormatException or JsonException)
        {
            return false;
        }
        if (payload is null || payload.Version != 1 || string.IsNullOrEmpty(payload.Nonce) || payload.Nonce.Length != 64
            || payload.UserId == Guid.Empty || payload.ChatId != request.ChatId
            || payload.QuestionId != request.Id
            || payload.CreatedTimeTicks != request.CreatedTime.ToUniversalTime().Ticks
            || payload.ContentHash != HashContent(request.Content)
            || (authenticatedUserId.HasValue && authenticatedUserId.Value != payload.UserId))
            return false;
        lock (gate)
        {
            RemoveExpired();
            if (payload.ExpiresAtUnixMilliseconds <= clock.GetUtcNow().ToUnixTimeMilliseconds()
                || !pending.TryGetValue(payload.Nonce, out TicketPayload? expected)
                || expected != payload)
                return false;
            pending.Remove(payload.Nonce);
        }
        userId = payload.UserId;
        return true;
    }

    /// <summary>Rate partition hint only: no principal or nonce consumption.</summary>
    public bool TryPeekPendingUser(string? ticket, out Guid userId)
    {
        userId = Guid.Empty;
        if (string.IsNullOrWhiteSpace(ticket) || ticket.Length > MaxTicketLength) return false;
        TicketPayload? payload;
        try
        {
            payload = JsonSerializer.Deserialize<TicketPayload>(protector.Unprotect(ticket));
        }
        catch (Exception exception) when (exception is CryptographicException or FormatException or JsonException)
        {
            return false;
        }
        if (payload is null || payload.Version != 1 || string.IsNullOrEmpty(payload.Nonce)
            || payload.Nonce.Length != 64 || payload.UserId == Guid.Empty) return false;
        lock (gate)
        {
            RemoveExpired();
            if (payload.ExpiresAtUnixMilliseconds <= clock.GetUtcNow().ToUnixTimeMilliseconds()
                || !pending.TryGetValue(payload.Nonce, out TicketPayload? expected) || expected != payload)
                return false;
            userId = payload.UserId;
            return true;
        }
    }

    public static string ResolveRateLimitPartition(HttpContext context)
    {
        string? subject = context.User.FindFirstValue("sub");
        if (context.User.Identity?.IsAuthenticated == true && !string.IsNullOrWhiteSpace(subject))
            return $"user:{subject}";
        if (context.Request.Headers.TryGetValue(HeaderName, out var values) && values.Count == 1
            && context.RequestServices.GetService<ChatStreamTicketService>() is { } service
            && service.TryPeekPendingUser(values[0], out Guid userId))
            return $"user:{userId}";
        return $"ip:{context.Connection.RemoteIpAddress?.ToString() ?? "unknown"}";
    }

    private static string HashContent(string content) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(content)));

    private void RemoveExpired()
    {
        long now = clock.GetUtcNow().ToUnixTimeMilliseconds();
        foreach (string nonce in pending.Where(pair => pair.Value.ExpiresAtUnixMilliseconds <= now)
                     .Select(pair => pair.Key).ToArray())
            pending.Remove(nonce);
    }
}
