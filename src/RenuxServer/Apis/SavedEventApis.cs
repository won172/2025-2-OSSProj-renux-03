using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Caching.Memory;
using System.Globalization;
using System.Text.RegularExpressions;

using RenuxServer.DbContexts;
using RenuxServer.Models;

namespace RenuxServer.Apis;

public record SavedEventReminderDto(bool Enabled, IReadOnlyList<int> DaysBefore);

public record SavedEventDto(
    string EventId,
    string Source,
    string Kind,
    string Title,
    string? StartDate,
    string EndDate,
    string? Url,
    SavedEventReminderDto Reminder,
    DateTime CreatedTime,
    DateTime UpdatedTime
);

/// <summary>
/// 일정 저장(없으면 만들고, 있으면 알림 설정만 바꾼다).
/// EndDate는 처음 저장할 때 RAG에서 그 일정을 찾을 날짜(캘린더 응답의 endDate)다.
/// </summary>
public record SaveEventRequest(string? EventId, string? EndDate, SavedEventReminderDto? Reminder);

/// <summary>엔드포인트와 계약 테스트가 함께 쓰는 처리 결과. Body는 응답 JSON 본문이다.</summary>
public sealed record SavedEventOutcome(int StatusCode, object? Body);

/// <summary>
/// 캘린더 일정 저장과 저장한 일정의 앱 안 알림.
/// 저장할 때 서버가 RAG 후보에서 그 일정을 다시 찾아, 사용자가 볼 수 있는 공식 일정만 저장하고
/// 제목·기간도 후보 값으로 남긴다(클라이언트가 보낸 제목·날짜를 믿지 않는다).
/// 날짜를 확실히 읽지 못한(unverified) 일정은 알림이 틀린 날짜로 갈 수 있어 저장하지 않는다.
/// </summary>
static public class SavedEventApis
{
    internal const string Topic = "saved_event";
    internal const string TopicLabel = "저장한 일정";

    /// <summary>한 사용자가 저장할 수 있는 일정 수.</summary>
    internal const int MaxSavedPerUser = 200;

    /// <summary>화면이 고를 수 있는 알림 시점: 7일 전, 1일 전, 당일.</summary>
    internal static readonly int[] AllowedDaysBefore = [7, 1, 0];

    internal static readonly IReadOnlyList<int> DefaultDaysBefore = [7, 1];

    private static readonly Regex EventIdPattern = new(@"^(schedule|notices):\S{1,180}$", RegexOptions.CultureInvariant);

    static public void AddSavedEventApis(this WebApplication application)
    {
        var app = application.MapGroup("/calendar/saved").RequireAuthorization();

        app.MapGet("", async (ServerDbContext db, HttpContext context) =>
        {
            if (!TryGetUserId(context, out var userId))
            {
                return Results.Unauthorized();
            }

            var saved = await db.SavedEvents
                .Where(e => e.UserId == userId)
                .OrderBy(e => e.EndDate)
                .ThenBy(e => e.Title)
                .ToListAsync(context.RequestAborted);
            return Results.Ok(saved.Select(ToDto).ToList());
        });

        app.MapPut("", async (
            ServerDbContext db,
            HttpContext context,
            IConfiguration configuration,
            IHttpClientFactory httpClientFactory,
            IMemoryCache cache,
            ILogger<Program> logger,
            SaveEventRequest request) =>
        {
            if (!TryGetUserId(context, out var userId))
            {
                return Results.Unauthorized();
            }

            var outcome = await SaveAsync(
                db, userId, request,
                (endDate, cancellationToken) => NotificationApis.FetchCandidateRangeAsync(
                    configuration, httpClientFactory, cache, logger,
                    CalendarApis.ResolveScope(context), endDate, endDate, CalendarApis.RagCandidateLimit, cancellationToken),
                logger,
                context.RequestAborted);
            return Results.Json(outcome.Body, statusCode: outcome.StatusCode);
        });

        app.MapDelete("", async (ServerDbContext db, HttpContext context, string? eventId) =>
        {
            if (!TryGetUserId(context, out var userId))
            {
                return Results.Unauthorized();
            }

            if (string.IsNullOrWhiteSpace(eventId) || !EventIdPattern.IsMatch(eventId.Trim()))
            {
                return Results.BadRequest(new CalendarErrorResponse("invalid_event", "일정 id가 올바르지 않습니다."));
            }

            var id = eventId.Trim();
            await db.SavedEvents
                .Where(e => e.UserId == userId && e.EventId == id)
                .ExecuteDeleteAsync(context.RequestAborted);
            return Results.NoContent();
        });
    }

