using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging.Abstractions;
using RenuxServer.Apis;
using RenuxServer.DbContexts;
using RenuxServer.Models;

internal static class SavedEventsTests
{
    private static readonly Guid UserId = Guid.Parse("00000000-0000-0000-0000-0000000000a1");

    public static async Task RunAsync(Action<bool, string> check)
    {
        CheckReminderNormalization(check);
        CheckDueNotifications(check);
        CheckSnapshotRefresh(check);
        await CheckInputValidationAsync(check);

        var connection = Environment.GetEnvironmentVariable("DONGTTOK_TEST_CONNECTION");
        if (!string.IsNullOrWhiteSpace(connection))
        {
            await CheckSaveFlowAgainstDatabaseAsync(check, connection);
        }
    }

    private static void CheckReminderNormalization(Action<bool, string> check)
    {
        check(SavedEventApis.TryNormalizeReminder(null, out var defaults)
              && defaults.Enabled && defaults.DaysBefore.SequenceEqual([7, 1]),
            "A save without reminder settings must default to on, 7 and 1 days before.");
        check(SavedEventApis.TryNormalizeReminder(new SavedEventReminderDto(true, [0, 7, 7]), out var normalized)
              && normalized.Enabled && normalized.DaysBefore.SequenceEqual([7, 0]),
            "Reminder days must be de-duplicated and ordered from the earliest step.");
        check(!SavedEventApis.TryNormalizeReminder(new SavedEventReminderDto(true, [3]), out _),
            "Reminder steps other than 7, 1 and 0 days must be rejected.");
        check(SavedEventApis.TryNormalizeReminder(new SavedEventReminderDto(true, []), out var empty) && !empty.Enabled,
            "Turning reminders on without any step must be stored as off (no reminder could ever fire).");
        check(SavedEventApis.ParseDays("7,banana,3,0,1").SequenceEqual([7, 1, 0]),
            "Stored reminder days must ignore unknown or disallowed values.");
    }

    private static SavedEvent Saved(string? start, string end, string days = "7,1,0", bool enabled = true)
        => new()
        {
            UserId = UserId,
            EventId = "schedule:winter",
            Source = "schedule",
            SourceId = "winter",
            Kind = "academic",
            Title = "겨울 계절학기 수강신청",
            StartDate = start is null ? null : NotificationApis.ToUtcDateTime(DateOnly.Parse(start)),
            EndDate = NotificationApis.ToUtcDateTime(DateOnly.Parse(end)),
            Url = "https://www.dongguk.edu/schedule:winter",
            RemindEnabled = enabled,
            RemindDaysBefore = days,
        };

    private static List<UserNotification> Due(SavedEvent saved, string today)
        => SavedEventApis.BuildDueNotifications(saved, DateOnly.Parse(today), DateTime.UtcNow);

    private static void CheckDueNotifications(Action<bool, string> check)
    {
        var period = Saved("2026-11-11", "2026-11-13");

        check(Due(period, "2026-11-03").Count == 0, "Nothing is due before the first (7-day) step.");

        var sevenBefore = Due(period, "2026-11-04");
        check(sevenBefore.Count == 1
              && sevenBefore[0].ReminderDaysBefore == 7
              && sevenBefore[0].TargetDate == NotificationApis.ToUtcDateTime(new DateOnly(2026, 11, 11))
              && sevenBefore[0].Body == "7일 뒤(11월 11일) 시작합니다. 겨울 계절학기 수강신청"
              && sevenBefore[0].Topic == SavedEventApis.Topic
              && sevenBefore[0].Source == "schedule" && sevenBefore[0].SourceId == "winter",
            "A period that has not started counts down to its start date.");

        var late = Due(period, "2026-11-08");
        check(late.Count == 1 && late[0].ReminderDaysBefore == 7 && late[0].Body.StartsWith("3일 뒤(11월 11일) 시작합니다", StringComparison.Ordinal),
            $"Saving after a step passed must create one notification worded with the real days left (got {string.Join(" | ", late.Select(n => n.Body))}).");

        var dayBefore = Due(period, "2026-11-10");
        check(dayBefore.Count == 1 && dayBefore[0].ReminderDaysBefore == 1 && dayBefore[0].Body.StartsWith("내일(11월 11일) 시작합니다", StringComparison.Ordinal),
            "Only the most imminent due step is created.");

        var startDay = Due(period, "2026-11-11");
        check(startDay.Count == 1 && startDay[0].ReminderDaysBefore == 0 && startDay[0].Body.StartsWith("오늘 시작합니다", StringComparison.Ordinal),
            "On the start day only the start reminder is sent (no deadline reminder the same day).");

        var running = Due(period, "2026-11-12");
        check(running.Count == 1 && running[0].ReminderDaysBefore == 1
              && running[0].TargetDate == NotificationApis.ToUtcDateTime(new DateOnly(2026, 11, 13))
              && running[0].Body.StartsWith("내일(11월 13일) 마감입니다", StringComparison.Ordinal),
            "After the start day the period counts down to its deadline.");

        check(Due(period, "2026-11-14").Count == 0, "Ended events produce no reminders.");
        check(Due(Saved("2026-11-11", "2026-11-13", enabled: false), "2026-11-10").Count == 0, "Reminders that are off produce nothing.");

        var deadline = Due(Saved(null, "2026-11-13", days: "1"), "2026-11-12");
        check(deadline.Count == 1 && deadline[0].Body == "내일(11월 13일) 마감입니다. 겨울 계절학기 수강신청",
            "A deadline-only event counts down to its end date.");
        check(Due(Saved(null, "2026-11-13", days: "1"), "2026-11-05").Count == 0,
            "Only the chosen steps fire (no 7-day reminder when only 1 day was chosen).");

        var keys = new[] { "2026-11-04", "2026-11-10", "2026-11-11", "2026-11-12" }
            .SelectMany(day => Due(period, day))
            .Select(notification => notification.DedupKey)
            .ToList();
        check(keys.Distinct(StringComparer.Ordinal).Count() == keys.Count && keys.All(key => key.Contains(":saved_event:schedule:winter:", StringComparison.Ordinal)),
            "Each step has its own dedup key scoped to the user, topic and event.");
        check(Due(period, "2026-11-04")[0].DedupKey == Due(period, "2026-11-05")[0].DedupKey,
            "Repeated syncs on later days reuse the same key until the next step is due.");
    }

