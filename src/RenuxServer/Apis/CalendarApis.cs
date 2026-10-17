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
    /// 설계상 한계: to 이후 92일보다 더 늦게 끝나는 일정은 기간과 겹쳐도 누락될 수 있다.
    /// </summary>
    internal const int LookaheadDays = 92;

    /// <summary>RAG 후보 limit 상한. 이만큼 돌아오면 잘렸을 수 있다.</summary>
    internal const int RagCandidateLimit = 300;

    /// <summary>
    /// from/to가 놓일 수 있는 범위: 오늘(KST) ± 이 햇수. 익명 엔드포인트의 캐시 키 수를 묶고,
    /// to + LookaheadDays 계산이 DateOnly 범위를 넘지 않게 한다.
    /// </summary>
    internal const int MaxYearsFromToday = 2;

    /// <summary>익명 호출을 허용하므로 IP 단위 레이트 리밋을 건다(Program.cs의 정책 이름).</summary>
    internal const string RateLimitPolicy = "calendar";

    /// <summary>
    /// 신청·접수 성격의 학사일정. "종합시험 접수"처럼 "시험"이 들어가도 시험 기간이 아니다.
    /// </summary>
    private static readonly string[] RegistrationKeywords = ["접수", "신청", "등록", "응시원서"];

    /// <summary>실제 시험 기간을 나타내는 표현. 신청·접수 키워드가 없을 때만 exam으로 본다.</summary>
    private static readonly string[] ExamKeywords = ["중간고사", "기말고사", "시험"];

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
        }).AllowAnonymous().RequireRateLimiting(RateLimitPolicy);
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
        CancellationToken cancellationToken,
        DateOnly? today = null)
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

        // 오늘(KST) ± MaxYearsFromToday 밖은 거부한다. 이 검사 덕분에 아래 to + LookaheadDays가
        // DateOnly.MaxValue를 넘지 않는다(예: 9999-12-31 입력이 500이 되지 않음).
        var anchor = today ?? NotificationApis.TodayKst();
        if (fromDate < anchor.AddYears(-MaxYearsFromToday) || toDate > anchor.AddYears(MaxYearsFromToday))
        {
            return BadRequest($"조회 기간은 오늘 기준 앞뒤 {MaxYearsFromToday}년 이내여야 합니다.");
        }

        var queryTo = toDate.AddDays(LookaheadDays);

        var scope = ResolveScope(context);
        var fetch = await NotificationApis.FetchCandidateRangeAsync(
            configuration,
            httpClientFactory,
            cache,
            logger,
            scope,
            fromDate,
            queryTo,
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
                queryTo);
        }

        var events = BuildEvents(fetch.Candidates, fromDate, toDate, logger);
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
        DateOnly to,
        ILogger? logger = null)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var items = new List<(DateOnly SortDate, CalendarEventDto Event)>();

        foreach (var candidate in candidates)
        {
            var mapped = ToEvent(candidate, logger);
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

    internal static (DateOnly? Start, DateOnly End, CalendarEventDto Event)? ToEvent(
        NotificationCandidateDto candidate,
        ILogger? logger)
    {
        if (string.IsNullOrWhiteSpace(candidate.Id))
        {
            return null;
        }

        var source = (candidate.Source ?? "").Trim().ToLowerInvariant();
        string kind;
        if (source == "schedule")
        {
            kind = ResolveScheduleKind(candidate.Title);
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

        if (start is { } startValue && startValue > end)
        {
            // 뒤바뀐 기간은 신뢰할 수 없으므로 종료일 하루짜리 일정으로 다룬다. 후보 id만 남긴다(개인정보 없음).
            logger?.LogWarning(
                "Calendar candidate {CandidateId} has start_date after end_date; treating it as a single-day event.",
                candidate.Id);
            start = null;
        }

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

    /// <summary>
    /// 학사일정의 kind. 신청·접수 성격이면 academic, 그 밖에 시험 기간 표현이 있으면 exam이다.
    /// </summary>
    internal static string ResolveScheduleKind(string? title)
    {
        var text = title ?? "";
        if (RegistrationKeywords.Any(keyword => text.Contains(keyword, StringComparison.Ordinal)))
        {
            return "academic";
        }

        return ExamKeywords.Any(keyword => text.Contains(keyword, StringComparison.Ordinal)) ? "exam" : "academic";
    }

    private static string? ResolveEvidence(NotificationCandidateDto candidate)
        => ElementToText(candidate.Evidence)
            ?? NullIfBlank(candidate.Metadata?.GetValueOrDefault("evidence"));

    /// <summary>
    /// RAG의 date_status를 그대로 전달하되, 정확히 "confirmed"가 아닌 값(대소문자 차이·공백 포함)은
    /// 확인된 것으로 취급하지 않고 "unverified"로 낮춘다. 값이 없으면 기존 후보와 같이 "confirmed"다.
    /// </summary>
    private static string ResolveDateStatus(NotificationCandidateDto candidate)
    {
        var raw = ElementToText(candidate.DateStatus)
            ?? NullIfBlank(candidate.Metadata?.GetValueOrDefault("date_status"));
        if (raw is null)
        {
            return ConfirmedStatus;
        }

        return string.Equals(raw, ConfirmedStatus, StringComparison.Ordinal) ? ConfirmedStatus : UnverifiedStatus;
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
