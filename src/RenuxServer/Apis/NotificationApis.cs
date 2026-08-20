using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Caching.Memory;
using System.Globalization;
using System.IdentityModel.Tokens.Jwt;
using System.Net.Http.Json;
using System.Security.Claims;
using System.Text.Json;
using System.Text.Json.Serialization;

using RenuxServer.DbContexts;
using RenuxServer.Models;

namespace RenuxServer.Apis;

public record NotificationPreferenceDto(
    string Topic,
    bool Enabled,
    IReadOnlyList<int> RemindDaysBefore,
    string Channel
);

public record UpdateNotificationPreferencesRequest(List<NotificationPreferenceDto> Preferences);

public record NotificationCandidateDto(
    string Id,
    string Source,
    [property: JsonPropertyName("source_id")] string? SourceId,
    [property: JsonPropertyName("chunk_id")] string? ChunkId,
    string Title,
    string Topic,
    string? Category,
    [property: JsonPropertyName("target_date")] string TargetDate,
    [property: JsonPropertyName("start_date")] string? StartDate,
    [property: JsonPropertyName("end_date")] string? EndDate,
    [property: JsonPropertyName("d_day")] int DDay,
    [property: JsonPropertyName("published_at")] string? PublishedAt,
    string? Url,
    string? Snippet,
    double? Confidence,
    [property: JsonPropertyName("date_source")] string? DateSource,
    Dictionary<string, string?>? Metadata
);

public record DeadlineDto(
    string Id,
    string Source,
    string SourceLabel,
    string SourceId,
    string? ChunkId,
    string Title,
    string Topic,
    string TopicLabel,
    string? Category,
    DateTime TargetDate,
    int DDay,
    string? Url,
    string? Snippet,
    string? PublishedAt,
    string? DateSource
);

public record UserNotificationDto(
    Guid Id,
    string Topic,
    string TopicLabel,
    string Source,
    string SourceId,
    string Title,
    string Body,
    DateTime TargetDate,
    DateTime ReminderDate,
    int ReminderDaysBefore,
    string? Url,
    bool IsRead,
    DateTime CreatedTime,
    DateTime? ReadTime
);

public record NotificationSyncResult(int Created, int UpcomingMatched);

