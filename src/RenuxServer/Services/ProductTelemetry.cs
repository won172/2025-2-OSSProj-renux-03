using Microsoft.EntityFrameworkCore;
using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;

using RenuxServer.DbContexts;
using RenuxServer.Models;

namespace RenuxServer.Services;

public static class ProductEventTypes
{
    public const string AnswerCompleted = "answer_completed";
    public const string FeedbackSubmitted = "feedback_submitted";
    public const string SuggestionShown = "suggestion_shown";
    public const string SuggestionClicked = "suggestion_clicked";
    public const string SessionReturned = "session_returned";

    public static readonly IReadOnlySet<string> Allowed = new HashSet<string>(StringComparer.Ordinal)
    {
        AnswerCompleted,
        FeedbackSubmitted,
        SuggestionShown,
        SuggestionClicked,
        SessionReturned,
    };
}

public sealed record ProductEventContext(
    string? SubjectKey,
    string? SessionKey,
    bool IsExcluded,
    string? ExclusionReason);

public sealed record ProductEventData(
    string EventType,
    string RequestId,
    Guid? SessionId = null,
    int? Rating = null,
    int? SuggestionIndex = null,
    int? SuggestionCount = null,
    bool? IsFallback = null,
    bool? Grounded = null,
    int? SourceCount = null);

public sealed record ProductKpiRatio(int Numerator, int Denominator, double? Rate);

public sealed record ProductKpiReport(
    DateTime From,
    DateTime To,
    ProductKpiRatio HelpfulAnswerRate,
    ProductKpiRatio SevenDayValidReuseRate,
    int ExcludedEventCount,
    string[] Caveats);

public static class ProductKpiMath
{
    private static readonly TimeZoneInfo SeoulTimeZone = TimeZoneInfo.FindSystemTimeZoneById("Asia/Seoul");

    public static bool IsValidCompletion(ProductEvent productEvent)
        => productEvent.EventType == ProductEventTypes.AnswerCompleted
           && !productEvent.IsExcluded
           && productEvent.IsFallback == false
           && productEvent.Grounded == true
           && productEvent.SubjectKey != null
           && productEvent.AnswerKey != null;

    public static ProductKpiRatio HelpfulAnswerRate(IEnumerable<ProductEvent> windowEvents)
    {
        var eligible = windowEvents.Where(productEvent => !productEvent.IsExcluded).ToList();
        var validCompletions = eligible.Where(IsValidCompletion).ToList();
        var validAnswerSubjects = validCompletions
            .Select(productEvent => (productEvent.AnswerKey!, productEvent.SubjectKey!))
            .ToHashSet();
        int numerator = eligible
            .Where(productEvent => productEvent.EventType == ProductEventTypes.FeedbackSubmitted
                                   && !productEvent.IsExcluded
                                   && productEvent.Rating == 1
                                   && productEvent.AnswerKey != null
                                   && productEvent.SubjectKey != null
                                   && validAnswerSubjects.Contains((productEvent.AnswerKey, productEvent.SubjectKey)))
            .Select(productEvent => (productEvent.AnswerKey!, productEvent.SubjectKey!))
            .Distinct()
            .Count();
        return Ratio(numerator, validAnswerSubjects.Count);
    }

    public static ProductKpiRatio SevenDayValidReuseRate(
        IEnumerable<ProductEvent> allAnswerEvents,
        DateTime from,
        DateTime to)
    {
        DateTime matureCohortEnd = to.AddDays(-7);
        if (matureCohortEnd <= from) return Ratio(0, 0);

        int denominator = 0;
        int numerator = 0;
        foreach (var subjectAnswers in allAnswerEvents
                     .Where(productEvent => IsValidCompletion(productEvent) && productEvent.OccurredTime < to)
                     .GroupBy(productEvent => productEvent.SubjectKey!))
        {
            var ordered = subjectAnswers.OrderBy(productEvent => productEvent.OccurredTime).ToList();
            DateTime first = ordered[0].OccurredTime;
            if (first < from || first >= matureCohortEnd) continue;

            denominator += 1;
            DateOnly firstDate = SeoulDate(first);
            if (ordered.Skip(1).Any(productEvent =>
                    SeoulDate(productEvent.OccurredTime) > firstDate
                    && productEvent.OccurredTime <= first.AddDays(7)))
            {
                numerator += 1;
            }
        }

        return Ratio(numerator, denominator);
    }

    private static ProductKpiRatio Ratio(int numerator, int denominator)
        => new(numerator, denominator, denominator == 0 ? null : (double)numerator / denominator);

    private static DateOnly SeoulDate(DateTime value)
    {
        DateTime utc = value.Kind switch
        {
            DateTimeKind.Utc => value,
            DateTimeKind.Local => value.ToUniversalTime(),
            _ => DateTime.SpecifyKind(value, DateTimeKind.Utc),
        };
        return DateOnly.FromDateTime(TimeZoneInfo.ConvertTimeFromUtc(utc, SeoulTimeZone));
    }
}