    internal static async Task<SavedEventOutcome> SaveAsync(
        ServerDbContext db,
        Guid userId,
        SaveEventRequest request,
        Func<DateOnly, CancellationToken, Task<NotificationCandidateFetchResult>> fetchCandidates,
        ILogger logger,
        CancellationToken cancellationToken)
    {
        var eventId = request.EventId?.Trim();
        if (string.IsNullOrEmpty(eventId) || !EventIdPattern.IsMatch(eventId))
        {
            return Error(StatusCodes.Status400BadRequest, "invalid_event", "일정 id가 올바르지 않습니다.");
        }

        if (!TryNormalizeReminder(request.Reminder, out var reminder))
        {
            return Error(StatusCodes.Status400BadRequest, "invalid_reminder", "알림은 7일 전·1일 전·당일 중에서 고를 수 있습니다.");
        }

        var now = DateTime.UtcNow;
        var existing = await db.SavedEvents
            .FirstOrDefaultAsync(e => e.UserId == userId && e.EventId == eventId, cancellationToken);
        if (existing is not null)
        {
            // 이미 저장한 일정은 알림 설정만 바꾼다(RAG를 다시 묻지 않는다).
            existing.RemindEnabled = reminder.Enabled;
            existing.RemindDaysBefore = SerializeDays(reminder.DaysBefore);
            existing.UpdatedTime = now;
            await db.SaveChangesAsync(cancellationToken);
            return new SavedEventOutcome(StatusCodes.Status200OK, ToDto(existing));
        }

        if (!NotificationApis.TryParseDateOnly(request.EndDate?.Trim(), out var endDate))
        {
            return Error(StatusCodes.Status400BadRequest, "invalid_date", "endDate는 YYYY-MM-DD 형식이어야 합니다.");
        }

        var today = NotificationApis.TodayKst();
        if (endDate < today.AddYears(-CalendarApis.MaxYearsFromToday) || endDate > today.AddYears(CalendarApis.MaxYearsFromToday))
        {
            return Error(StatusCodes.Status400BadRequest, "invalid_date", $"오늘 기준 앞뒤 {CalendarApis.MaxYearsFromToday}년 이내의 일정만 저장할 수 있습니다.");
        }

        var count = await db.SavedEvents.CountAsync(e => e.UserId == userId, cancellationToken);
        if (count >= MaxSavedPerUser)
        {
            return Error(StatusCodes.Status409Conflict, "too_many_saved", $"일정은 {MaxSavedPerUser}개까지 저장할 수 있습니다.");
        }

        var fetch = await fetchCandidates(endDate, cancellationToken);
        if (!fetch.Succeeded)
        {
            return Error(StatusCodes.Status503ServiceUnavailable, "calendar_unavailable", "일정 정보를 일시적으로 불러올 수 없습니다.");
        }

        var candidate = fetch.Candidates.FirstOrDefault(item => string.Equals(item.Id, eventId, StringComparison.Ordinal));
        var mapped = candidate is null ? null : CalendarApis.ToEvent(candidate, logger);
        if (candidate is null || mapped is null)
        {
            return Error(StatusCodes.Status404NotFound, "event_not_found", "이 일정을 찾을 수 없습니다. 캘린더를 새로고침해 주세요.");
        }

        var (start, end, calendarEvent) = mapped.Value;
        if (calendarEvent.DateStatus != "confirmed")
        {
            return Error(StatusCodes.Status409Conflict, "date_unverified", "날짜를 확실히 읽지 못한 일정이라 저장할 수 없습니다. 원문에서 날짜를 확인해 주세요.");
        }

        var saved = new SavedEvent
        {
            UserId = userId,
            EventId = eventId,
            Source = calendarEvent.Source,
            SourceId = NotificationApis.ResolveCandidateSourceId(candidate),
            Kind = calendarEvent.Kind,
            Title = calendarEvent.Title,
            StartDate = start is null ? null : NotificationApis.ToUtcDateTime(start.Value),
            EndDate = NotificationApis.ToUtcDateTime(end),
            Url = calendarEvent.Url,
            RemindEnabled = reminder.Enabled,
            RemindDaysBefore = SerializeDays(reminder.DaysBefore),
            CreatedTime = now,
            UpdatedTime = now,
        };

        try
        {
            await db.SavedEvents.AddAsync(saved, cancellationToken);
            await db.SaveChangesAsync(cancellationToken);
        }
        catch (DbUpdateException)
        {
            // 같은 일정을 동시에 두 번 저장한 경우: 이미 저장된 행을 돌려준다.
            db.ChangeTracker.Clear();
            var raced = await db.SavedEvents
                .FirstOrDefaultAsync(e => e.UserId == userId && e.EventId == eventId, cancellationToken);
            if (raced is null)
            {
                throw;
            }

            return new SavedEventOutcome(StatusCodes.Status200OK, ToDto(raced));
        }

        return new SavedEventOutcome(StatusCodes.Status201Created, ToDto(saved));
    }

