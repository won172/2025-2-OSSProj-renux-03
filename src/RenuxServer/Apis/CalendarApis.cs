using Microsoft.Extensions.Caching.Memory;
using System.Globalization;
using System.Text.Json;

namespace RenuxServer.Apis;

public record CalendarEventDto(
    string Id,
    string Source,
    string Kind,
    string Title,
    string? StartDate,
    string EndDate,
    string? Url,
    string? Snippet,
    string? Evidence,
    string DateStatus,
    string? Department,
    string? PublishedAt
);

public record CalendarEventsResponse(
    string From,
    string To,
    DateTimeOffset GeneratedAt,
    IReadOnlyList<CalendarEventDto> Events
);

public record CalendarErrorResponse(string Error, string Message);

/// <summary>엔드포인트와 계약 테스트가 함께 쓰는 처리 결과. Body는 응답 JSON 본문이다.</summary>
public sealed record CalendarEventsOutcome(int StatusCode, object Body);

/// <summary>
/// 학사일정·공지 마감을 기간 단위로 보여 주는 읽기 전용 달력 API.
/// 알림 설정과 무관하게 공식 후보를 그대로 노출하고, 범위(학과·WISE)는 알림 경로와 같다.
/// </summary>
static public class CalendarApis
{
    /// <summary>한 번에 조회할 수 있는 최대 기간(to - from, 일).</summary>
    internal const int MaxSpanDays = 92;

    /// <summary>
    /// RAG는 마감일(end/target) 기준으로 후보를 거르므로, from 이전에 시작해 기간 안에 걸치는
    /// 긴 일정(예: 겨울 방학 12-22~02-28)을 놓치지 않도록 to 뒤로 이만큼 더 조회한다.
    /// </summary>
    internal const int LookaheadDays = 92;

    /// <summary>RAG 후보 limit 상한. 이만큼 돌아오면 잘렸을 수 있다.</summary>
    internal const int RagCandidateLimit = 300;

    private const string ConfirmedStatus = "confirmed";
    private const string UnverifiedStatus = "unverified";

    static public void AddCalendarApis(this WebApplication application)
    {
        var app = application.MapGroup("/calendar");

        app.MapMethods("/{**path}", new[] { "OPTIONS" }, () => Results.Ok()).AllowAnonymous();

        app.MapGet("/events", async (
            HttpContext context,
            IConfiguration configuration,
            IHttpClientFactory httpClientFactory,
            IMemoryCache cache,
            ILogger<Program> logger,
            string? from,
            string? to) =>
        {
            var outcome = await GetEventsAsync(
                context, configuration, httpClientFactory, cache, logger, from, to, context.RequestAborted);
            return Results.Json(outcome.Body, statusCode: outcome.StatusCode);
        }).AllowAnonymous();
    }

    internal static NotificationCandidateScope ResolveScope(HttpContext context)
        => context.User.Identity?.IsAuthenticated == true
            ? NotificationApis.ResolveCandidateScope(context)
            : NotificationCandidateScope.Public;

    internal static async Task<CalendarEventsOutcome> GetEventsAsync(
        HttpContext context,
        IConfiguration configuration,
        IHttpClientFactory httpClientFactory,
        IMemoryCache cache,
        ILogger logger,
        string? from,
        string? to,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(from) || string.IsNullOrWhiteSpace(to))
        {
            return BadRequest("from과 to는 필수입니다(YYYY-MM-DD).");
        }

        if (!NotificationApis.TryParseDateOnly(from.Trim(), out var fromDate)
            || !NotificationApis.TryParseDateOnly(to.Trim(), out var toDate))
        {
            return BadRequest("from과 to는 YYYY-MM-DD 형식이어야 합니다.");
        }

        if (toDate < fromDate)
        {
            return BadRequest("to는 from과 같거나 이후여야 합니다.");
        }

        if (toDate.DayNumber - fromDate.DayNumber > MaxSpanDays)
        {
            return BadRequest($"조회 기간은 최대 {MaxSpanDays}일입니다.");
        }

        var scope = ResolveScope(context);
        var fetch = await NotificationApis.FetchCandidateRangeAsync(
            configuration,
            httpClientFactory,
            cache,
            logger,
            scope,
            fromDate,
            toDate.AddDays(LookaheadDays),
            RagCandidateLimit,
            cancellationToken);

        if (!fetch.Succeeded)
        {
            // 빈 목록을 성공으로 돌려주면 "일정 없음"으로 오해된다.
            return new CalendarEventsOutcome(
                StatusCodes.Status503ServiceUnavailable,
                new CalendarErrorResponse("calendar_unavailable", "일정 정보를 일시적으로 불러올 수 없습니다."));
        }

        if (fetch.Candidates.Count >= RagCandidateLimit)
        {
            logger.LogWarning(
                "Calendar candidates hit the RAG limit ({Limit}) for {From}..{To} (query to {QueryTo}); results may be truncated.",
                RagCandidateLimit,
                fromDate,
                toDate,
                toDate.AddDays(LookaheadDays));
        }