    private static NotificationCandidateDto Candidate(string id, string title, string target, string? start, string? end, string? dateStatus = null)
        => new(
            id,
            id.Split(':')[0],
            id.Split(':')[1],
            null,
            title,
            "academic_schedule",
            null,
            target,
            start,
            end,
            3,
            null,
            $"https://www.dongguk.edu/{id}",
            null,
            0.9,
            "schedule",
            new Dictionary<string, string?>())
        {
            DateStatus = dateStatus is null ? null : JsonDocument.Parse(JsonSerializer.Serialize(dateStatus)).RootElement,
        };

    private static void CheckSnapshotRefresh(Action<bool, string> check)
    {
        var saved = Saved("2026-11-11", "2026-11-13");
        var unchanged = SavedEventApis.RefreshSnapshots(
            [saved],
            [Candidate("schedule:winter", "겨울 계절학기 수강신청", "2026-11-13", "2026-11-11", "2026-11-13")],
            DateTime.UtcNow);
        check(!unchanged, "A matching candidate with the same values must not mark the snapshot as changed.");

        var moved = SavedEventApis.RefreshSnapshots(
            [saved],
            [Candidate("schedule:winter", "겨울 계절학기 수강신청(변경)", "2026-11-20", "2026-11-18", "2026-11-20")],
            DateTime.UtcNow);
        check(moved && saved.Title == "겨울 계절학기 수강신청(변경)"
              && saved.EndDate == NotificationApis.ToUtcDateTime(new DateOnly(2026, 11, 20))
              && saved.StartDate == NotificationApis.ToUtcDateTime(new DateOnly(2026, 11, 18)),
            "A changed official schedule updates the saved title and dates so reminders follow it.");

        var before = saved.EndDate;
        var unverified = SavedEventApis.RefreshSnapshots(
            [saved],
            [Candidate("schedule:winter", "다른 제목", "2026-12-01", null, "2026-12-01", dateStatus: "unverified")],
            DateTime.UtcNow);
        check(!unverified && saved.EndDate == before, "An unverified candidate must not move a saved date.");
    }

    private static async Task CheckInputValidationAsync(Action<bool, string> check)
    {
        // DB에 닿기 전에 끝나는 검증. 이 컨텍스트는 실제로 연결되지 않는다.
        var options = new DbContextOptionsBuilder<ServerDbContext>()
            .UseNpgsql("Host=localhost;Database=model_only;Username=model_only;Password=model_only")
            .Options;
        await using var db = new ServerDbContext(options);
        var fetched = 0;
        Task<NotificationCandidateFetchResult> Fetch(DateOnly _, CancellationToken __)
        {
            fetched++;
            return Task.FromResult(new NotificationCandidateFetchResult([], true));
        }

        foreach (var eventId in new string?[] { null, "", "chat:1", "schedule:", "notices:has space", "schedule:" + new string('a', 181) })
        {
            var outcome = await SavedEventApis.SaveAsync(db, UserId, new SaveEventRequest(eventId, "2026-11-13", null), Fetch, NullLogger.Instance, CancellationToken.None);
            check(outcome.StatusCode == 400 && ErrorCode(outcome) == "invalid_event", $"Event id '{eventId}' must be rejected with 400 invalid_event.");
        }

        var badReminder = await SavedEventApis.SaveAsync(
            db, UserId, new SaveEventRequest("schedule:winter", "2026-11-13", new SavedEventReminderDto(true, [2])), Fetch, NullLogger.Instance, CancellationToken.None);
        check(badReminder.StatusCode == 400 && ErrorCode(badReminder) == "invalid_reminder", "A disallowed reminder step must be rejected before any lookup.");
        check(fetched == 0, "Invalid requests must not query RAG.");
    }