static public class NotificationApis
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private static readonly string[] SupportedTopics =
    [
        "scholarship",
        "course_registration",
        "tuition_payment",
        "document_submission",
        "academic_schedule",
    ];

    private static readonly Dictionary<string, string> TopicLabels = new(StringComparer.OrdinalIgnoreCase)
    {
        ["scholarship"] = "장학",
        ["course_registration"] = "수강신청",
        ["tuition_payment"] = "등록/납부",
        ["document_submission"] = "서류 제출",
        ["academic_schedule"] = "학사일정",
    };

    /// <summary>
    /// 설정을 처음 만들 때 켜 두는 주제.
    ///
    /// 전부 꺼진 채로 시작하면 설정 화면을 찾아 직접 켜기 전까지 알림이 0건이라,
    /// 기능이 있다는 사실조차 알기 어려웠다. 설정 행이 없다는 것은 사용자가 끈 적이
    /// 없다는 뜻이므로, 여기서 켜도 누군가의 명시적 선택을 덮어쓰지 않는다.
    /// 앱 안 배지 수준의 알림이라 기본 노출이 과하지 않다.
    /// </summary>
    private static readonly HashSet<string> DefaultEnabledTopics = new(StringComparer.OrdinalIgnoreCase)
    {
        "academic_schedule",
        "course_registration",
        "scholarship",
    };

    static public void AddNotificationApis(this WebApplication application)
    {
        var app = application.MapGroup("/notifications").RequireAuthorization();

        app.MapMethods("", new[] { "OPTIONS" }, () => Results.Ok()).AllowAnonymous();
        app.MapMethods("/{**path}", new[] { "OPTIONS" }, () => Results.Ok()).AllowAnonymous();

        app.MapGet("/preferences", async (ServerDbContext db, HttpContext context) =>
        {
            if (!TryGetUserId(context, out Guid userId))
            {
                return Results.Unauthorized();
            }

            var preferences = await EnsurePreferencesAsync(db, userId, context.RequestAborted);
            return Results.Ok(new { preferences = preferences.Select(ToDto).ToList() });
        });

        app.MapPut("/preferences", async (
            ServerDbContext db,
            HttpContext context,
            UpdateNotificationPreferencesRequest request) =>
        {
            if (!TryGetUserId(context, out Guid userId))
            {
                return Results.Unauthorized();
            }

            var preferences = await EnsurePreferencesAsync(db, userId, context.RequestAborted);
            var byTopic = preferences.ToDictionary(p => p.Topic, StringComparer.OrdinalIgnoreCase);
            var now = DateTime.UtcNow;

            foreach (var incoming in request.Preferences)
            {
                var topic = NormalizeTopic(incoming.Topic);
                if (!SupportedTopics.Contains(topic, StringComparer.OrdinalIgnoreCase) || !byTopic.TryGetValue(topic, out var preference))
                {
                    continue;
                }

                preference.Enabled = incoming.Enabled;
                preference.RemindDaysBefore = SerializeRemindDays(incoming.RemindDaysBefore);
                preference.Channel = string.IsNullOrWhiteSpace(incoming.Channel) ? "in_app" : incoming.Channel.Trim();
                preference.UpdatedTime = now;
            }

            await db.SaveChangesAsync(context.RequestAborted);
            return Results.Ok(new { preferences = preferences.Select(ToDto).ToList() });
        });

        app.MapGet("/deadlines", async (
            ServerDbContext db,
            HttpContext context,
            IConfiguration configuration,
            IHttpClientFactory httpClientFactory,
            IMemoryCache cache,
            ILogger<Program> logger,
            int daysAhead = 60,
            int limit = 100) =>
        {
            if (!TryGetUserId(context, out Guid userId))
            {
                return Results.Unauthorized();
            }

            var preferences = await EnsurePreferencesAsync(db, userId, context.RequestAborted);
            var enabledTopics = preferences.Where(p => p.Enabled).Select(p => p.Topic).ToHashSet(StringComparer.OrdinalIgnoreCase);
            if (enabledTopics.Count == 0)
            {
                return Results.Ok(Array.Empty<DeadlineDto>());
            }

            var candidates = await FetchCandidatesAsync(configuration, httpClientFactory, cache, logger, daysAhead, limit, context.RequestAborted);
            var deadlines = candidates
                .Select(candidate => ToDeadline(candidate))
                .Where(deadline => deadline is not null && enabledTopics.Contains(deadline.Topic))
                .Select(deadline => deadline!)
                .OrderBy(deadline => deadline.TargetDate)
                .ThenBy(deadline => deadline.Title)
                .Take(Math.Clamp(limit, 1, 300))
                .ToList();

            return Results.Ok(deadlines);
        });

        app.MapPost("/sync", async (
            ServerDbContext db,
            HttpContext context,
            IConfiguration configuration,
            IHttpClientFactory httpClientFactory,
            IMemoryCache cache,
            ILogger<Program> logger) =>
        {
            if (!TryGetUserId(context, out Guid userId))
            {
                return Results.Unauthorized();
            }

            var preferences = await EnsurePreferencesAsync(db, userId, context.RequestAborted);
            var enabledPreferences = preferences.Where(p => p.Enabled).ToList();
            if (enabledPreferences.Count == 0)
            {
                return Results.Ok(new NotificationSyncResult(0, 0));
            }

            var candidates = await FetchCandidatesAsync(configuration, httpClientFactory, cache, logger, 60, 200, context.RequestAborted);
            var today = TodayKst();
            var now = DateTime.UtcNow;
            var dueNotifications = new List<UserNotification>();
            var upcomingMatched = 0;

            foreach (var candidate in candidates)
            {
                var topic = InferTopic(candidate);
                var preference = enabledPreferences.FirstOrDefault(p => string.Equals(p.Topic, topic, StringComparison.OrdinalIgnoreCase));
                if (preference is null || !TryParseDateOnly(candidate.TargetDate, out var targetDate))
                {
                    continue;
                }

                upcomingMatched++;
                foreach (var daysBefore in ParseRemindDays(preference.RemindDaysBefore))
                {
                    var reminderDate = targetDate.AddDays(-daysBefore);
                    if (reminderDate > today || targetDate < today)
                    {
                        continue;
                    }

                    var sourceId = ResolveCandidateSourceId(candidate);
                    var dedupKey = $"{userId}:{candidate.Source}:{sourceId}:{topic}:{targetDate:yyyy-MM-dd}:{daysBefore}";
                    dueNotifications.Add(new UserNotification
                    {
                        UserId = userId,
                        Topic = topic,
                        Source = candidate.Source,
                        SourceId = sourceId,
                        DedupKey = dedupKey,
                        Title = candidate.Title,
                        Body = BuildNotificationBody(candidate.Title, targetDate, daysBefore),
                        TargetDate = ToUtcDateTime(targetDate),
                        ReminderDate = ToUtcDateTime(reminderDate),
                        ReminderDaysBefore = daysBefore,
                        Url = candidate.Url,
                        CreatedTime = now,
                    });
                }
            }

            if (dueNotifications.Count == 0)
            {
                return Results.Ok(new NotificationSyncResult(0, upcomingMatched));
            }

            var dedupKeys = dueNotifications.Select(n => n.DedupKey).Distinct().ToList();
            var existingKeys = await db.UserNotifications
                .Where(n => n.UserId == userId && dedupKeys.Contains(n.DedupKey))
                .Select(n => n.DedupKey)
                .ToListAsync(context.RequestAborted);
            var existing = existingKeys.ToHashSet(StringComparer.Ordinal);
            var newNotifications = dueNotifications
                .Where(n => !existing.Contains(n.DedupKey))
                .GroupBy(n => n.DedupKey)
                .Select(g => g.First())
                .ToList();

            var createdCount = 0;
            if (newNotifications.Count > 0)
            {
                try
                {
                    await db.UserNotifications.AddRangeAsync(newNotifications, context.RequestAborted);
                    await db.SaveChangesAsync(context.RequestAborted);
                    createdCount = newNotifications.Count;
                }
                catch (Microsoft.EntityFrameworkCore.DbUpdateException)
                {
                    db.ChangeTracker.Clear();
                    existingKeys = await db.UserNotifications
                        .Where(n => n.UserId == userId && dedupKeys.Contains(n.DedupKey))
                        .Select(n => n.DedupKey)
                        .ToListAsync(context.RequestAborted);
                    existing = existingKeys.ToHashSet(StringComparer.Ordinal);
                    newNotifications = dueNotifications
                        .Where(n => !existing.Contains(n.DedupKey))
                        .GroupBy(n => n.DedupKey)
                        .Select(g => g.First())
                        .ToList();

                    if (newNotifications.Count > 0)
                    {
                        await db.UserNotifications.AddRangeAsync(newNotifications, context.RequestAborted);
                        await db.SaveChangesAsync(context.RequestAborted);
                        createdCount = newNotifications.Count;
                    }
                }
            }

            return Results.Ok(new NotificationSyncResult(createdCount, upcomingMatched));
        });

        app.MapGet("", async (
            ServerDbContext db,
            HttpContext context,
            int limit = 50,
            bool includePast = false) =>
        {
            if (!TryGetUserId(context, out Guid userId))
            {
                return Results.Unauthorized();
            }

            var query = db.UserNotifications.Where(n => n.UserId == userId);

            // 이미 지난 마감은 기본적으로 감춘다. 어제 끝난 신청이 오늘도 알림함
            // 맨 위에 남아 있으면 지금 챙겨야 할 것을 가린다.
            if (!includePast)
            {
                var todayUtc = ToUtcDateTime(TodayKst());
                query = query.Where(n => n.TargetDate >= todayUtc);
            }

            var notifications = await query
                // 생성 시각이 아니라 마감이 임박한 순으로 — 알림함의 목적은 '무엇이 급한가'다.
                .OrderBy(n => n.TargetDate)
                .ThenByDescending(n => n.CreatedTime)
                .Take(Math.Clamp(limit, 1, 100))
                .ToListAsync(context.RequestAborted);
            return Results.Ok(notifications.Select(ToDto).ToList());
        });

        app.MapPost("/{id:guid}/read", async (ServerDbContext db, HttpContext context, Guid id) =>
        {
            if (!TryGetUserId(context, out Guid userId))
            {
                return Results.Unauthorized();
            }

            var notification = await db.UserNotifications.FirstOrDefaultAsync(
                n => n.Id == id && n.UserId == userId,
                context.RequestAborted);
            if (notification is null)
            {
                return Results.NotFound();
            }

            if (!notification.IsRead)
            {
                notification.IsRead = true;
                notification.ReadTime = DateTime.UtcNow;
                await db.SaveChangesAsync(context.RequestAborted);
            }

            return Results.Ok(ToDto(notification));
        });

        // 알림이 쌓였을 때 하나씩 누르지 않아도 되도록.
        app.MapPost("/read-all", async (ServerDbContext db, HttpContext context) =>
        {
            if (!TryGetUserId(context, out Guid userId))
            {
                return Results.Unauthorized();
            }

            var unread = await db.UserNotifications
                .Where(n => n.UserId == userId && !n.IsRead)
                .ToListAsync(context.RequestAborted);

            if (unread.Count == 0)
            {
                return Results.Ok(new { updated = 0 });
            }

            var now = DateTime.UtcNow;
            foreach (var notification in unread)
            {
                notification.IsRead = true;
                notification.ReadTime = now;
            }

            await db.SaveChangesAsync(context.RequestAborted);
            return Results.Ok(new { updated = unread.Count });
        });

        // 읽은 알림 일괄 정리. 지우지 않으면 학기 내내 쌓이기만 한다.
        app.MapDelete("/read", async (ServerDbContext db, HttpContext context) =>
        {
            if (!TryGetUserId(context, out Guid userId))
            {
                return Results.Unauthorized();
            }

            var deleted = await db.UserNotifications
                .Where(n => n.UserId == userId && n.IsRead)
                .ExecuteDeleteAsync(context.RequestAborted);
            return Results.Ok(new { deleted });
        });

        app.MapDelete("/{id:guid}", async (ServerDbContext db, HttpContext context, Guid id) =>
        {
            if (!TryGetUserId(context, out Guid userId))
            {
                return Results.Unauthorized();
            }

            var deleted = await db.UserNotifications
                .Where(n => n.Id == id && n.UserId == userId)
                .ExecuteDeleteAsync(context.RequestAborted);

            return deleted > 0 ? Results.Ok(new { deleted }) : Results.NotFound();
        });
    }

    private static bool TryGetUserId(HttpContext context, out Guid userId)
    {
        userId = Guid.Empty;
        var userIdStr = context.User.FindFirstValue(JwtRegisteredClaimNames.Sub);
        return userIdStr != null && Guid.TryParse(userIdStr, out userId);
    }

    private static async Task<List<NotificationPreference>> EnsurePreferencesAsync(
        ServerDbContext db,
        Guid userId,
        CancellationToken cancellationToken)
    {
        var preferences = await db.NotificationPreferences
            .Where(p => p.UserId == userId)
            .ToListAsync(cancellationToken);
        var existingTopics = preferences.Select(p => p.Topic).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var now = DateTime.UtcNow;
        var anyAdded = false;

        foreach (var topic in SupportedTopics)
        {
            if (existingTopics.Contains(topic))
            {
                continue;
            }

            var preference = new NotificationPreference
            {
                UserId = userId,
                Topic = topic,
                Enabled = DefaultEnabledTopics.Contains(topic),
                RemindDaysBefore = "7,1,0",
                Channel = "in_app",
                CreatedTime = now,
                UpdatedTime = now,
            };
            preferences.Add(preference);
            await db.NotificationPreferences.AddAsync(preference, cancellationToken);
            anyAdded = true;
        }

        if (anyAdded)
        {
            try
            {
                await db.SaveChangesAsync(cancellationToken);
            }
            catch (Microsoft.EntityFrameworkCore.DbUpdateException)
            {
                // Concurrent request raced to insert the same rows — discard the conflicted state and re-read.
                db.ChangeTracker.Clear();
                preferences = await db.NotificationPreferences
                    .Where(p => p.UserId == userId)
                    .ToListAsync(cancellationToken);
            }
        }

        return preferences
            .OrderBy(p => Array.IndexOf(SupportedTopics, p.Topic))
            .ToList();
    }

    private static NotificationPreferenceDto ToDto(NotificationPreference preference)
        => new(
            preference.Topic,
            preference.Enabled,
            ParseRemindDays(preference.RemindDaysBefore),
            preference.Channel
        );

    private static UserNotificationDto ToDto(UserNotification notification)
        => new(
            notification.Id,
            notification.Topic,
            TopicLabels.GetValueOrDefault(notification.Topic, notification.Topic),
            notification.Source,
            notification.SourceId,
            notification.Title,
            notification.Body,
            notification.TargetDate,
            notification.ReminderDate,
            notification.ReminderDaysBefore,
            notification.Url,
            notification.IsRead,
            notification.CreatedTime,
            notification.ReadTime
        );

    private static DeadlineDto? ToDeadline(NotificationCandidateDto candidate)
    {
        if (!TryParseDateOnly(candidate.TargetDate, out var targetDate))
        {
            return null;
        }

        var topic = InferTopic(candidate);
        return new DeadlineDto(
            candidate.Id,
            candidate.Source,
            candidate.Source.Equals("schedule", StringComparison.OrdinalIgnoreCase) ? "학사일정" : "공지",
            ResolveCandidateSourceId(candidate),
            candidate.ChunkId,
            candidate.Title,
            topic,
            TopicLabels.GetValueOrDefault(topic, topic),
            candidate.Category,
            ToUtcDateTime(targetDate),
            candidate.DDay,
            candidate.Url,
            candidate.Snippet,
            candidate.PublishedAt,
            candidate.DateSource
        );
    }

    private static async Task<List<NotificationCandidateDto>> FetchCandidatesAsync(
        IConfiguration configuration,
        IHttpClientFactory httpClientFactory,
        IMemoryCache cache,
        ILogger logger,
        int daysAhead,
        int limit,
        CancellationToken cancellationToken)
    {
        var ragUrl = configuration["RagServiceUrl"] ?? configuration["RAG_SERVICE_URL"] ?? "http://rag-service:8000";
        var today = TodayKst();
        var clampedDaysAhead = Math.Clamp(daysAhead, 1, 180);
        var clampedLimit = Math.Clamp(limit, 1, 300);
        var from = today.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        var to = today.AddDays(clampedDaysAhead).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        const string sources = "notices,schedule";
        var url = $"{ragUrl.TrimEnd('/')}/notifications/candidates?from={Uri.EscapeDataString(from)}&to={Uri.EscapeDataString(to)}&sources={sources}&limit={clampedLimit}";

        // 후보 목록은 전 사용자 공용이고 공지는 하루 4회만 갱신되므로 20분간 캐시한다.
        // 관심 주제 필터·유저별 알림 생성은 이 메서드 바깥에서 수행되므로 캐시에는 개인 데이터가 담기지 않는다.
        var cacheKey = $"notif:candidates:{from}:{to}:{sources}:{clampedLimit}";
        var candidates = await cache.GetOrCreateAsync<List<NotificationCandidateDto>>(cacheKey, async entry =>
        {
            entry.AbsoluteExpirationRelativeToNow = TimeSpan.FromMinutes(20);
            try
            {
                var client = httpClientFactory.CreateClient();
                client.Timeout = TimeSpan.FromSeconds(8); // 콜드 상태의 RAG가 페이지를 100초까지 잡아두지 않도록 명시적 제한.
                var fetched = await client.GetFromJsonAsync<List<NotificationCandidateDto>>(url, JsonOptions, cancellationToken);
                return fetched ?? [];
            }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or JsonException)
            {
                logger.LogWarning(ex, "Failed to fetch notification candidates from RAG service.");
                // 실패는 오래 캐시하지 않고 30초 뒤 재시도되게 한다.
                entry.AbsoluteExpirationRelativeToNow = TimeSpan.FromSeconds(30);
                return [];
            }
        });

        return candidates ?? [];
    }

    private static string InferTopic(NotificationCandidateDto candidate)
    {
        var rawTopic = NormalizeTopic(candidate.Topic);
        if (SupportedTopics.Contains(rawTopic, StringComparer.OrdinalIgnoreCase))
        {
            return rawTopic;
        }

        var haystack = $"{candidate.Title} {candidate.Topic} {candidate.Category} {candidate.Source}".ToLowerInvariant();
        if (haystack.Contains("장학")) return "scholarship";
        if (haystack.Contains("수강신청") || haystack.Contains("수강 신청") || haystack.Contains("수강정정") || haystack.Contains("수강 정정")) return "course_registration";
        if (haystack.Contains("등록금") || haystack.Contains("납부")) return "tuition_payment";
        if (haystack.Contains("서류") || haystack.Contains("제출") || haystack.Contains("접수") || haystack.Contains("신청")) return "document_submission";
        return "academic_schedule";
    }

    private static string NormalizeTopic(string topic)
        => topic.Trim().ToLowerInvariant().Replace("-", "_");

    private static IReadOnlyList<int> ParseRemindDays(string value)
        => value
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Select(part => int.TryParse(part, NumberStyles.Integer, CultureInfo.InvariantCulture, out var day) ? day : (int?)null)
            .Where(day => day is >= 0 and <= 30)
            .Select(day => day!.Value)
            .Distinct()
            .OrderByDescending(day => day)
            .ToList();

    private static string SerializeRemindDays(IReadOnlyList<int> days)
    {
        var normalized = days
            .Where(day => day is >= 0 and <= 30)
            .Distinct()
            .OrderByDescending(day => day)
            .ToList();
        if (normalized.Count == 0)
        {
            normalized = [7, 1, 0];
        }
        return string.Join(",", normalized);
    }

    private static bool TryParseDateOnly(string value, out DateOnly date)
        => DateOnly.TryParseExact(value, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out date);

    private static DateOnly TodayKst()
        => DateOnly.FromDateTime(DateTimeOffset.UtcNow.ToOffset(TimeSpan.FromHours(9)).DateTime);

    private static DateTime ToUtcDateTime(DateOnly date)
        => DateTime.SpecifyKind(date.ToDateTime(TimeOnly.MinValue), DateTimeKind.Utc);

    private static string ResolveCandidateSourceId(NotificationCandidateDto candidate)
        => !string.IsNullOrWhiteSpace(candidate.SourceId)
            ? candidate.SourceId!
            : !string.IsNullOrWhiteSpace(candidate.ChunkId)
                ? candidate.ChunkId!
                : candidate.Id;

    private static string BuildNotificationBody(string title, DateOnly targetDate, int daysBefore)
    {
        var dateText = targetDate.ToString("M월 d일", CultureInfo.GetCultureInfo("ko-KR"));
        return daysBefore switch
        {
            0 => $"오늘 마감입니다. {title}",
            1 => $"내일({dateText}) 마감입니다. {title}",
            _ => $"{daysBefore}일 뒤({dateText}) 마감입니다. {title}",
        };
    }
}