    /// <summary>
    /// 알림 동기화 때 받은 후보로 저장한 일정의 제목·기간·링크를 최신으로 맞춘다.
    /// 날짜를 확실히 읽은 후보일 때만 바꾼다. 하나라도 바뀌면 true.
    /// </summary>
    internal static bool RefreshSnapshots(
        IEnumerable<SavedEvent> savedEvents,
        IEnumerable<NotificationCandidateDto> candidates,
        DateTime now,
        ILogger? logger = null)
    {
        var byId = new Dictionary<string, NotificationCandidateDto>(StringComparer.Ordinal);
        foreach (var candidate in candidates)
        {
            if (!string.IsNullOrWhiteSpace(candidate.Id))
            {
                byId.TryAdd(candidate.Id, candidate);
            }
        }

        var changed = false;
        foreach (var saved in savedEvents)
        {
            if (!byId.TryGetValue(saved.EventId, out var candidate)
                || CalendarApis.ToEvent(candidate, logger) is not { } mapped
                || mapped.Event.DateStatus != "confirmed")
            {
                continue;
            }

            DateTime? startDate = mapped.Start is null ? null : NotificationApis.ToUtcDateTime(mapped.Start.Value);
            var endDate = NotificationApis.ToUtcDateTime(mapped.End);
            if (saved.Title == mapped.Event.Title && saved.StartDate == startDate && saved.EndDate == endDate && saved.Url == mapped.Event.Url)
            {
                continue;
            }

            saved.Title = mapped.Event.Title;
            saved.StartDate = startDate;
            saved.EndDate = endDate;
            saved.Url = mapped.Event.Url;
            saved.UpdatedTime = now;
            changed = true;
        }

        return changed;
    }

    /// <summary>
    /// 오늘 보내야 할 저장한 일정 알림.
    /// 기간 일정은 시작일까지(시작 당일 포함), 시작 다음 날부터는 마감일까지 센다. 하루짜리 일정·마감만 있는
    /// 공지는 마감일까지 센다. 기준일마다 알림 날짜가 지난 단계 중 가장 임박한 하나만 만들고, 문구는 실제로
    /// 남은 날로 쓴다(늦게 저장해 '7일 전' 단계가 이미 지났을 때 "7일 뒤"라고 쓰지 않는다).
    /// </summary>
    internal static List<UserNotification> BuildDueNotifications(SavedEvent saved, DateOnly today, DateTime now)
    {
        var due = new List<UserNotification>();
        if (!saved.RemindEnabled)
        {
            return due;
        }

        var end = DateOnly.FromDateTime(saved.EndDate);
        DateOnly? start = saved.StartDate is { } value ? DateOnly.FromDateTime(value) : null;
        var days = ParseDays(saved.RemindDaysBefore);

        if (start is { } startDate && startDate < end)
        {
            if (startDate >= today)
            {
                AddMostImminent(startDate, isStart: true);
            }
            else
            {
                AddMostImminent(end, isStart: false);
            }
        }
        else
        {
            AddMostImminent(end, isStart: false);
        }

        return due;

        void AddMostImminent(DateOnly anchor, bool isStart)
        {
            if (anchor < today)
            {
                return;
            }

            // ParseDays는 큰 값부터 정렬된다. 알림 날짜가 지난 단계 중 마지막(가장 작은 값)이 가장 임박하다.
            int? step = null;
            foreach (var daysBefore in days)
            {
                if (anchor.AddDays(-daysBefore) <= today)
                {
                    step = daysBefore;
                }
            }

            if (step is not { } daysBeforeStep)
            {
                return;
            }

            var anchorKind = isStart ? "start" : "end";
            due.Add(new UserNotification
            {
                UserId = saved.UserId,
                Topic = Topic,
                Source = saved.Source,
                SourceId = saved.SourceId,
                DedupKey = $"{saved.UserId}:{Topic}:{saved.EventId}:{anchorKind}:{anchor:yyyy-MM-dd}:{daysBeforeStep}",
                Title = saved.Title,
                Body = BuildBody(saved.Title, anchor, anchor.DayNumber - today.DayNumber, isStart),
                TargetDate = NotificationApis.ToUtcDateTime(anchor),
                ReminderDate = NotificationApis.ToUtcDateTime(anchor.AddDays(-daysBeforeStep)),
                ReminderDaysBefore = daysBeforeStep,
                Url = saved.Url,
                CreatedTime = now,
            });
        }
    }