    private static async Task CheckSaveFlowAgainstDatabaseAsync(Action<bool, string> check, string connection)
    {
        var options = new DbContextOptionsBuilder<ServerDbContext>().UseNpgsql(connection).Options;
        await using (var migrate = new ServerDbContext(options))
        {
            await migrate.Database.MigrateAsync();
        }

        var userId = Guid.NewGuid();
        await using (var seed = new ServerDbContext(options))
        {
            var major = await seed.Majors.FirstAsync();
            var role = await seed.Roles.FirstAsync();
            seed.Users.Add(new User { Id = userId, UserId = $"saved-{userId:N}", HashPassword = "x", Username = "저장 테스트", MajorId = major.Id, RoleId = role.Id });
            await seed.SaveChangesAsync();
        }

        var today = NotificationApis.TodayKst();
        var end = today.AddDays(10);
        var endText = end.ToString("yyyy-MM-dd");
        var startText = today.AddDays(8).ToString("yyyy-MM-dd");
        var candidates = new List<NotificationCandidateDto>
        {
            Candidate("schedule:period", "기말고사 성적 이의신청", endText, startText, endText),
            Candidate("notices:unsure", "날짜가 애매한 공지", endText, null, endText, dateStatus: "unverified"),
        };
        var fetchCount = 0;
        var ragDown = false;
        Task<NotificationCandidateFetchResult> Fetch(DateOnly _, CancellationToken __)
        {
            fetchCount++;
            return Task.FromResult(new NotificationCandidateFetchResult(ragDown ? [] : candidates, !ragDown));
        }

        async Task<SavedEventOutcome> Save(string eventId, SavedEventReminderDto? reminder = null, string? endDate = null)
        {
            await using var db = new ServerDbContext(options);
            return await SavedEventApis.SaveAsync(db, userId, new SaveEventRequest(eventId, endDate ?? endText, reminder), Fetch, NullLogger.Instance, CancellationToken.None);
        }

        try
        {
            ragDown = true;
            var unavailable = await Save("schedule:period");
            check(unavailable.StatusCode == 503 && ErrorCode(unavailable) == "calendar_unavailable", "RAG failure must be 503, not a silent save.");
            ragDown = false;

            var notFound = await Save("schedule:not-visible");
            check(notFound.StatusCode == 404 && ErrorCode(notFound) == "event_not_found", "Events the user cannot see (not in their candidate scope) must not be saved.");

            var unverified = await Save("notices:unsure");
            check(unverified.StatusCode == 409 && ErrorCode(unverified) == "date_unverified", "Unverified dates must not be saved for reminders.");

            var created = await Save("schedule:period");
            var dto = created.Body as SavedEventDto;
            check(created.StatusCode == 201 && dto is not null
                  && dto.Title == "기말고사 성적 이의신청" && dto.StartDate == startText && dto.EndDate == endText
                  && dto.Kind == "academic" && dto.Reminder.Enabled && dto.Reminder.DaysBefore.SequenceEqual([7, 1]),
                "Saving stores the official title, period and kind from the candidate with default reminders.");

            var fetchesBefore = fetchCount;
            var updated = await Save("schedule:period", new SavedEventReminderDto(true, [0]));
            check(updated.StatusCode == 200 && (updated.Body as SavedEventDto)?.Reminder.DaysBefore.SequenceEqual([0]) == true && fetchCount == fetchesBefore,
                "Saving again only updates the reminder settings and does not query RAG.");

            await using (var reader = new ServerDbContext(options))
            {
                var rows = await reader.SavedEvents.Where(e => e.UserId == userId).ToListAsync();
                check(rows.Count == 1 && rows[0].SourceId == "period" && rows[0].RemindDaysBefore == "0",
                    "Exactly one row per user and event is stored, with the candidate's source id.");

                var due = SavedEventApis.BuildDueNotifications(rows[0], today.AddDays(8), DateTime.UtcNow);
                reader.UserNotifications.AddRange(due);
                await reader.SaveChangesAsync();
                var stored = await reader.UserNotifications.Where(n => n.UserId == userId).ToListAsync();
                check(stored.Count == 1 && stored[0].Topic == SavedEventApis.Topic && stored[0].Body.StartsWith("오늘 시작합니다", StringComparison.Ordinal),
                    "Due saved-event reminders persist as in-app notifications.");
            }

            await using (var deleter = new ServerDbContext(options))
            {
                await deleter.SavedEvents.Where(e => e.UserId == userId && e.EventId == "schedule:period").ExecuteDeleteAsync();
                check(!await deleter.SavedEvents.AnyAsync(e => e.UserId == userId), "Deleting removes the saved event.");
            }
        }
        finally
        {
            await using var cleanup = new ServerDbContext(options);
            await cleanup.UserNotifications.Where(n => n.UserId == userId).ExecuteDeleteAsync();
            await cleanup.SavedEvents.Where(e => e.UserId == userId).ExecuteDeleteAsync();
            await cleanup.Users.Where(u => u.Id == userId).ExecuteDeleteAsync();
        }
    }

    private static string? ErrorCode(SavedEventOutcome outcome) => (outcome.Body as CalendarErrorResponse)?.Error;
}