/// <summary>
/// Records a fixed, privacy-minimal event schema. This class never accepts a
/// question, answer body, name, student number, email address or IP address.
/// </summary>
public static class ProductTelemetry
{
    private static readonly HashSet<string> ExcludedAdminRoles = new(StringComparer.OrdinalIgnoreCase)
    {
        "관리자",
        "총학생회",
        "학생회",
    };

    public static string BuildPseudonymousKey(
        IConfiguration configuration,
        string purpose,
        string rawValue)
    {
        string configuredKey = FirstNonBlank(
                configuration["Telemetry:HmacKey"],
                configuration["TELEMETRY_HMAC_KEY"],
                configuration["Jwt:Key"],
                configuration["JWT_KEY"])
            ?? throw new InvalidOperationException("A server-side telemetry/JWT key is required.");

        byte[] key;
        try
        {
            key = Convert.FromBase64String(configuredKey);
        }
        catch (FormatException)
        {
            key = Encoding.UTF8.GetBytes(configuredKey);
        }

        string keyId = configuration["Telemetry:KeyId"]
            ?? configuration["TELEMETRY_KEY_ID"]
            ?? "v1";
        keyId = new string(keyId.Where(character => char.IsAsciiLetterOrDigit(character) || character is '-' or '_').Take(16).ToArray());
        if (keyId.Length == 0) keyId = "v1";

        byte[] message = Encoding.UTF8.GetBytes($"dongttok:{purpose}:{rawValue}");
        byte[] digest = HMACSHA256.HashData(key, message);
        return $"{keyId}.{Base64UrlEncode(digest)}";
    }

    public static async Task<ProductEventContext> ResolveContextAsync(
        ServerDbContext db,
        HttpContext http,
        IConfiguration configuration,
        Guid? sessionId,
        string? validatedGuestSubjectId,
        CancellationToken cancellationToken = default)
    {
        string? subjectKey = null;
        bool isExcluded = configuration.GetValue<bool>("Telemetry:ExcludeTraffic")
            || configuration.GetValue<bool>("TELEMETRY_EXCLUDE_TRAFFIC");
        string? exclusionReason = isExcluded ? "configured_test_traffic" : null;

        var subjectClaim = http.User.FindFirstValue(JwtRegisteredClaimNames.Sub);
        if (http.User.Identity?.IsAuthenticated == true && Guid.TryParse(subjectClaim, out Guid parsedUserId))
        {
            subjectKey = BuildPseudonymousKey(configuration, "subject:user", parsedUserId.ToString("N"));

            string? roleName = await db.Users
                .Where(user => user.Id == parsedUserId)
                .Select(user => user.Role!.Rolename)
                .FirstOrDefaultAsync(cancellationToken);
            if (!string.IsNullOrWhiteSpace(roleName) && ExcludedAdminRoles.Contains(roleName))
            {
                isExcluded = true;
                exclusionReason = "admin_traffic";
            }
        }
        else if (!string.IsNullOrWhiteSpace(validatedGuestSubjectId))
        {
            subjectKey = BuildPseudonymousKey(configuration, "subject:guest", validatedGuestSubjectId);
        }

        if (MatchesConfiguredTestKey(http, configuration))
        {
            isExcluded = true;
            exclusionReason = "test_traffic";
        }

        string? sessionKey = sessionId is null
            ? null
            : BuildPseudonymousKey(configuration, "session", sessionId.Value.ToString("N"));

        return new ProductEventContext(subjectKey, sessionKey, isExcluded, exclusionReason);
    }

    public static bool IsValidEventData(ProductEventData data)
    {
        if (!ProductEventTypes.Allowed.Contains(data.EventType)
            || string.IsNullOrWhiteSpace(data.RequestId)
            || data.RequestId.Length > 200)
        {
            return false;
        }

        return data.EventType switch
        {
            ProductEventTypes.AnswerCompleted =>
                data.Rating is null
                && data.SuggestionIndex is null
                && data.SuggestionCount is >= 0 and <= 10
                && data.SourceCount is >= 0 and <= 20,
            ProductEventTypes.FeedbackSubmitted =>
                data.Rating is 1 or -1
                && data.SuggestionIndex is null
                && data.SuggestionCount is null
                && data.IsFallback is null
                && data.Grounded is null
                && data.SourceCount is null,
            ProductEventTypes.SuggestionShown =>
                data.Rating is null
                && data.SuggestionIndex is null
                && data.SuggestionCount is >= 1 and <= 10
                && data.IsFallback is null
                && data.Grounded is null
                && data.SourceCount is null,
            ProductEventTypes.SuggestionClicked =>
                data.Rating is null
                && data.SuggestionIndex is >= 0 and < 10
                && data.SuggestionCount is null
                && data.IsFallback is null
                && data.Grounded is null
                && data.SourceCount is null,
            ProductEventTypes.SessionReturned =>
                data.Rating is null
                && data.SuggestionIndex is null
                && data.SuggestionCount is null
                && data.IsFallback is null
                && data.Grounded is null
                && data.SourceCount is null,
            _ => false,
        };
    }