    /// <summary>알림 문구. daysLeft는 알림을 만드는 날 기준으로 기준일까지 남은 날이다.</summary>
    internal static string BuildBody(string title, DateOnly anchor, int daysLeft, bool isStart)
    {
        var dateText = anchor.ToString("M월 d일", CultureInfo.GetCultureInfo("ko-KR"));
        var verb = isStart ? "시작합니다" : "마감입니다";
        return daysLeft switch
        {
            <= 0 => $"오늘 {verb}. {title}",
            1 => $"내일({dateText}) {verb}. {title}",
            _ => $"{daysLeft}일 뒤({dateText}) {verb}. {title}",
        };
    }

    internal static SavedEventDto ToDto(SavedEvent saved)
        => new(
            saved.EventId,
            saved.Source,
            saved.Kind,
            saved.Title,
            saved.StartDate is { } start ? FormatDate(start) : null,
            FormatDate(saved.EndDate),
            saved.Url,
            new SavedEventReminderDto(saved.RemindEnabled, ParseDays(saved.RemindDaysBefore)),
            saved.CreatedTime,
            saved.UpdatedTime);

    /// <summary>알림 설정이 없으면 기본값(켜짐, 7일 전·1일 전)이다. 허용하지 않는 날짜가 하나라도 있으면 거부한다.</summary>
    internal static bool TryNormalizeReminder(SavedEventReminderDto? requested, out SavedEventReminderDto reminder)
    {
        if (requested is null)
        {
            reminder = new SavedEventReminderDto(true, DefaultDaysBefore);
            return true;
        }

        var days = requested.DaysBefore ?? [];
        if (days.Any(day => !AllowedDaysBefore.Contains(day)))
        {
            reminder = new SavedEventReminderDto(false, []);
            return false;
        }

        var normalized = days.Distinct().OrderByDescending(day => day).ToList();
        // 켜 두었는데 고른 시점이 없으면 알림이 하나도 가지 않으므로 꺼진 것으로 저장한다.
        reminder = new SavedEventReminderDto(requested.Enabled && normalized.Count > 0, normalized);
        return true;
    }

    internal static IReadOnlyList<int> ParseDays(string value)
        => value
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Select(part => int.TryParse(part, NumberStyles.Integer, CultureInfo.InvariantCulture, out var day) ? day : (int?)null)
            .Where(day => day is { } d && AllowedDaysBefore.Contains(d))
            .Select(day => day!.Value)
            .Distinct()
            .OrderByDescending(day => day)
            .ToList();

    private static string SerializeDays(IReadOnlyList<int> days) => string.Join(",", days);

    private static string FormatDate(DateTime value) => value.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

    private static SavedEventOutcome Error(int statusCode, string error, string message)
        => new(statusCode, new CalendarErrorResponse(error, message));

    private static bool TryGetUserId(HttpContext context, out Guid userId)
        => NotificationApis.TryGetUserId(context, out userId);
}