        var events = BuildEvents(fetch.Candidates, fromDate, toDate);
        return new CalendarEventsOutcome(
            StatusCodes.Status200OK,
            new CalendarEventsResponse(
                FormatDate(fromDate),
                FormatDate(toDate),
                DateTimeOffset.UtcNow,
                events));
    }

    /// <summary>
    /// [from, to]와 겹치는 후보만 남긴다: (startDate ?? endDate) &lt;= to AND endDate &gt;= from.
    /// id로 중복을 제거하고 (startDate ?? endDate, title) 순으로 정렬한다.
    /// </summary>
    internal static List<CalendarEventDto> BuildEvents(
        IEnumerable<NotificationCandidateDto> candidates,
        DateOnly from,
        DateOnly to)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var items = new List<(DateOnly SortDate, CalendarEventDto Event)>();

        foreach (var candidate in candidates)
        {
            var mapped = ToEvent(candidate);
            if (mapped is null)
            {
                continue;
            }

            var (start, end, calendarEvent) = mapped.Value;
            var effectiveStart = start ?? end;
            if (effectiveStart > to || end < from)
            {
                continue;
            }

            if (!seen.Add(calendarEvent.Id))
            {
                continue;
            }

            items.Add((effectiveStart, calendarEvent));
        }

        return items
            .OrderBy(item => item.SortDate)
            .ThenBy(item => item.Event.Title, StringComparer.Ordinal)
            .ThenBy(item => item.Event.Id, StringComparer.Ordinal)
            .Select(item => item.Event)
            .ToList();
    }

    private static (DateOnly? Start, DateOnly End, CalendarEventDto Event)? ToEvent(NotificationCandidateDto candidate)
    {
        if (string.IsNullOrWhiteSpace(candidate.Id))
        {
            return null;
        }

        var source = (candidate.Source ?? "").Trim().ToLowerInvariant();
        string kind;
        if (source == "schedule")
        {
            kind = (candidate.Title ?? "").Contains("시험", StringComparison.Ordinal) ? "exam" : "academic";
        }
        else if (source == "notices")
        {
            kind = "notice";
        }
        else
        {
            return null;
        }

        DateOnly end;
        if (!NotificationApis.TryParseDateOnly(candidate.EndDate, out end)
            && !NotificationApis.TryParseDateOnly(candidate.TargetDate, out end))
        {
            return null;
        }

        DateOnly? start = NotificationApis.TryParseDateOnly(candidate.StartDate, out var parsedStart)
            ? parsedStart
            : null;

        var calendarEvent = new CalendarEventDto(
            candidate.Id,
            source,
            kind,
            candidate.Title ?? "",
            start is null ? null : FormatDate(start.Value),
            FormatDate(end),
            NullIfBlank(candidate.Url),
            NullIfBlank(candidate.Snippet),
            ResolveEvidence(candidate),
            ResolveDateStatus(candidate),
            NullIfBlank(candidate.Metadata?.GetValueOrDefault("department")),
            NullIfBlank(candidate.PublishedAt));

        return (start, end, calendarEvent);
    }

    private static string? ResolveEvidence(NotificationCandidateDto candidate)
        => ElementToText(candidate.Evidence)
            ?? NullIfBlank(candidate.Metadata?.GetValueOrDefault("evidence"));

    /// <summary>
    /// RAG의 date_status를 그대로 전달하되, 계약 밖의 값은 확인된 것으로 취급하지 않고
    /// "unverified"로 낮춘다. 값이 없으면 기존 후보와 같이 "confirmed"다.
    /// </summary>
    private static string ResolveDateStatus(NotificationCandidateDto candidate)
    {
        var raw = ElementToText(candidate.DateStatus)
            ?? NullIfBlank(candidate.Metadata?.GetValueOrDefault("date_status"));
        if (raw is null)
        {
            return ConfirmedStatus;
        }

        var normalized = raw.Trim().ToLowerInvariant();
        return normalized is ConfirmedStatus ? ConfirmedStatus : UnverifiedStatus;
    }

    private static string? ElementToText(JsonElement? element)
    {
        if (element is not { } value)
        {
            return null;
        }

        return value.ValueKind switch
        {
            JsonValueKind.String => NullIfBlank(value.GetString()),
            JsonValueKind.Array => NullIfBlank(string.Join("\n", value.EnumerateArray()
                .Where(item => item.ValueKind == JsonValueKind.String)
                .Select(item => item.GetString())
                .Where(text => !string.IsNullOrWhiteSpace(text)))),
            _ => null,
        };
    }

    private static string? NullIfBlank(string? value)
        => string.IsNullOrWhiteSpace(value) ? null : value;

    private static string FormatDate(DateOnly date)
        => date.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

    private static CalendarEventsOutcome BadRequest(string message)
        => new(StatusCodes.Status400BadRequest, new CalendarErrorResponse("invalid_range", message));
}