    public static async Task<bool> RecordAsync(
        ServerDbContext db,
        IConfiguration configuration,
        ProductEventContext eventContext,
        ProductEventData data,
        CancellationToken cancellationToken = default)
    {
        if (!IsValidEventData(data))
        {
            throw new ArgumentException("Event type or properties are not allowlisted.", nameof(data));
        }

        string answerKey = BuildPseudonymousKey(configuration, "answer", data.RequestId);
        string idempotencyRaw = data.EventType switch
        {
            ProductEventTypes.SuggestionClicked => $"{data.EventType}:{answerKey}:{data.SuggestionIndex}",
            ProductEventTypes.SessionReturned => $"{data.EventType}:{eventContext.SubjectKey}:{DateTime.UtcNow:yyyy-MM-dd}",
            _ => $"{data.EventType}:{answerKey}",
        };
        string idempotencyKey = BuildPseudonymousKey(configuration, "event", idempotencyRaw);
        DateTime occurredTime = DateTime.UtcNow;

        int inserted = await db.Database.ExecuteSqlInterpolatedAsync($"""
            INSERT INTO product_events (
                id, event_type, idempotency_key, subject_key, session_key,
                answer_key, rating, suggestion_index, suggestion_count,
                is_fallback, grounded, source_count, is_excluded,
                exclusion_reason, occurred_time)
            VALUES (
                {Guid.NewGuid()}, {data.EventType}, {idempotencyKey}, {eventContext.SubjectKey}, {eventContext.SessionKey},
                {answerKey}, {data.Rating}, {data.SuggestionIndex}, {data.SuggestionCount},
                {data.IsFallback}, {data.Grounded}, {data.SourceCount}, {eventContext.IsExcluded},
                {eventContext.ExclusionReason}, {occurredTime})
            ON CONFLICT (idempotency_key) DO NOTHING;
            """, cancellationToken);

        if (inserted > 0
            && data.EventType == ProductEventTypes.AnswerCompleted
            && !eventContext.IsExcluded
            && eventContext.SubjectKey is not null
            && data.IsFallback == false
            && data.Grounded == true)
        {
            await RecordSessionReturnIfApplicableAsync(
                db,
                configuration,
                eventContext,
                data.RequestId,
                occurredTime,
                cancellationToken);
        }

        return inserted > 0;
    }

    public static async Task<ProductKpiReport> BuildKpiReportAsync(
        ServerDbContext db,
        DateTime from,
        DateTime to,
        CancellationToken cancellationToken = default)
    {
        DateTime safeFrom = EnsureUtc(from);
        DateTime safeTo = EnsureUtc(to);
        if (safeTo <= safeFrom || safeTo - safeFrom > TimeSpan.FromDays(366))
        {
            throw new ArgumentOutOfRangeException(nameof(to), "KPI range must be between 1 minute and 366 days.");
        }

        var windowEvents = await db.ProductEvents
            .AsNoTracking()
            .Where(productEvent => productEvent.OccurredTime >= safeFrom && productEvent.OccurredTime < safeTo)
            .ToListAsync(cancellationToken);

        int excludedCount = windowEvents.Count(productEvent => productEvent.IsExcluded);
        var allEligibleAnswers = await db.ProductEvents
            .AsNoTracking()
            .Where(productEvent => productEvent.EventType == ProductEventTypes.AnswerCompleted
                                   && productEvent.OccurredTime < safeTo)
            .ToListAsync(cancellationToken);
        ProductKpiRatio helpfulRate = ProductKpiMath.HelpfulAnswerRate(windowEvents);
        ProductKpiRatio reuseRate = ProductKpiMath.SevenDayValidReuseRate(
            allEligibleAnswers,
            safeFrom,
            safeTo);

        return new ProductKpiReport(
            safeFrom,
            safeTo,
            helpfulRate,
            reuseRate,
            excludedCount,
            [
                "목표값과 기준선은 포함하지 않습니다.",
                "도움된 답변율의 분모는 기간 내 완료 답변이며, 피드백 미제출 답변도 포함합니다.",
                "7일 재사용률은 관찰기간 7일이 모두 지난 최초 사용자 코호트만 포함합니다.",
            ]);
    }

    public static async Task<bool> HasAnswerCompletionEventAsync(
        ServerDbContext db,
        IConfiguration configuration,
        ProductEventContext eventContext,
        string requestId,
        CancellationToken cancellationToken = default)
        => await FindAnswerCompletionEventAsync(
            db, configuration, eventContext, requestId, cancellationToken) is not null;

    public static async Task<ProductEvent?> FindAnswerCompletionEventAsync(
        ServerDbContext db,
        IConfiguration configuration,
        ProductEventContext eventContext,
        string requestId,
        CancellationToken cancellationToken = default)
    {
        if (eventContext.SubjectKey is null) return null;
        string answerKey = BuildPseudonymousKey(configuration, "answer", requestId);
        return await db.ProductEvents.AsNoTracking().SingleOrDefaultAsync(
            productEvent => productEvent.EventType == ProductEventTypes.AnswerCompleted
                            && productEvent.AnswerKey == answerKey
                            && productEvent.SubjectKey == eventContext.SubjectKey,
            cancellationToken);
    }

    public static async Task<bool> UpdateAnswerSuggestionCountAsync(
        ServerDbContext db,
        IConfiguration configuration,
        ProductEventContext eventContext,
        string requestId,
        int suggestionCount,
        CancellationToken cancellationToken = default)
    {
        if (eventContext.SubjectKey is null || suggestionCount is < 0 or > 10)
        {
            return false;
        }
        string answerKey = BuildPseudonymousKey(configuration, "answer", requestId);
        ProductEvent? completion = await db.ProductEvents.SingleOrDefaultAsync(
            productEvent => productEvent.EventType == ProductEventTypes.AnswerCompleted
                            && productEvent.AnswerKey == answerKey
                            && productEvent.SubjectKey == eventContext.SubjectKey,
            cancellationToken);
        if (completion is null) return false;
        completion.SuggestionCount = suggestionCount;
        await db.SaveChangesAsync(cancellationToken);
        return true;
    }

    public static async Task<int?> FindFeedbackRatingAsync(
        ServerDbContext db,
        IConfiguration configuration,
        ProductEventContext eventContext,
        string requestId,
        CancellationToken cancellationToken = default)
    {
        if (eventContext.SubjectKey is null) return null;
        string answerKey = BuildPseudonymousKey(configuration, "answer", requestId);
        return await db.ProductEvents.AsNoTracking()
            .Where(productEvent => productEvent.EventType == ProductEventTypes.FeedbackSubmitted
                                   && productEvent.AnswerKey == answerKey
                                   && productEvent.SubjectKey == eventContext.SubjectKey)
            .Select(productEvent => productEvent.Rating)
            .SingleOrDefaultAsync(cancellationToken);
    }

    private static async Task RecordSessionReturnIfApplicableAsync(
        ServerDbContext db,
        IConfiguration configuration,
        ProductEventContext eventContext,
        string requestId,
        DateTime occurredTime,
        CancellationToken cancellationToken)
    {
        DateTime today = occurredTime.Date;
        DateTime sevenDaysAgo = today.AddDays(-7);
        bool returned = await db.ProductEvents.AnyAsync(
            productEvent => productEvent.EventType == ProductEventTypes.AnswerCompleted
                            && !productEvent.IsExcluded
                            && productEvent.IsFallback == false
                            && productEvent.Grounded == true
                            && productEvent.SubjectKey == eventContext.SubjectKey
                            && productEvent.OccurredTime >= sevenDaysAgo
                            && productEvent.OccurredTime < today,
            cancellationToken);
        if (!returned) return;

        var returnData = new ProductEventData(ProductEventTypes.SessionReturned, requestId);
        await RecordAsync(db, configuration, eventContext, returnData, cancellationToken);
    }

    private static bool MatchesConfiguredTestKey(HttpContext http, IConfiguration configuration)
    {
        string? expected = configuration["Telemetry:TestKey"] ?? configuration["TELEMETRY_TEST_KEY"];
        string? actual = http.Request.Headers["X-Telemetry-Test-Key"].FirstOrDefault();
        if (string.IsNullOrWhiteSpace(expected) || string.IsNullOrWhiteSpace(actual)) return false;

        byte[] expectedBytes = SHA256.HashData(Encoding.UTF8.GetBytes(expected));
        byte[] actualBytes = SHA256.HashData(Encoding.UTF8.GetBytes(actual));
        return CryptographicOperations.FixedTimeEquals(expectedBytes, actualBytes);
    }

    private static DateTime EnsureUtc(DateTime value) => value.Kind switch
    {
        DateTimeKind.Utc => value,
        DateTimeKind.Local => value.ToUniversalTime(),
        _ => DateTime.SpecifyKind(value, DateTimeKind.Utc),
    };

    private static string Base64UrlEncode(byte[] value)
        => Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    private static string? FirstNonBlank(params string?[] values)
        => values.FirstOrDefault(value => !string.IsNullOrWhiteSpace(value));
}
